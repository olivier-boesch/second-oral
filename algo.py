#!/usr/bin/python3
"""
Placement automatique des oraux de second groupe
Olivier Boesch (c) 2023
"""
import logging
import sys
from csv import DictReader, DictWriter
from datetime import timedelta, datetime, time, date
from math import ceil, inf
from os.path import join
from random import shuffle
from typing import Union
from multiprocessing import Pool

import colorama

from db_facility_save import DbFacility

from webserver.app_secrets import generate_password, hash_password, CENTRE_EXAMEN
from webserver.reports import (
    liste_papillons_connexion,
    liste_papillons_candidats,
    liste_papillons_loges,
)

colorama.init(autoreset=True)

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

N_run               = _env_int("ALGO_N_RUN",    1_000)
ECART_MINI_CANDIDAT = timedelta(minutes=_env_int("ALGO_ECART_MINI", 80))
HEURE_DEBUT         = _env_time("ALGO_HEURE_DEBUT", 8, 10)
CRENEAUX            = _env_int("ALGO_CRENEAUX", 13)

# données
DATA_DIR = 'data'
ELVS_FILE = join(DATA_DIR, "candidats.csv")
PROFS_FILE = join(DATA_DIR, "examinateurs.csv")
PREPS_FILE = join(DATA_DIR, 'preps.csv')
OK_CHAR = "\U00002714"  # ✔
NOK_CHAR = "\U00002718"  # ✘
WARNING_CHAR = "\U0001F534"  # 🔴


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
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
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

    def verifie_temps_minimum(self, oral: "Oral", intervalle_minimum: int) -> bool:
        """
        Vérifie si l'oral respecte le temps minimum entre deux oraux
        
        :param oral: Oral à vérifier
        :type oral: "Oral"
        :param intervalle_minimum: Intervalle minimum entre les oraux en créneaux
        :type intervalle_minimum: int
        :return: True si le temps minimum est respecté, False sinon
        :rtype: bool
        """
        return abs(self.oraux[0].creneau - oral.creneau) >= intervalle_minimum

    def verifie_horaire_oraux(self, intervalle_minimum: int) -> bool:
        """
        Vérifie si les oraux respectent l'horaire minimum entre deux oraux
        
        :param intervalle_minimum: Intervalle minimum entre les oraux en créneaux
        :type intervalle_minimum: int
        :return: True si l'horaire minimum est respecté, False sinon
        :rtype: bool
        """
        if len(self.oraux) == 2:
            return self.verifie_temps_minimum(self.oraux[1], intervalle_minimum)
        return True

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

    @property
    def temps_total(self) -> timedelta:
        """
        Calcule le temps total de l'oral (préparation + oral)

        :return: Temps total de l'oral
        :rtype: timedelta
        """
        return self.temps_preparation + self.temps_oral


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
                 intervalle_pause: int = 4, traiter_matiere_principales_en_premier: bool = True, numero_run: int = 0):
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

    def setup_from_files(self) -> None:
        """Charge les données depuis les fichiers et crée les objets"""
        # chargement des données
        liste_donnees_candidats: list[dict] = charger_fichier_comme_liste(self.filename_candidats)
        liste_donnees_matieres: list[dict] = charger_fichier_comme_liste(self.filename_matieres)
        liste_donnees_examinateurs: list[dict] = charger_fichier_comme_liste(self.filename_examinateurs)
        log.info(f"Run {self.numero_run} : Données chargées")
        # liste des matières
        log.info(f"Run {self.numero_run} : Création matières")
        self.liste_matieres = [
            Matiere(nom=c["Matiere"], nom_court=c['Matière court'], temps_preparation=c["Temps preparation (min)"], temps_oral=c["Duree (min)"]) for c
            in liste_donnees_matieres]
        log.debug(f"Run {self.numero_run} : Liste des matières créée ({len(self.liste_matieres)} matières crées)")
        log.debug(f"Run {self.numero_run} : Liste des matières: {self.liste_matieres!s}")
        # liste des examinateurs
        log.info(f"Run {self.numero_run} : Création Examinateurs")
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
            heure_debut = time(hour=int(p['Heure mini']))
            t = Examinateur(nom=p["Nom"],
                            matiere=matiere,
                            max_creneaux_journee=self.max_creneaux_journee,
                            salle=salle, loge=loge, heure_debut=heure_debut, etablissements=etab)
            matiere.examinateurs.append(t)
            self.liste_examinateurs.append(t)
            nb_oraux_interdits = max(0, (datetime.combine(datetime.today(), t.heure_debut) - datetime.combine(datetime.today(), self.heure_debut)).total_seconds() // matiere.temps_oral.total_seconds())
            if nb_oraux_interdits > 0:
                log.info(f"Run {self.numero_run} : {t.nom} -> {nb_oraux_interdits} créneaux interdits (début à {t.heure_debut})")
                for i in range(int(nb_oraux_interdits)):
                    t.oraux[i] = CreneauInterdit()
        log.info(f"Run {self.numero_run} : Liste des examinateurs créée ({len(self.liste_examinateurs)} examinateurs créés)")
        log.debug(f"Run {self.numero_run} : Liste des examinateurs ({self.liste_examinateurs}) examinateurs créés)")
        # liste des candidats
        log.info(f"Run {self.numero_run} : Création candidats")
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
        log.info(f"Run {self.numero_run} : Liste des candidats crée ({len(self.liste_candidats)} candidats créés)")
        log.debug(f"Run {self.numero_run} : Liste des candidats: {self.liste_candidats!s}")
        self.liste_matieres.sort(key=lambda m: len(m.candidats), reverse=self.traiter_matiere_principales_en_premier)
        log.info(f"Run {self.numero_run} : Liste des matières triée")
        log.debug(f"Run {self.numero_run} : Liste des matères: {self.liste_matieres!s}")

    def save(self) -> tuple[list[tuple], list[dict], list[tuple]]:
        """
        Sauvegarde toutes les données en base et génère les informations de connexion.

        :return: Tuple (connexions examinateurs, connexions candidats, connexions loges)
        :rtype: tuple[list[tuple], list[dict], list[tuple]]
        """
        db = DbFacility()
        db.save_all(self)

        # Examinateurs : (salle, nom, mot_de_passe)
        liste_exams = sorted(
            [e.infos_connexion for e in self.liste_examinateurs],
            key=lambda m: m[0],
        )

        # Candidats : {'nom', 'numero', 'login_key'}
        liste_candidats = sorted(
            [c.infos_connexion for c in self.liste_candidats],
            key=lambda d: d['nom'],
        )

        # Loges : un mot de passe unique par loge
        loges_mdp: dict[str, str] = {}
        for examinateur in self.liste_examinateurs:
            if examinateur.loge not in loges_mdp:
                loges_mdp[examinateur.loge] = generate_password()
        loges_hashes = {nom: hash_password(mdp, nom) for nom, mdp in loges_mdp.items()}
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
        """
        creneau_plus_proche = self.max_creneaux_journee + 1
        examinateur_choisi = None
        shuffle(liste_examinateur)
        for examinateur in liste_examinateur:
            creneau = examinateur.recherche_creneau(creneau_reference, self.creneaux_minimum_entre_oraux, candidat)
            # le plus proche possible du matin et avec un écart suffisant
            if creneau is not None and creneau < creneau_plus_proche \
                    and self.verif_ecart_creneaux(creneau, creneau_reference):
                creneau_plus_proche = creneau
                examinateur_choisi = examinateur
        # rien trouvé -> Exception
        if creneau_plus_proche == self.max_creneaux_journee + 1:
            log.critical(f"Run {self.numero_run} : Pas de créneau trouvé")
            raise RuntimeError("Pas de créneau trouvé. Abandon")
        return creneau_plus_proche, examinateur_choisi

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
        log.info("vérification des horaires...")
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
        log.info(f"Run {self.numero_run} : Démarrage de l'appairage")
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
        log.info(f"Run {self.numero_run} : {len(self.liste_oraux)} oraux créés.")
        log.info(f"Run {self.numero_run} : Fin de l'appairage")
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

    def calcul_horaires(self) -> None:
        """
        Calcule les horaires des oraux à partir des créneaux.
        """
        log.info(f"Run {self.numero_run} : Calcul des horaires")
        for matiere_courante in self.liste_matieres:
            for examinateur_courant in matiere_courante.examinateurs:
                oraux_examinateur = examinateur_courant.oraux
                heure_courante = self.heure_debut
                i=0
                while isinstance(oraux_examinateur[i], CreneauInterdit):
                    heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_oral)
                    i+=1
                    if i % self.intervalle_pause == 0:
                        heure_courante = self.ajouter_temps(heure_courante, self.temps_pause)
                n_oraux_avant_pause = i % self.intervalle_pause
                for i_oral in range(i, len(oraux_examinateur)):
                    if i_oral != 0:
                        if not self.interrompre_oral and matiere_courante.temps_preparation.total_seconds() % matiere_courante.temps_oral.total_seconds() != 0:
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_preparation)
                        else:
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_oral)
                        if n_oraux_avant_pause >= self.intervalle_pause:
                            n_oraux_avant_pause = 0
                            heure_courante = self.ajouter_temps(heure_courante, self.temps_pause)
                        if oraux_examinateur[i_oral - 1] is not None and not isinstance(oraux_examinateur[i_oral - 1], CreneauInterdit) and oraux_examinateur[i_oral - 1].candidat.tiers_temps:
                            heure_courante = self.ajouter_temps(heure_courante, matiere_courante.temps_preparation / 3,
                                                                10)
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
        log.info(f"Run {self.numero_run} : fin de calcul des horaires.")

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
        log.info(f"Run {self.numero_run} : statistiques du run (%, min): {res}")
        return res

    def sauvegarder_oraux(self, filename) -> None:
        """
        Sauvegarde les données des oraux dans un fichier CSV.

        :param filename: Nom du fichier CSV où sauvegarder les données
        """
        with open(filename, 'w', newline='') as csvfile:
            fieldnames = ['Candidat', 'Numéro', 'Tiers temps',
                          'Examinateur', 'Matière', 'Salle', 'Heure sujet', 'Heure oral', 'Heure fin']
            writer = DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for oral in self.liste_oraux:
                writer.writerow({
                    'Candidat': oral.candidat.nom,
                    'Numéro': oral.candidat.numero,
                    'Tiers temps': "Oui" if oral.candidat.tiers_temps else "Non",
                    'Examinateur': oral.examinateur.nom,
                    'Matière': oral.matiere.nom,
                    'Salle': oral.examinateur.salle,
                    'Heure sujet': oral.heure_sujet.strftime('%H:%M') if oral.heure_sujet else '',
                    'Heure oral': oral.heure_oral.strftime('%H:%M') if oral.heure_oral else '',
                    'Heure fin': oral.heure_fin.strftime('%H:%M') if oral.heure_fin else ''
                })
        log.info(f"Run {self.numero_run} : Données des oraux sauvegardées dans {filename}")


def algo_run(parameters):
    """
    Exécute l'algorithme d'assignation des oraux. (adaptation pour le multiprocessing)

    :param parameters: Paramètres de configuration pour l'algorithme
    """
    alg = AlgoOne(**parameters)
    alg.setup_from_files()
    try:
        alg.resoudre()
    except RuntimeError:
        return None
    alg.calcul_horaires()
    alg.verif_ecart_horaire()
    stats = alg.statistiques()
    return alg, stats


if __name__ == '__main__':
    n_err = 0              # nombre d'erreurs
    best_percentage = 0    # meilleur pourcentage de remplissage des créneaux profs
    min_students_time = 0  # meilleur temps mini entre oraux candidats
    best_alg = None        # meilleur algo

    # liste des paramètres pour chaque run (tous identiques ici)
    parameters_list = [
                          {'filename_candidats': ELVS_FILE,
                           'filename_examinateurs': PROFS_FILE,
                           'filename_matieres': PREPS_FILE,
                           'temps_minimum_entre_oraux': ECART_MINI_CANDIDAT,
                           'max_creneaux_journee': CRENEAUX,
                           'heure_debut': HEURE_DEBUT,
                           'traiter_matiere_principales_en_premier': True,
                           'numero_run': i}
                            for i in range(N_run)]

    # Lancement des runs en parallèle avec multiprocessing (1 par CPU)
    with Pool() as pool:
        results = pool.map(algo_run, tuple(parameters_list))

    # Analyse des résultats
    for res in results:
        # erreur dans le run
        if res is None:
            n_err += 1
            continue
        # succès et calcul des stats
        # on garde le meilleur resultat (calculé sur le pourcentage de remplissage des créneaux profs)
        alg, stats = res
        if best_percentage < stats['profs']:
                best_percentage = stats['profs']
                min_students_time = stats['candidats']
                best_alg = alg
    log.info(f"erreurs: {n_err} / {N_run} soit {n_err / N_run * 100:.2f}%")
    if best_alg is None:
        log.critical(
            "Aucun placement valide trouvé sur l'ensemble des runs. "
            "Vérifiez la cohérence des fichiers CSV "
            "(nombre de candidats, d'examinateurs, créneaux disponibles)."
        )
        sys.exit(1)
    log.info("Meilleur Algo:")
    best_alg.statistiques()
    # Dossier de sortie commun (volume Docker, accessible via /download)
    from pathlib import Path as _Path
    DOCS_DIR = _Path('webserver') / 'static' / 'docs'
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Génération de la Base de données")
    liste_connexion_exams, liste_connexion_candidats, liste_connexion_loges = best_alg.save()

    # Écriture des credentials en clair dans un fichier temporaire non chiffré.
    # Ce fichier est destiné à être immédiatement lu et chiffré par le serveur
    # Flask (via algo_bg.py on_done), puis supprimé. Il ne doit jamais persister.
    import json as _json
    # Chemin absolu basé sur __file__ : garantit la résolution correcte
    # quel que soit le répertoire de travail du sous-processus.
    _creds_tmp = _Path(__file__).resolve().parent / 'data' / 'credentials_new.json'
    try:
        _creds_tmp.parent.mkdir(parents=True, exist_ok=True)
        _creds_tmp.write_text(_json.dumps({
            "examinateurs": {salle: mdp for salle, _nom, mdp in liste_connexion_exams},
            "loges": {nom: mdp for nom, mdp in liste_connexion_loges},
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
