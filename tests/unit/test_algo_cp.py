"""Tests unitaires pour algo_cp.py — moteur CP-SAT de placement des oraux."""
import sys
import types
from datetime import date, datetime, timedelta, time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Chemins ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webserver"))

# ── Mock webserver.app_secrets (doit être injecté avant tout import algo) ────
if "webserver.app_secrets" not in sys.modules:
    _as = types.ModuleType("webserver.app_secrets")
    _as.CENTRE_EXAMEN     = "Centre Test"
    _as.generate_password = lambda n=12: "TestPass12"
    _as.hash_password     = lambda pw, identifier: "testhash"
    _as.APP_SECRET_KEY    = "test-key"
    _as.ACCENT_COLOR      = "#336699"
    _as.DB_PARAMS = {
        "host": "localhost", "user": "u", "password": "p",
        "database": "d", "port": 3306,
        "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
    }
    _as.DB_SALT = "testsalt"
    sys.modules["webserver.app_secrets"] = _as
    if "app_secrets" not in sys.modules:
        sys.modules["app_secrets"] = _as

# ── Mock db_facility_save (évite toute tentative de connexion MariaDB) ───────
if "db_facility_save" not in sys.modules:
    _dfs = types.ModuleType("db_facility_save")
    _dfs.DbFacility = MagicMock
    sys.modules["db_facility_save"] = _dfs

from algo import AlgoError, PasDeCreneauDisponible  # noqa: E402
from algo_cp import AlgoCP, AucuneSolutionCP  # noqa: E402


# ── Constantes CSV (mêmes conventions que test_algo.py) ──────────────────────

_PREPS_HDR = "Matiere;Matière court;Temps preparation (min);Duree (min)"
_EXAM_HDR  = "Nom;Disc.poste;Salle;Heure mini;Etab;Loge"
_CAND_HDR  = "CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs"

_PREPS_BASE = [
    "Maths;Maths;20;20",
    "Philo;Philo;20;20",
]

# Durée totale d'un oral = 40 min = 2 créneaux de 20 min.
# ecart_mini = 40 min -> creneaux_minimum = ceil(40/20 + 1) = 3 créneaux.
_ECART_MINI = timedelta(minutes=40)
_MAX_CRENEAUX = 15


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_csv(path: Path, header: str, rows: list[str]) -> str:
    path.write_text("\n".join([header] + rows) + "\n", encoding="utf-8")
    return str(path)


def _build_algo_cp(tmp_path: Path, candidats: list[str], exams: list[str],
                    preps: list[str] | None = None, **kwargs) -> AlgoCP:
    """Construit et initialise un AlgoCP depuis des listes de lignes CSV."""
    if preps is None:
        preps = _PREPS_BASE
    kwargs.setdefault("temps_minimum_entre_oraux", _ECART_MINI)
    kwargs.setdefault("max_creneaux_journee", _MAX_CRENEAUX)
    kwargs.setdefault("heure_debut", time(hour=8, minute=0))
    alg = AlgoCP(
        filename_candidats=_write_csv(tmp_path / "candidats.csv", _CAND_HDR, candidats),
        filename_examinateurs=_write_csv(tmp_path / "examinateurs.csv", _EXAM_HDR, exams),
        filename_matieres=_write_csv(tmp_path / "preps.csv", _PREPS_HDR, preps),
        **kwargs,
    )
    alg.setup_from_files()
    return alg


def _cand(nom: str, numero: str, m1: str = "Maths", m2: str = "Philo",
          tt: int = 0, etab: str = "", profs: str = "") -> str:
    return f"{nom} ({numero});{m1};{m2};{tt};{etab};{profs}"


def _exam(nom: str, matiere: str, salle: str, heure: int = 8, etab: str = "") -> str:
    return f"{nom};{matiere};{salle};{heure};{etab};Loge1"


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAlgoCPSimplePlacement:
    """Placement basique : tous les candidats doivent obtenir leurs 2 oraux."""

    def test_tous_les_candidats_ont_deux_oraux(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"100000000{i}") for i in range(5)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        assert len(alg.liste_candidats) == 5
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2, f"{candidat.nom} n'a pas 2 oraux"

    def test_chaque_candidat_a_les_deux_matieres(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"200000000{i}") for i in range(5)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        for candidat in alg.liste_candidats:
            matieres = {o.matiere.nom for o in candidat.oraux}
            assert "Maths" in matieres
            assert "Philo" in matieres

    def test_chaque_creneau_examinateur_utilise_une_fois(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"300000000{i}") for i in range(6)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        for exam in alg.liste_examinateurs:
            creneaux_occupes = [o for o in exam.oraux if o is not None]
            creneaux_uniques = {o.creneau for o in creneaux_occupes if hasattr(o, 'creneau')}
            assert len(creneaux_occupes) == len(creneaux_uniques), \
                f"L'examinateur {exam.nom} a des créneaux en double"


class TestAlgoCPEcartMinimumGaranti:
    """L'écart minimum candidat est une contrainte dure : toujours respecté,
    en minutes réelles (cf. AlgoCP._minutes_creneau) — pas seulement en
    nombre de créneaux, qui n'est qu'une proxy imprécise dès que plusieurs
    matières ont des durées d'oral différentes ou qu'une pause méridienne
    est active."""

    def test_ecart_mini_respecte_pour_tous(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"400000000{i}") for i in range(8)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()
        alg.calcul_horaires()

        minutes_mini = alg.temps_minimum_entre_oraux.total_seconds() / 60
        for candidat in alg.liste_candidats:
            h1, h2 = candidat.oraux[0].heure_sujet, candidat.oraux[1].heure_sujet
            ecart_minutes = abs(
                datetime.combine(date(1, 1, 1), h2) - datetime.combine(date(1, 1, 1), h1)
            ).total_seconds() / 60
            assert ecart_minutes >= minutes_mini, (
                f"{candidat.nom} : écart {ecart_minutes} min < minimum requis {minutes_mini} min"
            )


class TestMinutesCreneau:
    """AlgoCP._minutes_creneau() : correspondance créneau -> minutes réelles
    écoulées depuis heure_debut, testée directement (sans solveur)."""

    def test_sans_pause_correspond_a_creneau_fois_duree(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"500000000{i}") for i in range(2)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
        )
        maths = next(m for m in alg.liste_matieres if m.nom == "Maths")
        examinateur = maths.examinateurs[0]
        minutes = alg._minutes_creneau(examinateur, maths)
        # _PREPS_BASE : Maths dure 20 min/créneau, pas de pause périodique ici.
        assert minutes[0] == 0
        assert minutes[3] == 60

    def test_pause_periodique_tous_les_n_creneaux(self, tmp_path):
        """Régression : `_minutes_creneau` doit insérer la pause périodique
        tous les `intervalle_pause` créneaux, comme `calcul_horaires()`
        (`n_oraux_avant_pause`) — une régression pendant l'écriture de cette
        méthode l'avait initialement omise (jamais incrémentée)."""
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"540000000{i}") for i in range(2)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            temps_pause=timedelta(minutes=15),
            intervalle_pause=4,
        )
        maths = next(m for m in alg.liste_matieres if m.nom == "Maths")
        examinateur = maths.examinateurs[0]
        minutes = alg._minutes_creneau(examinateur, maths)
        # Maths : 20 min/créneau. Créneaux 0-3 : 0, 20, 40, 60 (aucune pause
        # avant le 4e créneau délivré) ; la pause (15 min) s'insère juste
        # avant le créneau 4 : 60 + 20 + 15 = 95.
        assert minutes[:5] == [0, 20, 40, 60, 95]

    def test_pause_periodique_coherente_avec_calcul_horaires_reel(self, tmp_path):
        """Le mapping précalculé doit correspondre exactement à calcul_horaires()
        une fois résolu, dans un scénario où tous les créneaux sont occupés
        (aucun écart entre l'approximation pré-résolution et la réalité)."""
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"550000000{i}") for i in range(8)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            max_creneaux_journee=10,
            temps_pause=timedelta(minutes=15),
            intervalle_pause=4,
        )
        maths = next(m for m in alg.liste_matieres if m.nom == "Maths")
        examinateur = maths.examinateurs[0]
        mapping_avant = alg._minutes_creneau(examinateur, maths)

        alg.resoudre()
        alg.calcul_horaires()

        for oral in examinateur.oraux:
            if oral is None:
                continue
            minutes_reelles = round((
                datetime.combine(date(1, 1, 1), oral.heure_sujet)
                - datetime.combine(date(1, 1, 1), alg.heure_debut)
            ).total_seconds() / 60)
            assert minutes_reelles == mapping_avant[oral.creneau], (
                f"créneau {oral.creneau} : précalculé {mapping_avant[oral.creneau]} "
                f"!= réel {minutes_reelles}"
            )

    def test_creneaux_interdits_en_tete_ignores(self, tmp_path):
        # ProfPhilo : "Heure mini" à 9h -> les 3 premiers créneaux (8h/8h20/8h40)
        # sont interdits avec heure_debut=8h et Philo à 20 min/créneau (_PREPS_BASE) ;
        # le temps continue quand même à s'écouler à travers eux (comme dans
        # calcul_horaires()), donc le créneau 3 tombe bien à 9h00 (= 60 min).
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"510000000{i}") for i in range(2)],
            exams=[
                _exam("ProfMaths", "Maths", "A101"),
                _exam("ProfPhilo", "Philo", "B101", heure=9),
            ],
            heure_debut=time(hour=8, minute=0),
        )
        philo = next(m for m in alg.liste_matieres if m.nom == "Philo")
        examinateur = philo.examinateurs[0]
        minutes = alg._minutes_creneau(examinateur, philo)
        assert minutes[0] is None  # créneau interdit (avant 9h)
        assert minutes[3] == 60    # 9h00 - 8h00

    def test_pause_meridienne_ajoutee_une_seule_fois(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"520000000{i}") for i in range(2)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            heure_pause_meridienne=time(hour=8, minute=50),
            duree_pause_meridienne=timedelta(minutes=60),
        )
        maths = next(m for m in alg.liste_matieres if m.nom == "Maths")
        examinateur = maths.examinateurs[0]
        minutes = alg._minutes_creneau(examinateur, maths)
        # Maths : 20 min/créneau, préparation 20 min -> le créneau 1 (8h20,
        # préparation jusqu'à 9h00) chevauche la pause (8h50-9h50) : reporté à
        # 9h50, soit 110 min après heure_debut. Les créneaux suivants
        # s'enchaînent normalement à partir de là, la pause n'étant reportée
        # qu'une seule fois par examinateur.
        assert minutes[0] == 0
        assert minutes[1] == 110
        assert minutes[2] == 130
        assert minutes[3] == 150


class TestAlgoCPPauseMeridienne:
    """Régression : l'écart minimum candidat (en minutes réelles) reste
    garanti même avec une pause méridienne active et des matières de durées
    différentes — cf. investigation ayant motivé _minutes_creneau (un simple
    écart en nombre de créneaux peut être violé de plusieurs dizaines de
    minutes dans ce cas)."""

    def test_ecart_reel_respecte_avec_pause_et_durees_differentes(self, tmp_path):
        preps = ["Maths;Maths;10;10", "Philo;Philo;20;40"]
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"530000000{i}") for i in range(6)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
            preps=preps,
            heure_debut=time(hour=8, minute=0),
            temps_minimum_entre_oraux=timedelta(minutes=80),
            heure_pause_meridienne=time(hour=8, minute=50),
            duree_pause_meridienne=timedelta(minutes=60),
        )
        alg.resoudre()
        alg.calcul_horaires()

        for candidat in alg.liste_candidats:
            h1, h2 = candidat.oraux[0].heure_sujet, candidat.oraux[1].heure_sujet
            ecart_minutes = abs(
                datetime.combine(date(1, 1, 1), h2) - datetime.combine(date(1, 1, 1), h1)
            ).total_seconds() / 60
            assert ecart_minutes >= 80, (
                f"{candidat.nom} : écart {ecart_minutes} min < 80 min requis "
                f"(créneaux {candidat.oraux[0].creneau}/{candidat.oraux[1].creneau})"
            )


class TestAlgoCPExclusions:
    """Exclusions établissement / prof à éviter respectées dans la solution."""

    def test_etablissement_a_eviter_respecte(self, tmp_path):
        # Le candidat vient du même établissement que ProfA (Maths) -> ne doit
        # jamais lui être assigné, même si c'est le seul autre examinateur
        # disponible en dehors de ProfA2.
        alg = _build_algo_cp(
            tmp_path,
            candidats=[
                _cand("Cand0", "5000000000", etab="LyceeX"),
                *[_cand(f"Cand{i}", f"500000000{i}") for i in range(1, 4)],
            ],
            exams=[
                _exam("ProfA", "Maths", "A101", etab="LyceeX"),
                _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
            ],
        )
        alg.resoudre()

        cand0 = next(c for c in alg.liste_candidats if c.nom == "Cand0")
        maths_oral = next(o for o in cand0.oraux if o.matiere.nom == "Maths")
        assert maths_oral.examinateur.nom != "ProfA"

    def test_prof_a_eviter_respecte(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[
                _cand("Cand0", "6000000000", profs="ProfA"),
                *[_cand(f"Cand{i}", f"600000000{i}") for i in range(1, 4)],
            ],
            exams=[
                _exam("ProfA", "Maths", "A101"),
                _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
            ],
        )
        alg.resoudre()

        cand0 = next(c for c in alg.liste_candidats if c.nom == "Cand0")
        maths_oral = next(o for o in cand0.oraux if o.matiere.nom == "Maths")
        assert maths_oral.examinateur.nom != "ProfA"


class TestAlgoCPCreneauxInterdits:
    """Les créneaux interdits (heure de début décalée) ne sont jamais utilisés."""

    def test_creneaux_interdits_jamais_utilises(self, tmp_path):
        # ProfA commence 2 créneaux plus tard que l'heure de début générale.
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"700000000{i}") for i in range(3)],
            exams=[_exam("ProfA", "Maths", "A101", heure=9), _exam("ProfB", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
        )
        n_interdits = sum(
            1 for o in alg.liste_examinateurs[0].oraux
            if type(o).__name__ == "CreneauInterdit"
        )
        assert n_interdits > 0  # précondition du test : au moins 1 créneau interdit
        alg.resoudre()

        prof_a = next(e for e in alg.liste_examinateurs if e.nom == "ProfA")
        for i, oral in enumerate(prof_a.oraux):
            if oral is not None and hasattr(oral, "creneau"):
                assert i >= n_interdits, f"créneau interdit {i} utilisé chez ProfA"


class TestAlgoCPInfaisable:
    """Cas infaisables : erreurs explicites plutôt qu'un plantage silencieux."""

    def test_pas_assez_de_creneaux_leve_aucune_solution_cp(self, tmp_path):
        # 10 candidats, 3 créneaux par examinateur : contrairement au glouton
        # (qui bloque sur UN candidat précis en cours de construction),
        # CP-SAT détecte l'infaisabilité globalement dès la résolution.
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"800000000{i}") for i in range(10)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
            max_creneaux_journee=3,
        )
        with pytest.raises(AucuneSolutionCP) as exc_info:
            alg.resoudre()
        assert isinstance(exc_info.value, AlgoError)

    def test_candidat_sans_examinateur_disponible_leve_pas_de_creneau_disponible(self, tmp_path):
        # Seul examinateur de Maths à éviter par ce candidat -> domaine vide
        # pour ce candidat dès la construction du modèle (détecté avant même
        # de lancer le solveur, contrairement à l'infaisabilité globale
        # ci-dessus).
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand("Cand0", "8100000000", profs="ProfA")],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )
        with pytest.raises(PasDeCreneauDisponible) as exc_info:
            alg.resoudre()
        assert isinstance(exc_info.value, AlgoError)

    def test_ecart_mini_impossible_leve_aucune_solution_cp(self, tmp_path):
        # 1 seul examinateur par matière, assez de créneaux pour loger tout
        # le monde côté capacité brute, mais écart minimum bien trop grand
        # pour être respecté par quiconque : le modèle doit être infaisable.
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"900000000{i}") for i in range(4)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
            max_creneaux_journee=6,
            temps_minimum_entre_oraux=timedelta(hours=5),  # >> 6 créneaux * 20min
        )
        with pytest.raises(AucuneSolutionCP) as exc_info:
            alg.resoudre()
        assert isinstance(exc_info.value, AlgoError)


class TestAlgoCPRandomisation:
    """Le moteur doit varier d'un run à l'autre (choix explicite, cf. algo_cp.py)."""

    def test_deux_runs_donnent_des_affectations_differentes(self, tmp_path):
        exams = [
            _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
            _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
        ]
        candidats = [_cand(f"Cand{i}", f"11000000{i:02d}") for i in range(8)]

        def _placement(numero: int):
            subdir = tmp_path / f"run{numero}"
            subdir.mkdir()
            alg = _build_algo_cp(subdir, candidats=list(candidats), exams=list(exams))
            alg.resoudre()
            return tuple(
                (o.examinateur.nom, o.creneau)
                for c in alg.liste_candidats for o in c.oraux
            )

        resultats = {_placement(i) for i in range(5)}
        assert len(resultats) > 1, "les 5 runs ont produit exactement la même affectation"


class TestAlgoCPEquiteEntreExaminateurs:
    """L'objectif CP-SAT doit répartir la charge équitablement entre
    examinateurs d'une même matière (terme d'équité dominant)."""

    def test_charge_equilibree_entre_deux_examinateurs(self, tmp_path):
        from collections import Counter
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"120000000{i}") for i in range(10)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        charges = Counter(
            o.examinateur.nom for o in alg.liste_oraux if o.matiere.nom == "Maths"
        )
        assert max(charges.values()) - min(charges.values()) <= 1

    def test_depassement_fin_journee_reparti_pas_concentre(self, tmp_path, monkeypatch):
        """Régression : la pénalité de fin de journée est calculée par examinateur
        (max de son propre dépassement), pas sommée sur tous ses oraux en retard —
        sinon un seul examinateur pourrait absorber tout le dépassement pendant
        que ses collègues finissent nettement plus tôt. Le caractère quadratique
        renforce encore cette répartition.
        """
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_POIDS_FIN_JOURNEE", 5000)
        monkeypatch.setattr(algo_cp, "ALGO_CP_TIMEOUT", 5)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"130000000{i}") for i in range(8)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
            # Créneau 2 (20 min/créneau) : sujet à 8h40, fin d'oral à 9h20.
            heure_cible_fin_journee=time(hour=9, minute=20),
        )
        alg.resoudre()
        dernier_creneau_par_examinateur: dict[str, int] = {}
        for o in alg.liste_oraux:
            if o.matiere.nom == "Maths":
                nom = o.examinateur.nom
                dernier_creneau_par_examinateur[nom] = max(
                    dernier_creneau_par_examinateur.get(nom, 0), o.creneau,
                )
        valeurs = list(dernier_creneau_par_examinateur.values())
        assert max(valeurs) - min(valeurs) <= 1


class TestPoidsEquiteEffectif:
    """AlgoCP._poids_equite_effectif() : garantit la dominance de l'équité
    sur le tassement (désormais en minutes réelles, potentiellement bien plus
    grandes qu'un simple index de créneau) — testée directement, sans passer
    par le solveur."""

    def test_poids_configure_suffisant_inchange(self, tmp_path):
        # max_minutes=60, bruit=25, 2 candidats -> borne = 2*2*(60*25+25) = 5050,
        # largement sous ALGO_POIDS_EQUITE par défaut (1 000 000) : pas de relevé.
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"600000000{i}") for i in range(2)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        import algo_cp
        assert alg._poids_equite_effectif(60, 25) == algo_cp.ALGO_POIDS_EQUITE

    def test_poids_configure_insuffisant_releve(self, tmp_path, monkeypatch):
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_POIDS_EQUITE", 10)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"610000000{i}") for i in range(10)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        # max_minutes=1200, bruit=25, 10 candidats -> borne = 2*10*(1200*25+25) = 600 500.
        assert alg._poids_equite_effectif(1200, 25) == 600_501

    def test_ne_descend_jamais_sous_la_valeur_configuree(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"620000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        import algo_cp
        # Même avec max_minutes/bruit à 0 (borne nulle), jamais sous la valeur configurée.
        assert alg._poids_equite_effectif(0, 0) == algo_cp.ALGO_POIDS_EQUITE

    def test_borne_fin_journee_prise_en_compte(self, tmp_path, monkeypatch):
        """La pénalité de fin de journée étant quadratique en minutes, elle peut
        à elle seule dépasser le poids d'équité configuré : sa borne doit entrer
        dans le calcul, sinon l'équité cesserait d'être prioritaire."""
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_POIDS_EQUITE", 10)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"630000000{i}") for i in range(10)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        # borne tassement = 2*10*(1200*25+25) = 600 500, + borne fin de journée.
        assert alg._poids_equite_effectif(1200, 25, 4_000_000) == 4_600_501

    def test_borne_fin_journee_nulle_par_defaut(self, tmp_path, monkeypatch):
        """Fonctionnalité désactivée : le poids reste celui du seul tassement."""
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_POIDS_EQUITE", 10)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"640000000{i}") for i in range(10)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        assert alg._poids_equite_effectif(1200, 25) == 600_501


class TestCutoffMinutesFinJournee:
    """AlgoCP._cutoff_minutes_fin_journee : convertit heure_cible_fin_journee
    en minutes depuis heure_debut — testée directement, sans passer par le
    solveur CP-SAT."""

    def _alg(self, tmp_path, prefixe, **kwargs):
        return _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"{prefixe}{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            **kwargs,
        )

    def test_none_si_heure_cible_non_definie(self, tmp_path):
        assert self._alg(tmp_path, "40000000")._cutoff_minutes_fin_journee() is None

    def test_conversion_en_minutes_depuis_heure_debut(self, tmp_path):
        alg = self._alg(tmp_path, "41000000", heure_cible_fin_journee=time(hour=17, minute=30))
        assert alg._cutoff_minutes_fin_journee() == 570

    def test_zero_si_cible_egale_a_heure_debut(self, tmp_path):
        alg = self._alg(tmp_path, "42000000", heure_cible_fin_journee=time(hour=8, minute=0))
        assert alg._cutoff_minutes_fin_journee() == 0

    def test_borne_a_zero_si_cible_avant_heure_debut(self, tmp_path):
        """Cas absurde mais non bloquant : tout dépasse, rien ne plante."""
        alg = self._alg(tmp_path, "43000000", heure_cible_fin_journee=time(hour=7, minute=0))
        assert alg._cutoff_minutes_fin_journee() == 0

    def test_suit_l_heure_de_debut(self, tmp_path):
        """La cible est absolue : décaler l'heure de début réduit d'autant la
        marge disponible avant la cible."""
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"44000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=9, minute=30),
            heure_cible_fin_journee=time(hour=17, minute=30),
        )
        assert alg._cutoff_minutes_fin_journee() == 480


class TestMinutesFinCreneau:
    """AlgoCP._minutes_fin_creneau : minutes réelles jusqu'à la FIN de l'oral
    de chaque créneau, avec la durée propre à la matière de l'examinateur."""

    # Maths : 20 min de préparation + 20 min d'oral -> créneau de 20 min,
    # oral terminé 40 min après la remise du sujet.
    # Long  : 60 + 60 -> créneau de 60 min, oral terminé 120 min après.
    _PREPS_DUREES_DIFFERENTES = ["Maths;Maths;20;20", "Long;Long;60;60"]

    def _alg(self, tmp_path):
        return _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"45000000{i}", m1="Maths", m2="Long")
                        for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfLong", "Long", "B101")],
            preps=self._PREPS_DUREES_DIFFERENTES,
            heure_debut=time(hour=8, minute=0),
            heure_cible_fin_journee=time(hour=12, minute=0),
        )

    def _par_matiere(self, alg):
        exams = {e.matiere.nom: e for e in alg.liste_examinateurs}
        return {
            nom: alg._minutes_fin_creneau(exam, exam.matiere)
            for nom, exam in exams.items()
        }

    def test_ajoute_la_duree_effective_de_la_matiere(self, tmp_path):
        alg = self._alg(tmp_path)
        for nom, minutes_fin in self._par_matiere(alg).items():
            exam = next(e for e in alg.liste_examinateurs if e.matiere.nom == nom)
            duree = round(
                (exam.matiere.temps_preparation + exam.matiere.temps_oral).total_seconds() / 60
            )
            minutes_sujet = alg._minutes_creneau(exam, exam.matiere)
            assert [
                None if m is None else m + duree for m in minutes_sujet
            ] == minutes_fin

    def test_duree_propre_a_la_matiere_jamais_une_moyenne(self, tmp_path):
        """Le cœur du réglage : à index de créneau égal, deux matières de durées
        différentes finissent à des heures très différentes. Une conversion par
        durée d'oral moyenne (ce que faisait une version retirée de ce réglage)
        les confondrait."""
        minutes = self._par_matiere(self._alg(tmp_path))
        # Créneau 0 : Maths finit à 8h40 (40 min), Long à 10h00 (120 min).
        assert minutes["Maths"][0] == 40
        assert minutes["Long"][0] == 120
        # Créneau 2 : Maths finit à 9h20 (80 min), Long à 12h00 (240 min).
        assert minutes["Maths"][2] == 80
        assert minutes["Long"][2] == 240

    def test_meme_heure_cible_bornes_de_creneaux_differentes(self, tmp_path):
        """Conséquence directe : une même heure cible autorise beaucoup plus de
        créneaux à la matière courte qu'à la longue."""
        alg = self._alg(tmp_path)
        cutoff = alg._cutoff_minutes_fin_journee()   # 12h00 - 8h00 = 240 min
        assert cutoff == 240
        minutes = self._par_matiere(alg)
        derniers = {
            nom: max(
                (creneau for creneau, m in enumerate(minutes_fin)
                 if m is not None and m <= cutoff),
                default=None,
            )
            for nom, minutes_fin in minutes.items()
        }
        assert derniers["Maths"] > derniers["Long"]
        # Le solveur pénalise en minutes, jamais en index : c'est bien la même
        # heure cible qui produit ces deux bornes différentes.
        assert minutes["Long"][derniers["Long"]] <= cutoff
        assert minutes["Maths"][derniers["Maths"]] <= cutoff

    def test_none_conserve_pour_les_creneaux_interdits(self, tmp_path):
        """Un créneau interdit (début de journée décalé) reste non assignable."""
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"46000000{i}") for i in range(3)],
            exams=[
                _exam("ProfMaths", "Maths", "A101", heure=10),
                _exam("ProfPhilo", "Philo", "B101"),
            ],
            heure_debut=time(hour=8, minute=0),
        )
        exam = next(e for e in alg.liste_examinateurs if e.nom == "ProfMaths")
        minutes_sujet = alg._minutes_creneau(exam, exam.matiere)
        minutes_fin = alg._minutes_fin_creneau(exam, exam.matiere)
        assert [m is None for m in minutes_fin] == [m is None for m in minutes_sujet]
        assert any(m is None for m in minutes_fin)


class TestAlgoCPHeureCibleFinJournee:
    """Résolution complète avec heure_cible_fin_journee : objectif souple,
    ne doit jamais empêcher un placement par ailleurs faisable."""

    def test_poids_defaut(self):
        import algo_cp
        assert algo_cp.ALGO_POIDS_FIN_JOURNEE == 25

    def test_poids_equite_defaut(self):
        import algo_cp
        assert algo_cp.ALGO_POIDS_EQUITE == 1_000_000

    def test_bruit_tassement_defaut(self):
        import algo_cp
        assert algo_cp.ALGO_BRUIT_TASSEMENT == 25

    def test_bruit_tassement_zero_ne_plante_pas(self, tmp_path, monkeypatch):
        """Garde-fou : ALGO_BRUIT_TASSEMENT=0 casserait random.randint(0, -1)
        sans le clamp defensif dans resoudre()."""
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_BRUIT_TASSEMENT", 0)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"470000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        assert len(alg.liste_oraux) == 6

    def test_placement_toujours_complet_avec_cible_active(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"450000000{i}") for i in range(6)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            # Cible volontairement intenable : objectif souple, jamais bloquant.
            heure_cible_fin_journee=time(hour=8, minute=30),
        )
        alg.resoudre()
        alg.calcul_horaires()

        assert len(alg.liste_candidats) == 6
        minutes_mini = alg.temps_minimum_entre_oraux.total_seconds() / 60
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2
            h1, h2 = candidat.oraux[0].heure_sujet, candidat.oraux[1].heure_sujet
            ecart_minutes = abs(
                datetime.combine(date(1, 1, 1), h2) - datetime.combine(date(1, 1, 1), h1)
            ).total_seconds() / 60
            assert ecart_minutes >= minutes_mini

    def test_cible_atteignable_respectee(self, tmp_path, monkeypatch):
        """Avec assez d'examinateurs pour tenir la cible, aucun oral ne doit la
        dépasser — c'est ce que l'ancienne pénalité linéaire en index de créneau,
        écrasée par le tassement, ne garantissait pas."""
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_CP_TIMEOUT", 10)
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"480000000{i}") for i in range(6)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
            heure_debut=time(hour=8, minute=0),
            heure_cible_fin_journee=time(hour=10, minute=0),
        )
        alg.resoudre()
        alg.calcul_horaires()
        assert alg.depassement_fin_journee(time(hour=10, minute=0)) == 0

    def test_desactivee_aucune_penalite(self, tmp_path):
        """Sans heure cible, le modèle ne crée aucune variable de dépassement."""
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"490000000{i}") for i in range(4)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        assert len(alg.liste_oraux) == 8


class TestAlgoCPModeOptimal:
    """ALGO_CP_OPTIMAL désactive toute limite de temps (max_time_in_seconds
    reste à son défaut `inf`) — désactivé par défaut, actif seulement si
    explicitement demandé via la variable d'environnement."""

    def _construire(self, tmp_path):
        return _build_algo_cp(
            tmp_path,
            candidats=[_cand("Cand0", "1300000000")],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )

    def test_desactive_par_defaut(self):
        import algo_cp
        assert algo_cp.ALGO_CP_OPTIMAL is False

    def test_timeout_applique_quand_mode_optimal_desactive(self, tmp_path, monkeypatch):
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_CP_OPTIMAL", False)
        captures = []
        original_solve = algo_cp.cp_model.CpSolver.Solve

        def _solve_espion(self, model, *args, **kwargs):
            captures.append(self.parameters.max_time_in_seconds)
            return original_solve(self, model, *args, **kwargs)

        monkeypatch.setattr(algo_cp.cp_model.CpSolver, "Solve", _solve_espion)
        alg = self._construire(tmp_path)
        alg.resoudre()
        assert captures == [float(algo_cp.ALGO_CP_TIMEOUT)]

    def test_aucune_limite_quand_mode_optimal_active(self, tmp_path, monkeypatch):
        import math
        import algo_cp
        monkeypatch.setattr(algo_cp, "ALGO_CP_OPTIMAL", True)
        captures = []
        original_solve = algo_cp.cp_model.CpSolver.Solve

        def _solve_espion(self, model, *args, **kwargs):
            captures.append(self.parameters.max_time_in_seconds)
            return original_solve(self, model, *args, **kwargs)

        monkeypatch.setattr(algo_cp.cp_model.CpSolver, "Solve", _solve_espion)
        alg = self._construire(tmp_path)
        alg.resoudre()
        assert len(captures) == 1
        assert math.isinf(captures[0])
