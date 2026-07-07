#!/usr/bin/python3
"""
Placement automatique des oraux de second groupe
Olivier Boesch (c) 2023
"""
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from csv import DictReader
from datetime import timedelta, datetime, time, date
from math import ceil, inf
from os.path import join
from random import shuffle
from typing import Union
from multiprocessing import Pool
from multiprocessing import Lock as _mp_Lock

import colorama

from db_facility_save import DbFacility

from webserver.app_secrets import generate_password, hash_password, CENTRE_EXAMEN
from webserver.reports import (
    liste_papillons_connexion,
    liste_papillons_candidats,
    liste_papillons_loges,
)

colorama.init(autoreset=True)

# Lorsque ce fichier est lancé directement (`python algo.py`), il est
# enregistré dans sys.modules sous la clé "__main__", pas "algo". Si un
# module tiers (ex. algo_cp.py) fait ensuite `from algo import ...`, Python
# ne trouve pas de module "algo" déjà chargé et réexécute donc CE FICHIER
# une seconde fois, dans un espace de noms complètement séparé — avec pour
# conséquence que son bloc `if __name__ == '__main__':` (qui définit `_Path`,
# utilisé par AlgoOne.save()) ne s'exécute jamais dans cette seconde copie.
# On force ici l'alias pour que tout `import algo` ultérieur réutilise ce
# module déjà chargé, qu'il ait été lancé en tant que script ou importé
# normalement.
sys.modules.setdefault("algo", sys.modules[__name__])

# paramètres de run — surchargés par variables d'environnement si présentes
import os as _os

def _env_int(key, default):
    try:
        return int(_os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _env_time(key, default_h, default_m):
    raw = _os.environ.get(key, "")
    try:
        h, m = raw.split(":")
        return time(hour=int(h), minute=int(m))
    except Exception:
        return time(hour=default_h, minute=default_m)

def _env_bool(key, default=False):
    return _os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes", "on")

def _env_time_optional(key):
    """Comme _env_time, mais retourne None (fonctionnalité désactivée) si la
    variable est absente ou vide, plutôt qu'une heure par défaut arbitraire."""
    raw = _os.environ.get(key, "").strip()
    if not raw:
        return None
    try:
        h, m = raw.split(":")
        return time(hour=int(h), minute=int(m))
    except Exception:
        return None

N_run               = _env_int("ALGO_N_RUN",    1_000)
ECART_MINI_CANDIDAT = timedelta(minutes=_env_int("ALGO_ECART_MINI", 80))
HEURE_DEBUT         = _env_time("ALGO_HEURE_DEBUT", 8, 10)
CRENEAUX            = _env_int("ALGO_CRENEAUX", 13)
DEBUG_DISPLAY       = _env_bool("ALGO_DEBUG", False)
ALGO_ENGINE         = _os.environ.get("ALGO_ENGINE", "monte_carlo").strip().lower()
# Petites matières repoussées en fin de journée (cf. AlgoOne._reserver_petites_matieres) :
# comportement appliqué par défaut en production (__main__), mais l'API reste opt-in
# (AlgoOne.__init__ défaut à False) pour ne pas changer le comportement des appelants
# existants (tests, scripts) qui ne demandent pas explicitement cette optimisation.
PETITES_MATIERES_FIN_JOURNEE = _env_bool("ALGO_PETITES_MATIERES_FIN_JOURNEE", True)
SEUIL_PETITE_MATIERE         = _env_int("ALGO_SEUIL_PETITE_MATIERE", 5)
MARGE_PETITE_MATIERE         = _env_int("ALGO_MARGE_PETITE_MATIERE", 2)
# Pause méridienne (aucune par défaut — None désactive la fonctionnalité) :
# heure de début et durée réglables depuis /gestion/algo.
PAUSE_MERIDIENNE_DEBUT = _env_time_optional("ALGO_PAUSE_MERIDIENNE_DEBUT")
PAUSE_MERIDIENNE_DUREE = timedelta(minutes=_env_int("ALGO_PAUSE_MERIDIENNE_DUREE", 0))
# Objectif souple (jamais bloquant) d'heure de fin du dernier oral — cf. AlgoOne.__init__.
HEURE_FIN_JOURNEE_CIBLE = _env_time_optional("ALGO_HEURE_FIN_JOURNEE_CIBLE")

# données
DATA_DIR = 'data'
ELVS_FILE = join(DATA_DIR, "candidats.csv")
PROFS_FILE = join(DATA_DIR, "examinateurs.csv")
PREPS_FILE = join(DATA_DIR, 'preps.csv')
OK_CHAR = "\U00002714"  # ✔
NOK_CHAR = "\U00002718"  # ✘
WARNING_CHAR = "\U0001F534"  # 🔴


class AlgoError(RuntimeError):
    """Erreur algo avec contexte de diagnostic."""
    pass


class PasDeCreneauDisponible(AlgoError):
    """Aucun créneau disponible pour placer un candidat."""
    def __init__(self, candidat, n_examinateurs):
        self.candidat = candidat
        self.n_examinateurs = n_examinateurs
        super().__init__(
            f"Candidat {candidat.numero} ({candidat.nom.strip()}) — "
            f"aucun créneau disponible ({n_examinateurs} examinateur(s))"
        )


class CustomFormatter(logging.Formatter):
    """Format du log pour la console"""

    blue = colorama.Fore.BLUE
    yellow = colorama.Fore.YELLOW
    red = colorama.Fore.RED
    green = colorama.Fore.GREEN
    violet = colorama.Fore.MAGENTA
    format = "%(relativeCreated)-0.1f ms [%(levelname)-8s] %(message)s"
    format_det = "%(relativeCreated)-0.1f ms [%(levelname)-8s] %(message)s (%(module)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: blue + format_det,
        logging.INFO: green + format,
        logging.WARNING: yellow + format_det,
        logging.ERROR: red + format_det,
        logging.CRITICAL: violet + format_det
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


# objets log
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Verrou inter-processus : les runs sont exécutés en parallèle via
# multiprocessing.Pool et héritent (fork) des mêmes handlers/descripteurs de
# sortie standard, donc du même pipe lu côté serveur web (SSE). Sans
# sérialisation, deux processus peuvent entrelacer leurs écritures au milieu
# d'un caractère UTF-8 multi-octets (✔, ✘...), corrompant ce flux.
#
# Ce verrou ne protège QUE `ch` (la console, seule piped vers le serveur
# web) — surtout pas `fh` (fichier local `data/log.txt`, jamais lu par le
# serveur). fh est toujours au niveau DEBUG (indépendamment de ALGO_DEBUG),
# donc c'est lui qui reçoit l'immense majorité du volume de logs (plusieurs
# centaines de milliers d'appels log.debug() sur un batch N_run=1000, dans
# les boucles per-candidat). Un verrou dessus re-sérialiserait une grande
# partie d'un calcul censé être parallèle pour un bénéfice quasi nul (un
# fichier de diagnostic local dont l'éventuel entrelacement de lignes n'a
# aucun impact fonctionnel). `ch`, lui, ne reçoit que l'INFO par défaut
# (~2 lignes/run), donc le coût du verrou y est négligeable.
#
# (Une première tentative avait remplacé ce verrou par un
# QueueHandler/QueueListener pour éliminer toute contention — mais avec ce
# volume de logs par candidat, le coût de pickling de chaque LogRecord plus
# la consommation mono-thread du listener se sont révélés bien pires que le
# verrou lui-même : préférer ce correctif ciblé, plus simple et plus rapide.)
_LOG_LOCK = _mp_Lock()

class _LockedEmitMixin:
    """Sérialise emit() entre processus via _LOG_LOCK (cf. commentaire ci-dessus)."""
    def emit(self, record):
        with _LOG_LOCK:
            super().emit(record)

class LockedStreamHandler(_LockedEmitMixin, logging.StreamHandler):
    pass

ch = LockedStreamHandler()
ch.setLevel(logging.DEBUG if DEBUG_DISPLAY else logging.INFO)
ch.setFormatter(CustomFormatter())
fh = logging.FileHandler(join(DATA_DIR, "log.txt"), mode='w')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(relativeCreated)-12.3f [%(levelname)-8s] %(message)s (%(filename)s:%(lineno)d)"))
log.addHandler(ch)
log.addHandler(fh)


def charger_fichier_comme_liste(filename: str) -> list:
    """Charge un CSV comme liste de dicts.
    Tolère BOM UTF-8, encodage latin-1, séparateur ',' ou ';', espaces parasites.
    """
    from pathlib import Path as _Path
    import sys as _sys
    _root = str(_Path(__file__).resolve().parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    try:
        from webserver.csv_validator import normalize_csv_file
        rows, _ = normalize_csv_file(filename)
        return rows
    except Exception:
        # Fallback original si le module est indisponible
        with open(filename, "r", encoding="utf-8-sig") as f:
            data = DictReader(f, delimiter=";")
            return [r for r in data]


# Cache par processus worker : `multiprocessing.Pool` ne crée que
# `cpu_count()` processus, qui traitent chacun plusieurs runs Monte-Carlo
# séquentiellement (jusqu'à N_run=1000) — sans ce cache, `setup_from_files()`
# relit et re-parse les 3 CSV depuis le disque à *chaque* run, alors que leur
# contenu ne change jamais pendant tout le batch. Peuplé une seule fois par
# worker via `Pool(initializer=...)`, jamais par instance `AlgoOne` : reste
# donc `None` (et sans effet) en dehors de ce contexte précis — CP-SAT (une
# seule résolution, pas de `Pool`), tests, et usage direct en script
# continuent de lire les fichiers normalement, comportement inchangé.
_cache_donnees_worker: dict[str, list[dict]] | None = None


def _initialiser_cache_worker(filename_candidats: str, filename_examinateurs: str,
                              filename_matieres: str) -> None:
    """Initializer de `multiprocessing.Pool` (Monte-Carlo) : parse les 3 CSV
    une fois par processus worker plutôt qu'une fois par run — cf.
    commentaire de `_cache_donnees_worker`. Les dicts obtenus ne sont jamais
    mutés par `setup_from_files()` (seulement lus), donc partageables sans
    risque entre tous les runs traités par ce worker."""
    global _cache_donnees_worker
    _cache_donnees_worker = {
        'candidats': charger_fichier_comme_liste(filename_candidats),
        'examinateurs': charger_fichier_comme_liste(filename_examinateurs),
        'matieres': charger_fichier_comme_liste(filename_matieres),
    }


def chercher_par_nom(liste: list[Union["Candidat", "Examinateur", "Matiere"]], nom: str) -> Union["Candidat", "Examinateur", "Matiere"]:
    """Cherche dans une liste d'objets par l'attribut nom.
    Pour les Matiere, recherche aussi par nom_court (insensible à la casse)
    afin que les CSV puissent utiliser les noms courts ou complets.
    """
    nom_norm = nom.strip().lower()
    for item in liste:
        if item.nom.strip().lower() == nom_norm:
            return item
        # Matiere : essai sur le nom court également
        if hasattr(item, 'nom_court') and item.nom_court.strip().lower() == nom_norm:
            return item
    return None


def chercher_par_matiere(liste: list[Union["Oral", "Examinateur"]], matiere: str) -> Union["Oral", "Examinateur"]:
    """cherche dans une liste d'objets Oral ou Examinateur par l'attribut matiere"""
    for item in liste:
        if item.matiere == matiere:
            return item


def parser_heure_mini(valeur: str) -> time:
    """Parse la colonne 'Heure mini' de examinateurs.csv.

    Accepte une heure entière ('9') pour compatibilité avec les fichiers
    existants, ou une heure:minute ('9:30') — toujours en 24h.
    """
    valeur = valeur.strip()
    if ':' in valeur:
        h, m = valeur.split(':', 1)
        return time(hour=int(h), minute=int(m))
    return time(hour=int(valeur))


class CreneauInterdit:
    """
        Définit si un créneau est interdit pour placer un oral
    """
    pass


class Candidat:
    """Candidat au second groupe"""

    def __init__(self, nom: str, numero: str, choix1: "Matiere", choix2: "Matiere", tiers_temps: bool,
                 profs_a_eviter="", etablissement=""):
        """
        :param nom: nom du candidat
        :type nom: str
        :param numero: numéro du candidat
        :type numero: str
        :param choix1: choix de matiere 1
        :type choix1: Matiere
        :param choix2: choix de matiere 2
        :type choix2: Matiere
        :param tiers_temps: le candidat a un tiers temps
        :type tiers_temps: bool
        """
        self.nom: str = nom.strip()
        self.numero: str = numero.strip()
        self.choix1: "Matiere" = choix1
        self.choix2: "Matiere" = choix2
        self.tiers_temps: bool = tiers_temps
        self.idx = None
        self.profs_a_eviter = profs_a_eviter.split(',')
        self.etablissement = etablissement
        self.login_key: str = generate_password()
        self.oraux: list["Oral"] = []

    def to_dict(self) -> dict:
        """
        Convertit l'objet en dictionnaire.

        :return: Dictionnaire représentant l'objet
        :rtype: dict
        """
        return {
            'id': self.idx,
            'nom': self.nom,
            'numero': self.numero,
            'choix1': self.choix1.idx,
            'choix2': self.choix2.idx,
            'tiers_temps': 1 if self.tiers_temps else 0,
            'etablissement': self.etablissement,
            'login_key': self.login_key,
            'password_hash': hash_password(self.login_key, self.numero),
        }

    @property
    def infos_connexion(self) -> dict:
        """Renvoie les informations de connexion du candidat pour les papillons."""
        return {'nom': self.nom, 'numero': self.numero, 'login_key': self.login_key}

    def __repr__(self) -> str:
        """représentation en chaine"""
        s_tiers_temps = " ( " + WARNING_CHAR + "tiers temps )" if self.tiers_temps else ""
        choix1_ok = OK_CHAR if chercher_par_matiere(self.oraux, self.choix1.nom) else NOK_CHAR
        choix2_ok = OK_CHAR if chercher_par_matiere(self.oraux, self.choix2.nom) else NOK_CHAR
        return f"{self.nom} ({self.numero} - {self.etablissement}): {self.choix1.nom} {choix1_ok} / {self.choix2.nom} {choix2_ok}{s_tiers_temps}"


class Examinateur:
    """Examinateur du second groupe"""

    def __init__(self, nom: str, matiere: "Matiere", salle: str, loge: str, max_creneaux_journee: int,
                 heure_debut: time, etablissements: str=""):
        """
        :param nom: nom de l'examinateur
        :type nom: str
        :param matiere: matiere de l'examinateur
        :type matiere: Matiere
        :param salle: salle de l'examinateur
        :type salle: str
        :param loge: loge de l'examinateur
        :type loge: str
        :param max_creneaux_journee: nombre maximum de créneaux dans la journée
        :type max_creneaux_journee: int
        :param heure_debut: heure de début des oraux
        :type heure_debut: time
        :param etablissements: liste des établissements assignés à l'examinateur (séparés par des virgules)
        :type etablissements: str
        """
        self.nom: str = nom
        self.matiere: Matiere = matiere
        """self._oraux : liste des oraux pour l'examinateur"""
        self.oraux: list[Union["Oral", None, CreneauInterdit]] = [None] * max_creneaux_journee
        self.salle: str = salle
        self.loge = loge
        self.heure_debut: time = heure_debut
        self.idx = None
        self.etablissements = etablissements.split(',')
        self.mot_de_passe = generate_password()
        log.debug(f"Creation examinateur: {str(self)}")

    @property
    def infos_connexion(self) -> tuple:
        """
        Renvoie les informations de connexion de l'examinateur

        :return: Tuple contenant la salle, le nom et le mot de passe
        :rtype: tuple
        """
        return self.salle, self.nom, self.mot_de_passe

    def to_dict(self) -> dict:
        """
        Convertit l'objet en dictionnaire
        
        :return: Dictionnaire représentant l'objet
        :rtype: dict
        """
        return {
            'id': self.idx,
            'nom': self.nom,
            'matiere': self.matiere.idx,
            'salle': self.salle,
            'loge': self.loge,
            'etablissements': ','.join(self.etablissements),
            'password_hash': hash_password(self.mot_de_passe, self.salle),
        }

    def recherche_creneau(self, creneau_reference: int | None, ecart_mini: int, candidat: Candidat) -> int | None:
        """
        Recherche un créneau disponible pour l'examinateur en tenant compte des contraintes.
        :param creneau_reference: Créneau de référence pour le candidat (None si aucun oral n'a été planifié)
        :type creneau_reference: int | None
        :param ecart_mini: Ecart minimum entre les oraux en créneaux en créneaux
        :type ecart_mini: int
        :param candidat: Candidat pour lequel on cherche un créneau
        :type candidat: Candidat
        :return: Numéro du créneau disponible ou None si aucun créneau n'est disponible
        :rtype: int | None
        """
        # pas de reference -> premier disponible
        if self.etablissements != [''] and candidat.etablissement in self.etablissements:
            return None
        if candidat.profs_a_eviter != [''] and self.nom in candidat.profs_a_eviter:
            return None
        if creneau_reference is None:
            for i in range(len(self.oraux)):
                if self.oraux[i] is None and type(self.oraux[i]) is not CreneauInterdit:
                    return i
        else:
            # recherche avant
            for i in range(0, creneau_reference - ecart_mini + 1):
                if self.oraux[i] is None and type(self.oraux[i]) is not CreneauInterdit:
                    return i
            # recherche après
            for i in range(creneau_reference + ecart_mini, len(self.oraux)):
                if self.oraux[i] is None and type(self.oraux[i]) is not CreneauInterdit:
                    return i
        return None  # pas de creneau trouvé

    def __repr__(self) -> str:
        """Représentation en chaine"""
        return f"{self.nom} ({self.etablissements}) ({self.matiere.nom} / {self.salle})"


class Matiere:
    def __init__(self, nom: str, nom_court: str, temps_preparation: int, temps_oral: int):
        """
        Docstring for __init__
        
        :param nom: Nom de la matière
        :type nom: str
        :param nom_court: Nom court de la matière
        :type nom_court: str
        :param temps_preparation: Temps de préparation en minutes
        :type temps_preparation: int
        :param temps_oral: Temps de l'oral en minutes
        :type temps_oral: int
        """
        self.nom: str = nom
        self.nom_court: str = nom_court
        self.temps_preparation: timedelta = timedelta(minutes=int(temps_preparation))
        self.temps_oral: timedelta = timedelta(minutes=int(temps_oral))
        self.examinateurs: list["Examinateur"] = []
        self.candidats: list["Candidat"] = []
        self.idx = None
        log.debug(f"Creation matiere: {str(self)}")

    def to_dict(self) -> dict:
        """
        Convertit l'objet en dictionnaire

        :return: Dictionnaire représentant l'objet
        :rtype: dict
        """
        return {
            'id': self.idx,
            'nom': self.nom,
            'nom_court': self.nom_court
        }

    def __repr__(self) -> str:
        """Représentation en chaine"""
        return f"{self.nom_court}: {self.temps_preparation} / {self.temps_oral}"

    def __eq__(self, other: Union["Matiere", str]) -> bool:
        """
        Compare deux matières par objet/nom ou par objet/objet
        
        :param self: Matiere actuelle
        :param other: Matiere à comparer (nom ou objet)
        :type other: "Matiere" | str
        :return: True si les matières sont les mêmes, False sinon
        :rtype: bool
        """
        if isinstance(other, Matiere):
            return self.nom == other.nom
        elif isinstance(other, str):
            other_norm = other.strip().lower()
            return (self.nom.strip().lower() == other_norm
                    or self.nom_court.strip().lower() == other_norm)
        raise NotImplementedError("Can only be str or Matiere Objects")


class Oral:
    """Oral du second groupe"""
    def __init__(self, examinateur: "Examinateur", matiere: "Matiere", candidat: "Candidat", creneau: int = 0):
        """
        :param examinateur: Objet Examinateur
        :type examinateur: "Examinateur"
        :param matiere: Objet Matiere
        :type matiere: "Matiere"
        :param candidat: Objet Candidat
        :type candidat: "Candidat"
        :param creneau: Numéro du créneau de l'oral
        :type creneau: int
        """
        self.examinateur: "Examinateur" = examinateur
        self.matiere: "Matiere" = matiere
        self.candidat: "Candidat" = candidat
        self.creneau: int = creneau
        self.heure_sujet: time | None = None
        self.heure_oral: time | None = None
        self.heure_fin: time | None = None
        self.idx = None

    def to_dict(self):
        """
        Convertit l'objet en dictionnaire

        :return: Dictionnaire représentant l'objet
        :rtype: dict
        """
        return {
            'id': self.idx,
            'examinateur': self.examinateur.idx,
            'candidat': self.candidat.idx,
            'heure_sujet': self.heure_sujet.strftime("%H:%M"),
            'heure_oral': self.heure_oral.strftime("%H:%M"),
            'heure_fin': self.heure_fin.strftime("%H:%M")
        }

    def __repr__(self) -> str:
        """Représentation en chaine"""
        if self.heure_sujet is not None:
            s_heure = f"s:{self.heure_sujet} / o:{self.heure_oral} / f:{self.heure_fin}"
        else:
            s_heure = f"creneau {self.creneau}"
        return f"c:{self.candidat!s} | e:{self.examinateur!s} | m:{self.matiere!s} @ {s_heure}"


class AlgoOne:
    """Classe principale de l'algorithme d'appairage des oraux"""
    def __init__(self, filename_candidats: str = '', filename_examinateurs: str = '', filename_matieres: str = '',
                 heure_debut: time = time(hour=8, minute=00),
                 temps_minimum_entre_oraux: timedelta = timedelta(hours=1), interrompre_oral: bool = False,
                 max_creneaux_journee: int = 15, temps_pause: timedelta = timedelta(minutes=20),
                 intervalle_pause: int = 4, traiter_matiere_principales_en_premier: bool = True, numero_run: int = 0,
                 optimiser_petites_matieres: bool = False, seuil_petite_matiere: int = 5,
                 marge_flexibilite_petite_matiere: int = 2,
                 heure_pause_meridienne: time | None = None,
                 duree_pause_meridienne: timedelta = timedelta(minutes=0),
                 heure_fin_journee_cible: time | None = None):
        """
        :param filename_candidats: nom du fichier des candidats
        :type filename_candidats: str
        :param filename_examinateurs: nom du fichier des examinateurs
        :type filename_examinateurs: str
        :param filename_matieres: nom du fichier des matières
        :type filename_matieres: str
        :param heure_debut: heure de début des oraux
        :type heure_debut: time
        :param temps_minimum_entre_oraux: temps minimum entre les oraux d'un même candidat
        :type temps_minimum_entre_oraux: timedelta
        :param interrompre_oral: autoriser l'interruption des oraux pour la préparation du suivant
        :type interrompre_oral: bool
        :param max_creneaux_journee: nombre maximum de créneaux dans la journée
        :type max_creneaux_journee: int
        :param temps_pause: temps de pause après un certain nombre d'oraux
        :type temps_pause: timedelta
        :param intervalle_pause: nombre d'oraux avant la pause
        :type intervalle_pause: int
        :param traiter_matiere_principales_en_premier: traiter les matières principales en premier (les plus demandées)
        :type traiter_matiere_principales_en_premier: bool
        :param numero_run: numéro du run (pour les logs)
        :type numero_run: int
        :param optimiser_petites_matieres: repousser les matières peu demandées vers la fin de
            journée (cf. _reserver_petites_matieres) ; désactivé par défaut (opt-in) pour ne pas
            changer le comportement des appelants existants — activé explicitement par __main__.
        :type optimiser_petites_matieres: bool
        :param seuil_petite_matiere: nombre de candidats (oraux) en-dessous duquel une matière
            est considérée "petite" et voit ses premiers créneaux réservés
        :type seuil_petite_matiere: int
        :param marge_flexibilite_petite_matiere: nombre de créneaux de marge laissés ouverts
            en plus du strict nécessaire, pour ne pas sur-contraindre l'écart minimum candidat
        :type marge_flexibilite_petite_matiere: int
        :param heure_pause_meridienne: heure à partir de laquelle aucun oral ne doit être en
            cours pour un examinateur (None désactive la pause méridienne) ; cf. calcul_horaires
        :type heure_pause_meridienne: time | None
        :param duree_pause_meridienne: durée de la pause méridienne
        :type duree_pause_meridienne: timedelta
        :param heure_fin_journee_cible: heure à laquelle on souhaite que le dernier oral de la
            journée soit terminé (None désactive la fonctionnalité). Objectif souple, jamais
            bloquant : Monte-Carlo (cf. selectionner_meilleur_algo) préfère, parmi les runs déjà
            conformes à l'écart minimum candidat, celui qui finit le plus tôt ; CP-SAT (cf.
            AlgoCP.resoudre) pénalise dans sa fonction objectif (poids ALGO_POIDS_HEURE_FIN_JOURNEE)
            les créneaux utilisés au-delà d'un index approximatif de cette heure, sans jamais
            rendre le modèle infaisable à cause de ce seul réglage.
        :type heure_fin_journee_cible: time | None
        """
        self.filename_candidats: str = filename_candidats
        self.filename_examinateurs: str = filename_examinateurs
        self.filename_matieres: str = filename_matieres
        self.liste_candidats: list["Candidat"] = []
        self.liste_examinateurs: list["Examinateur"] = []
        self.liste_matieres: list["Matiere"] = []
        self.liste_oraux: list["Oral"] = []
        self.temps_minimum_entre_oraux = temps_minimum_entre_oraux
        self.creneaux_minimum_entre_oraux = ceil(self.temps_minimum_entre_oraux.total_seconds() / 60.0 / 20. + 1)
        self.interrompre_oral = interrompre_oral
        self.max_creneaux_journee = max_creneaux_journee
        self.heure_debut = heure_debut
        self.temps_pause = temps_pause
        self.intervalle_pause = intervalle_pause
        self.traiter_matiere_principales_en_premier = traiter_matiere_principales_en_premier
        self.numero_run = numero_run
        self.optimiser_petites_matieres = optimiser_petites_matieres
        self.seuil_petite_matiere = seuil_petite_matiere
        self.marge_flexibilite_petite_matiere = marge_flexibilite_petite_matiere
        self.heure_pause_meridienne = heure_pause_meridienne
        self.duree_pause_meridienne = duree_pause_meridienne
        self.heure_fin_journee_cible = heure_fin_journee_cible

    def setup_from_files(self) -> None:
        """Charge les données depuis les fichiers et crée les objets"""
        # chargement des données — réutilise le cache worker s'il est déjà
        # peuplé (cf. _cache_donnees_worker), sinon lit les fichiers
        # directement (CP-SAT, tests, usage hors Pool : comportement inchangé)
        if _cache_donnees_worker is not None:
            liste_donnees_candidats: list[dict] = _cache_donnees_worker['candidats']
            liste_donnees_matieres: list[dict] = _cache_donnees_worker['matieres']
            liste_donnees_examinateurs: list[dict] = _cache_donnees_worker['examinateurs']
        else:
            liste_donnees_candidats = charger_fichier_comme_liste(self.filename_candidats)
            liste_donnees_matieres = charger_fichier_comme_liste(self.filename_matieres)
            liste_donnees_examinateurs = charger_fichier_comme_liste(self.filename_examinateurs)
        log.debug(f"Run {self.numero_run} : Données chargées")
        # liste des matières
        log.debug(f"Run {self.numero_run} : Création matières")
        self.liste_matieres = [
            Matiere(nom=c["Matiere"], nom_court=c['Matière court'], temps_preparation=c["Temps preparation (min)"], temps_oral=c["Duree (min)"]) for c
            in liste_donnees_matieres]
        log.debug(f"Run {self.numero_run} : Liste des matières créée ({len(self.liste_matieres)} matières crées)")
        log.debug(f"Run {self.numero_run} : Liste des matières: {self.liste_matieres!s}")
        # liste des examinateurs
        log.debug(f"Run {self.numero_run} : Création Examinateurs")
        for p in liste_donnees_examinateurs:
            matiere = chercher_par_nom(self.liste_matieres, p["Disc.poste"])
            if matiere is None:
                noms = [f"{m.nom} / {m.nom_court}" for m in self.liste_matieres]
                raise ValueError(
                    f"Discipline '{p['Disc.poste']}' (examinateur '{p['Nom']}') "
                    f"introuvable dans preps.csv. Disciplines disponibles : {noms}"
                )
            salle = p["Salle"]
            loge = p['Loge']
            etab = p["Etab"]
            heure_debut = parser_heure_mini(p['Heure mini'])
            t = Examinateur(nom=p["Nom"],
                            matiere=matiere,
                            max_creneaux_journee=self.max_creneaux_journee,
                            salle=salle, loge=loge, heure_debut=heure_debut, etablissements=etab)
            matiere.examinateurs.append(t)
            self.liste_examinateurs.append(t)
            nb_oraux_interdits = max(0, (datetime.combine(datetime.today(), t.heure_debut) - datetime.combine(datetime.today(), self.heure_debut)).total_seconds() // matiere.temps_oral.total_seconds())
            if nb_oraux_interdits > 0:
                log.debug(f"Run {self.numero_run} : {t.nom} -> {nb_oraux_interdits} créneaux interdits (début à {t.heure_debut})")
                for i in range(int(nb_oraux_interdits)):
                    t.oraux[i] = CreneauInterdit()
        log.debug(f"Run {self.numero_run} : Liste des examinateurs créée ({len(self.liste_examinateurs)} examinateurs créés)")
        log.debug(f"Run {self.numero_run} : Liste des examinateurs ({self.liste_examinateurs}) examinateurs créés)")
        # liste des candidats
        log.debug(f"Run {self.numero_run} : Création candidats")
        for e in liste_donnees_candidats:
            course1 = chercher_par_nom(self.liste_matieres, e["CHOIX DISCIPLINE 1"])
            course2 = chercher_par_nom(self.liste_matieres, e["CHOIX DISCIPLINE 2"])
            if course1 is None or course2 is None:
                noms = [f"{m.nom} / {m.nom_court}" for m in self.liste_matieres]
                missing = e["CHOIX DISCIPLINE 1"] if course1 is None else e["CHOIX DISCIPLINE 2"]
                raise ValueError(
                    f"Discipline '{missing}' (candidat '{e['CANDIDAT']}') "
                    f"introuvable dans preps.csv. Disciplines disponibles : {noms}"
                )
            name = e["CANDIDAT"].split("(")[0]
            number = e["CANDIDAT"].split("(")[1][:-1]
            tiers_temps = True if e["TT"] == "1" else False
            profs = e['Profs']
            etab = e['Etab']
            s = Candidat(nom=name, numero=number, choix1=course1, choix2=course2, tiers_temps=tiers_temps,
                         profs_a_eviter=profs,
                         etablissement=etab)
            course1.candidats.append(s)
            course2.candidats.append(s)
            self.liste_candidats.append(s)
        log.debug(f"Run {self.numero_run} : Liste des candidats crée ({len(self.liste_candidats)} candidats créés)")
        log.debug(f"Run {self.numero_run} : Liste des candidats: {self.liste_candidats!s}")
        self.liste_matieres.sort(key=lambda m: len(m.candidats), reverse=self.traiter_matiere_principales_en_premier)
        log.debug(f"Run {self.numero_run} : Liste des matières triée")
        log.debug(f"Run {self.numero_run} : Liste des matères: {self.liste_matieres!s}")
        if self.optimiser_petites_matieres:
            self._reserver_petites_matieres()

    def _reserver_petites_matieres(self) -> None:
        """
        Repousse les matières peu demandées vers la fin de journée, en réservant
        (marquant CreneauInterdit) les premiers créneaux de leurs examinateurs.

        Réutilise le mécanisme CreneauInterdit déjà en place pour les décalages
        d'heure_debut : les moteurs de résolution (Monte-Carlo, CP-SAT) le
        respectent déjà de façon identique, donc ce seul point d'entrée
        (partagé via AlgoOne.setup_from_files) leur profite à tous les deux
        sans aucune duplication de logique.

        Une matière est jugée "petite" quand son nombre de candidats est sous
        seuil_petite_matiere (nombre absolu d'oraux, pas un ratio). Le nombre
        de créneaux à garder ouverts par examinateur est calculé à partir du
        nombre réel de candidats de CETTE matière (+ une marge de flexibilité),
        donc deux petites matières de tailles différentes obtiennent
        naturellement des fenêtres de fin de journée différentes, sans besoin
        d'un ordre de traitement particulier (leurs examinateurs sont de toute
        façon disjoints d'une matière à l'autre).
        """
        for matiere in self.liste_matieres:
            if not matiere.examinateurs:
                continue
            if len(matiere.candidats) >= self.seuil_petite_matiere:
                continue
            n_a_garder = max(
                1,
                ceil(len(matiere.candidats) / len(matiere.examinateurs))
                + self.marge_flexibilite_petite_matiere,
            )
            for examinateur in matiere.examinateurs:
                creneaux_disponibles = [
                    i for i, o in enumerate(examinateur.oraux)
                    if not isinstance(o, CreneauInterdit)
                ]
                n_a_reserver = max(0, len(creneaux_disponibles) - n_a_garder)
                for idx in creneaux_disponibles[:n_a_reserver]:
                    examinateur.oraux[idx] = CreneauInterdit()
            log.debug(
                f"Run {self.numero_run} : matière '{matiere.nom}' jugée petite "
                f"({len(matiere.candidats)} candidat(s) < seuil {self.seuil_petite_matiere}) — "
                f"créneaux réservés en fin de journée "
                f"({n_a_garder} créneau(x) gardé(s) par examinateur)"
            )

    def save(self) -> tuple[list[tuple], list[dict], list[tuple]]:
        """
        Sauvegarde toutes les données en base et génère les informations de connexion.

        Stratégie d'initialisation des identifiants :
        - **Premier run** : credentials.enc absent → nouveaux identifiants générés pour
          tous les candidats, examinateurs et loges.
        - **Runs suivants** : credentials.enc présent → identifiants lus depuis le store
          chiffré et réutilisés à l'identique. Seul le planning des oraux change.
          Les entités absentes du store (ajout post-premier-run) reçoivent de nouveaux
          identifiants.

        :return: Tuple (connexions examinateurs, connexions candidats, connexions loges)
        :rtype: tuple[list[tuple], list[dict], list[tuple]]
        """
        from webserver.credential_store import load_credentials as _load_creds
        from webserver.app_secrets import APP_SECRET_KEY as _secret_key

        _enc_file = _Path(__file__).resolve().parent / 'data' / 'credentials.enc'
        is_first_run = not _enc_file.exists()

        if is_first_run:
            log.info("Premier run — génération de nouveaux identifiants.")
            existing_store: dict = {"candidats": {}, "examinateurs": {}, "loges": {}}
        else:
            log.info("Run suivant — réutilisation des identifiants depuis credentials.enc.")
            existing_store = _load_creds(_enc_file, _secret_key)
            # Remplacer les passwords générés en __init__ par les valeurs existantes
            existing_candidats: dict[str, str] = existing_store.get("candidats", {})
            existing_exam_pw:   dict[str, str] = existing_store.get("examinateurs", {})
            for c in self.liste_candidats:
                if c.numero in existing_candidats:
                    c.login_key = existing_candidats[c.numero]
            for e in self.liste_examinateurs:
                if e.salle in existing_exam_pw:
                    e.mot_de_passe = existing_exam_pw[e.salle]

        # ── Recréer les tables et insérer toutes les données ───────────────────
        db = DbFacility()
        db.save_all(self)

        # ── Examinateurs : (salle, nom, mot_de_passe) ──────────────────────────
        liste_exams = sorted(
            [e.infos_connexion for e in self.liste_examinateurs],
            key=lambda m: m[0],
        )

        # ── Candidats : {'nom', 'numero', 'login_key'} ─────────────────────────
        liste_candidats = sorted(
            [c.infos_connexion for c in self.liste_candidats],
            key=lambda d: d['nom'],
        )

        # ── Loges : un mot de passe unique par loge ────────────────────────────
        existing_loge_pw: dict[str, str] = existing_store.get("loges", {})
        loges_mdp: dict[str, str] = {}
        for examinateur in self.liste_examinateurs:
            loge = examinateur.loge
            if loge not in loges_mdp:
                loges_mdp[loge] = (
                    existing_loge_pw[loge]
                    if loge in existing_loge_pw
                    else generate_password()
                )
        # hash_password() (scrypt) relâche le GIL — un pool de threads parallélise
        # ces appels indépendants (cf. commentaire équivalent dans save_all()).
        with ThreadPoolExecutor() as pool:
            loges_hashes = dict(pool.map(
                lambda kv: (kv[0], hash_password(kv[1], kv[0])),
                loges_mdp.items(),
            ))
        db.save_loges(loges_hashes)
        liste_loges = sorted(
            [(nom, mdp) for nom, mdp in loges_mdp.items()],
            key=lambda t: t[0],
        )

        return liste_exams, liste_candidats, liste_loges

    def creer_oral(self, candidat: Candidat, examinateur: Examinateur, matiere: Matiere, creneau: int) -> None:
        """Crée un oral et l'assigne au candidat et à l'examinateur"""
        oral = Oral(examinateur, matiere, candidat, creneau)
        candidat.oraux.append(oral)
        examinateur.oraux[creneau] = oral
        self.liste_oraux.append(oral)
        log.debug(f"Run {self.numero_run} : Creation oral: {oral}")

    def recherche_creneau(self, creneau_reference: int | None, liste_examinateur: list[Examinateur],
                          candidat: Candidat) -> tuple[int, Examinateur]:
        """
        Recherche un créneau disponible pour un candidat parmi les examinateurs disponibles.

        :param creneau_reference: Créneau de référence pour le candidat (None si aucun oral n'a été planifié)
        :type creneau_reference: int | None
        :param liste_examinateur: Liste des examinateurs disponibles
        :type liste_examinateur: list[Examinateur]
        :param candidat: Candidat pour lequel on cherche un créneau
        :type candidat: Candidat
        :return: Tuple contenant le numéro du créneau disponible et l'examinateur choisi
        :rtype: tuple[int, Examinateur]

        .. note::
            Priorité à l'équité entre examinateurs d'une même matière : parmi les
            examinateurs offrant un créneau valide, on choisit d'abord celui qui a
            le moins d'oraux déjà attribués (charge la plus faible), et seulement
            en cas d'égalité celui dont le créneau est le plus proche du matin.
            Sans ce critère de charge, le glouton a tendance à privilégier
            systématiquement le même examinateur (celui dont le prochain créneau
            libre est le plus tôt), au détriment d'une répartition équilibrée.
        """
        meilleur_choix: tuple[int, int] | None = None  # (charge, creneau)
        examinateur_choisi = None
        shuffle(liste_examinateur)
        for examinateur in liste_examinateur:
            creneau = examinateur.recherche_creneau(creneau_reference, self.creneaux_minimum_entre_oraux, candidat)
            if creneau is None or not self.verif_ecart_creneaux(creneau, creneau_reference):
                continue
            charge = sum(
                1 for o in examinateur.oraux if o is not None and not isinstance(o, CreneauInterdit)
            )
            candidat_choix = (charge, creneau)
            if meilleur_choix is None or candidat_choix < meilleur_choix:
                meilleur_choix = candidat_choix
                examinateur_choisi = examinateur
        # rien trouvé -> Exception
        if meilleur_choix is None:
            log.critical(f"Run {self.numero_run} : Pas de créneau trouvé")
            raise PasDeCreneauDisponible(candidat, len(liste_examinateur))
        return meilleur_choix[1], examinateur_choisi

    def verif_ecart_creneaux(self, creneau1, creneau2) -> bool:
        """
        Vérifie si l'écart entre deux créneaux est suffisant.

        :param creneau1: Premier créneau à comparer
        :param creneau2: Deuxième créneau à comparer
        :return: True si l'écart est suffisant, False sinon
        :rtype: bool
        """
        if creneau1 is None or creneau2 is None:
            return True
        return abs(creneau1 - creneau2) >= self.creneaux_minimum_entre_oraux

    def verif_ecart_horaire(self, stats=False) -> Union[None, float]:
        """
        Vérifie si l'écart entre les horaires des oraux est suffisant.

        :param stats: choisir de retourner la valeur minimale de l'écart entre les oraux
        :return: Valeur minimale de l'écart entre les oraux si stats est True, None sinon
        :rtype: float | None
        """
        log.debug("vérification des horaires...")
        min_diff = inf
        for candidat in self.liste_candidats:
            heure_oral1 = candidat.oraux[0].heure_sujet
            heure_oral2 = candidat.oraux[1].heure_sujet
            diff = abs((datetime.combine(date(1, 1, 1), heure_oral2) - datetime.combine(date(1, 1, 1),
                                                                                        heure_oral1)).total_seconds())
            if min_diff > diff:
                min_diff = diff
            log.debug(f"verif: {heure_oral1} - {heure_oral2} = {diff / 60} min")
            try:
                assert diff >= self.temps_minimum_entre_oraux.total_seconds()
            except AssertionError:
                log.warning(f"Trop court: {heure_oral1} - {heure_oral2} = {diff / 60} min")
        log.debug(f"Run {self.numero_run} : Vérification terminée.")
        if stats:
            return min_diff
        return None

    def resoudre(self) -> None:
        """
        Résout l'appairage des oraux
        """
        i_matiere = 0
        log.debug(f"Run {self.numero_run} : Démarrage de l'appairage")
        while i_matiere < len(self.liste_matieres):
            matiere_courante: Matiere = self.liste_matieres[i_matiere]
            log.debug(f"************ Matiere courante: {matiere_courante!s}")
            liste_candidats_a_placer = matiere_courante.candidats.copy()
            liste_examinateurs = matiere_courante.examinateurs.copy()
            shuffle(liste_candidats_a_placer)
            while liste_candidats_a_placer:
                candidat = liste_candidats_a_placer.pop(0)
                if len(candidat.oraux) == 0:
                    creneau_reference = None
                elif len(candidat.oraux) == 1:
                    creneau_reference = candidat.oraux[0].creneau
                if len(candidat.oraux) < 2:
                    creneau_oral, examinateur = self.recherche_creneau(creneau_reference, liste_examinateurs, candidat)
                    self.creer_oral(candidat, examinateur, matiere_courante, creneau_oral)
                    log.debug(f"{candidat}, {creneau_reference}, {creneau_oral}")
            # préparation de la matière suivante
            i_matiere += 1
        log.debug(f"Run {self.numero_run} : {len(self.liste_oraux)} oraux créés.")
        log.debug(f"Run {self.numero_run} : Fin de l'appairage")
        assert len(self.liste_oraux) == 2 * len(self.liste_candidats)

    @staticmethod
    def ajouter_temps(heure, temps, arrondi=1) -> time:
        """
        Ajoute un temps à une heure donnée.

        :param heure: Heure à laquelle ajouter le temps
        :param temps: Temps à ajouter
        :param arrondi: Arrondi en minutes (1min par défaut)
        :return: Heure après l'ajout du temps
        :rtype: time
        """
        temps_arrondi = int(round((temps.total_seconds() / 60) / arrondi, 0) * arrondi)
        return (datetime.combine(date(1, 1, 1), heure) + timedelta(minutes=temps_arrondi)).time()

    def _chevauche_pause_meridienne(self, heure_courante: time, matiere_courante: "Matiere") -> bool:
        """Vrai si un oral démarrant à `heure_courante` serait déjà dans la
        pause méridienne, ou s'étendrait dedans (sujet + oral), auquel cas il
        doit être repoussé après la pause plutôt que de l'entamer."""
        if self.heure_pause_meridienne is None:
            return False
        if heure_courante >= self.heure_pause_meridienne:
            return True
        duree_totale = matiere_courante.temps_preparation + matiere_courante.temps_oral
        heure_fin_prevue = self.ajouter_temps(heure_courante, duree_totale)
        return heure_fin_prevue > self.heure_pause_meridienne

    def calcul_horaires(self) -> None:
        """
        Calcule les horaires des oraux à partir des créneaux.
        """
        log.debug(f"Run {self.numero_run} : Calcul des horaires")
        for matiere_courante in self.liste_matieres:
            for examinateur_courant in matiere_courante.examinateurs:
                oraux_examinateur = examinateur_courant.oraux
                heure_courante = self.heure_debut
                pause_meridienne_appliquee = False
                i=0
                while isinstance(oraux_examinateur[i], CreneauInterdit):
                    heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_oral)
                    i+=1
                    if i % self.intervalle_pause == 0:
                        heure_courante = self.ajouter_temps(heure_courante, self.temps_pause)
                n_oraux_avant_pause = i % self.intervalle_pause
                for i_oral in range(i, len(oraux_examinateur)):
                    if i_oral != i:
                        if not self.interrompre_oral and matiere_courante.temps_preparation.total_seconds() % matiere_courante.temps_oral.total_seconds() != 0:
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_preparation)
                        else:
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_oral)
                        if n_oraux_avant_pause >= self.intervalle_pause:
                            n_oraux_avant_pause = 0
                            heure_courante = self.ajouter_temps(heure_courante, self.temps_pause)
                        if oraux_examinateur[i_oral - 1] is not None and not isinstance(oraux_examinateur[i_oral - 1], CreneauInterdit) and oraux_examinateur[i_oral - 1].candidat.tiers_temps:
                            # Même arrondi (1 min) que celui appliqué à heure_oral du candidat
                            # tiers-temps précédent (ligne ~847) : un arrondi différent (10 min)
                            # ici sous-compensait le délai réel, provoquant un chevauchement de
                            # quelques minutes entre son oral et celui du candidat suivant dans
                            # la même salle (ex. temps_preparation=40 → 40/3=13.3min réels contre
                            # 10min seulement compensés ici).
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_preparation / 3)
                    if (
                        self.heure_pause_meridienne is not None
                        and not pause_meridienne_appliquee
                        and self._chevauche_pause_meridienne(heure_courante, matiere_courante)
                    ):
                        heure_courante = max(heure_courante, self.heure_pause_meridienne)
                        heure_courante = self.ajouter_temps(heure_courante, self.duree_pause_meridienne)
                        pause_meridienne_appliquee = True
                    if oraux_examinateur[i_oral] is not None:
                        oraux_examinateur[i_oral].heure_sujet = heure_courante
                        oraux_examinateur[i_oral].heure_oral = self.ajouter_temps(heure_courante,
                                                                                  matiere_courante.temps_preparation)
                        if oraux_examinateur[i_oral].candidat.tiers_temps:
                            oraux_examinateur[i_oral].heure_oral = self.ajouter_temps(
                                oraux_examinateur[i_oral].heure_oral, matiere_courante.temps_preparation / 3)
                        oraux_examinateur[i_oral].heure_fin = self.ajouter_temps(oraux_examinateur[i_oral].heure_oral,
                                                                                 matiere_courante.temps_oral)
                        n_oraux_avant_pause += 1
        log.debug(f"Run {self.numero_run} : fin de calcul des horaires.")

    def statistiques(self) -> dict:
        """
        Calcul des statistiques de l'appairage:
            * temps mini pour les élèves
            * pourcentage d'occupation dans les créneaux pour les examinateurs
        :return: Dictionnaire contenant les statistiques (pourcentage de remplissage des creneaux profs, temps min pour les candidats)
        :rtype: dict
        """
        ecart_mini_candidat = self.verif_ecart_horaire(stats=True) // 60
        log.debug(f"Run {self.numero_run} : ecart mini entre candidats = {ecart_mini_candidat} min")
        nombre_trous = 0
        nombre_creneaux = 0
        for examinateur in self.liste_examinateurs:
            i_oral = len(examinateur.oraux) - 1
            while i_oral >= 0 and examinateur.oraux[i_oral] is None:
                i_oral -= 1
            for i in range(i_oral, -1, -1):
                if examinateur.oraux[i] is None:
                    nombre_trous += 1
                elif not isinstance(examinateur.oraux[i], CreneauInterdit):
                    nombre_creneaux += 1
        pourcentage_occupe = nombre_creneaux / (nombre_trous + nombre_creneaux) * 100
        log.debug(f"Run {self.numero_run} : pourcentage d'oocupation des créneaux: {pourcentage_occupe}%")
        res = {"profs": round(pourcentage_occupe, 2), "candidats": ecart_mini_candidat}
        log.debug(f"Run {self.numero_run} : statistiques du run (%, min): {res}")
        return res

    def heure_fin_journee(self) -> time | None:
        """Heure de fin du dernier oral de la journée, tous examinateurs confondus.

        Nécessite que calcul_horaires() ait déjà été appelé (sinon heure_fin
        vaut None sur chaque Oral). Renvoie None si aucun oral n'a été placé.
        """
        heures_fin = [o.heure_fin for o in self.liste_oraux if o.heure_fin is not None]
        return max(heures_fin) if heures_fin else None


def algo_run(parameters):
    """
    Exécute l'algorithme d'assignation des oraux. (adaptation pour le multiprocessing)

    :param parameters: Paramètres de configuration pour l'algorithme
    """
    numero_run = parameters.get('numero_run', 0)
    log.info(f"Run {numero_run} : lancement")
    alg = AlgoOne(**parameters)
    alg.setup_from_files()
    try:
        alg.resoudre()
    except AlgoError as e:
        log.info(f"Run {numero_run} : fin (échec — {e})")
        return None, str(e)
    except RuntimeError as e:
        log.info(f"Run {numero_run} : fin (échec — {e})")
        return None, str(e)
    alg.calcul_horaires()
    alg.verif_ecart_horaire()
    stats = alg.statistiques()
    log.info(f"Run {numero_run} : fin ({stats})")
    return alg, stats


def selectionner_meilleur_algo(
    results: list,
    ecart_mini_minutes: float,
    heure_fin_cible: time | None = None,
) -> tuple:
    """
    Sélectionne le meilleur run parmi les résultats de algo_run().

    Un run est « conforme candidats » si son écart minimum réel entre les
    deux oraux d'un même candidat (stats['candidats'], en minutes) respecte
    le minimum configuré. Parmi les runs conformes, on choisit celui avec le
    meilleur taux d'occupation examinateurs (stats['profs']) — sans jamais
    élire un run non conforme tant qu'un run conforme existe dans le batch.

    Si `heure_fin_cible` est fourni, ce critère change parmi les runs
    conformes : on préfère celui dont le dernier oral finit le plus tôt
    (`alg.heure_fin_journee()`), le taux d'occupation ne servant plus qu'à
    départager une égalité d'heure de fin. Objectif souple : le repli sur le
    meilleur run tout court (aucun run conforme) reste inchangé, sans égard
    à l'heure de fin.

    Si AUCUN run n'est conforme (cas limite, données très contraintes), on
    retombe sur le meilleur run tout court (par profs) pour ne pas bloquer
    la génération, mais on le signale via `aucun_run_conforme=True` — à
    l'appelant de logger un avertissement explicite avant publication.

    :param results: liste de tuples (alg, info) comme retournés par
                     algo_run() — alg est None en cas d'échec du run (info
                     contient alors le message d'erreur), sinon info est le
                     dict de stats retourné par statistiques().
    :param ecart_mini_minutes: écart minimum candidat requis, en minutes.
    :param heure_fin_cible: heure de fin de journée souhaitée (None = ignorée).
    :return: (best_alg, best_stats, n_err, run_errors, aucun_run_conforme)
    """
    n_err = 0
    run_errors: list[str] = []
    best_percentage_compliant = -1.0
    best_cle_fin_compliant = None
    best_alg_compliant = None
    best_stats_compliant = None
    best_percentage_any = -1.0
    best_alg_any = None
    best_stats_any = None

    for alg, info in results:
        if alg is None:
            n_err += 1
            if info and info not in run_errors:
                run_errors.append(info)
            continue
        stats = info
        if best_percentage_any < stats['profs']:
            best_percentage_any = stats['profs']
            best_alg_any = alg
            best_stats_any = stats
        if stats['candidats'] < ecart_mini_minutes:
            continue
        if heure_fin_cible is not None:
            heure_fin = alg.heure_fin_journee() or time.max
            cle = (heure_fin, -stats['profs'])
            if best_cle_fin_compliant is None or cle < best_cle_fin_compliant:
                best_cle_fin_compliant = cle
                best_alg_compliant = alg
                best_stats_compliant = stats
        elif best_percentage_compliant < stats['profs']:
            best_percentage_compliant = stats['profs']
            best_alg_compliant = alg
            best_stats_compliant = stats

    if best_alg_compliant is not None:
        return best_alg_compliant, best_stats_compliant, n_err, run_errors, False
    return best_alg_any, best_stats_any, n_err, run_errors, best_alg_any is not None


if __name__ == '__main__':
    # Repousser les petites matières en fin de journée : opt-in au niveau de
    # l'API (AlgoOne.__init__ défaut à False) mais activé par défaut ici, en
    # production, pour les deux moteurs (cf. AlgoOne._reserver_petites_matieres).
    _petites_matieres_kwargs = {
        'optimiser_petites_matieres': PETITES_MATIERES_FIN_JOURNEE,
        'seuil_petite_matiere': SEUIL_PETITE_MATIERE,
        'marge_flexibilite_petite_matiere': MARGE_PETITE_MATIERE,
    }
    _pause_meridienne_kwargs = {
        'heure_pause_meridienne': PAUSE_MERIDIENNE_DEBUT,
        'duree_pause_meridienne': PAUSE_MERIDIENNE_DUREE,
    }
    _heure_fin_journee_kwargs = {
        'heure_fin_journee_cible': HEURE_FIN_JOURNEE_CIBLE,
    }
    if ALGO_ENGINE == "cpsat":
        # Moteur CP-SAT : une seule résolution (pas de tirages Monte-Carlo),
        # avec écart minimum candidat garanti par construction du modèle.
        log.info("Lancement de l'algorithme (moteur CP-SAT)")
        from algo_cp import algo_cp_run
        parameters = {'filename_candidats': ELVS_FILE,
                      'filename_examinateurs': PROFS_FILE,
                      'filename_matieres': PREPS_FILE,
                      'temps_minimum_entre_oraux': ECART_MINI_CANDIDAT,
                      'max_creneaux_journee': CRENEAUX,
                      'heure_debut': HEURE_DEBUT,
                      'traiter_matiere_principales_en_premier': True,
                      'numero_run': 0,
                      **_petites_matieres_kwargs,
                      **_pause_meridienne_kwargs,
                      **_heure_fin_journee_kwargs}
        results = [algo_cp_run(parameters)]
    else:
        log.info(f"Lancement de l'algorithme ({N_run} runs en parallèle)")

        # liste des paramètres pour chaque run (tous identiques ici)
        parameters_list = [
                              {'filename_candidats': ELVS_FILE,
                               'filename_examinateurs': PROFS_FILE,
                               'filename_matieres': PREPS_FILE,
                               'temps_minimum_entre_oraux': ECART_MINI_CANDIDAT,
                               'max_creneaux_journee': CRENEAUX,
                               'heure_debut': HEURE_DEBUT,
                               **_petites_matieres_kwargs,
                               **_pause_meridienne_kwargs,
                               **_heure_fin_journee_kwargs,
                               'traiter_matiere_principales_en_premier': True,
                               'numero_run': i}
                                for i in range(N_run)]

        # Lancement des runs en parallèle avec multiprocessing (1 par CPU) —
        # initializer : chaque worker parse les 3 CSV une seule fois à sa
        # création plutôt qu'à chaque run (cf. _initialiser_cache_worker)
        with Pool(initializer=_initialiser_cache_worker,
                 initargs=(ELVS_FILE, PROFS_FILE, PREPS_FILE)) as pool:
            results = pool.map(algo_run, tuple(parameters_list))

    # Sélection du meilleur run — ne retient un run que s'il respecte
    # l'écart minimum candidat (cf. selectionner_meilleur_algo), sauf si
    # aucun run du batch ne le respecte.
    ecart_mini_minutes = ECART_MINI_CANDIDAT.total_seconds() / 60
    best_alg, final_stats, n_err, run_errors, aucun_run_conforme = selectionner_meilleur_algo(
        results, ecart_mini_minutes, heure_fin_cible=HEURE_FIN_JOURNEE_CIBLE,
    )
    if ALGO_ENGINE == "monte_carlo":
        # Non pertinent en CP-SAT : une seule résolution est tentée (pas de
        # tirages Monte-Carlo), donc "n_err / N_run" n'a pas de sens ici —
        # l'échec éventuel est déjà couvert par le message critique ci-dessous.
        log.info(f"erreurs: {n_err} / {N_run} soit {n_err / N_run * 100:.2f}%")
    if best_alg is None:
        log.critical(
            "Aucun placement valide trouvé sur l'ensemble des runs. "
            "Vérifiez la cohérence des fichiers CSV "
            "(nombre de candidats, d'examinateurs, créneaux disponibles)."
        )
        for err in run_errors:
            log.critical(f"  Cause : {err}")
        sys.exit(1)
    if aucun_run_conforme:
        # En CP-SAT, l'écart minimum est une contrainte dure : ce cas ne
        # devrait jamais se produire (sauf solution de repli) — donc pas de
        # mention de "tentatives" (notion propre au Monte-Carlo) ici.
        tentatives = f" trouvé sur {N_run} tentatives" if ALGO_ENGINE == "monte_carlo" else ""
        log.critical(
            f"Aucun run conforme à l'écart minimum candidat "
            f"({ecart_mini_minutes:.0f} min){tentatives} — "
            f"planning publié avec un écart minimum réel de "
            f"{final_stats['candidats']} min (< {ecart_mini_minutes:.0f} min requis)."
        )
    log.info("Meilleur Algo:")
    log.info(f"  Remplissage des créneaux examinateurs : {final_stats['profs']}%")
    log.info(f"  Écart mini entre oraux candidats : {final_stats['candidats']} min")
    _heure_fin_journee = best_alg.heure_fin_journee()
    if _heure_fin_journee is not None:
        log.info(f"  Fin du dernier oral de la journée : {_heure_fin_journee.strftime('%H:%M')}")
    # Dossier de sortie commun (volume Docker, accessible via /download) —
    # hors de webserver/static/ : jamais servi directement par nginx ni par
    # le handler statique de Flask (cf. docs/securite.md).
    from pathlib import Path as _Path
    DOCS_DIR = _Path('webserver') / 'generated'
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Génération de la Base de données")
    liste_connexion_exams, liste_connexion_candidats, liste_connexion_loges = best_alg.save()

    # Écriture des credentials en clair dans un fichier temporaire non chiffré.
    # Ce fichier est destiné à être immédiatement lu et chiffré par le serveur
    # Flask (via algo_bg.py on_done), puis supprimé. Il ne doit jamais persister.
    # /dev/shm est un tmpfs en RAM sous Linux : les données n'atteignent jamais le disque.
    import json as _json
    _shm = _Path('/dev/shm')
    if _shm.exists() and _shm.is_dir():
        _creds_tmp = _shm / 'second_oral_creds_new.json'
    else:
        # Fallback si /dev/shm indisponible (non-Linux, CI, etc.)
        _creds_tmp = _Path(__file__).resolve().parent / 'data' / 'credentials_new.json'
    try:
        if _creds_tmp.parent != _shm:
            _creds_tmp.parent.mkdir(parents=True, exist_ok=True)
        _creds_tmp.write_text(_json.dumps({
            "candidats":    {d['numero']: d['login_key'] for d in liste_connexion_candidats},
            "examinateurs": {salle: mdp for salle, _nom, mdp in liste_connexion_exams},
            "loges":        {nom: mdp for nom, mdp in liste_connexion_loges},
        }))
        log.info(f"Credentials temporaires écrits dans {_creds_tmp}")
    except OSError as _e:
        log.warning(
            f"Impossible d'écrire credentials_new.json ({_e}) — "
            "le renouvellement des identifiants devra être fait manuellement "
            "via /gestion/credentials."
        )

    log.info("Génération des papillons examinateurs")
    liste_papillons_connexion(
        liste_connexion_exams,
        filename=str(DOCS_DIR / 'papillons_examinateurs.pdf'),
        centre_examen=CENTRE_EXAMEN,
    )
    log.info("Génération des papillons candidats")
    liste_papillons_candidats(
        liste_connexion_candidats,
        filename=str(DOCS_DIR / 'papillons_candidats.pdf'),
        centre_examen=CENTRE_EXAMEN,
    )
    log.info("Génération des papillons loges")
    liste_papillons_loges(
        liste_connexion_loges,
        filename=str(DOCS_DIR / 'papillons_loges.pdf'),
        centre_examen=CENTRE_EXAMEN,
    )
    log.info(f"Papillons enregistrés dans {DOCS_DIR}")
    log.info("Fin ----")
