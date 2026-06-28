"""Tests unitaires pour algo.py — algorithme de placement des oraux."""
import sys
import time as _time
import types
from datetime import timedelta, time
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
    # Ne pas écraser app_secrets si l'integration conftest l'a déjà injecté
    # (ordre de collection : integration avant unit → son hash_password scrypt doit rester intact)
    if "app_secrets" not in sys.modules:
        sys.modules["app_secrets"] = _as

# ── Mock db_facility_save (évite toute tentative de connexion MariaDB) ───────
if "db_facility_save" not in sys.modules:
    _dfs = types.ModuleType("db_facility_save")
    _dfs.DbFacility = MagicMock
    sys.modules["db_facility_save"] = _dfs

from algo import AlgoOne, AlgoError, PasDeCreneauDisponible  # noqa: E402


# ── Constantes CSV ────────────────────────────────────────────────────────────

_PREPS_HDR = "Matiere;Matière court;Temps preparation (min);Duree (min)"
_EXAM_HDR  = "Nom;Disc.poste;Salle;Heure mini;Etab;Loge"
_CAND_HDR  = "CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs"

# Deux matières avec oraux courts (20 min prep + 20 min oral) pour des tests rapides.
_PREPS_BASE = [
    "Maths;Maths;20;20",
    "Philo;Philo;20;20",
]

# Durée totale d'un oral = 40 min = 2 créneaux de 20 min.
# ecart_mini = 40 min → creneaux_minimum = ceil(40/20 + 1) = 3 créneaux.
_ECART_MINI = timedelta(minutes=40)
_MAX_CRENEAUX = 15


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_csv(path: Path, header: str, rows: list[str]) -> str:
    (path).write_text("\n".join([header] + rows) + "\n", encoding="utf-8")
    return str(path)


def _build_algo(tmp_path: Path, candidats: list[str], exams: list[str],
                preps: list[str] | None = None, **kwargs) -> AlgoOne:
    """Construit et initialise un AlgoOne depuis des listes de lignes CSV."""
    if preps is None:
        preps = _PREPS_BASE
    kwargs.setdefault("temps_minimum_entre_oraux", _ECART_MINI)
    kwargs.setdefault("max_creneaux_journee", _MAX_CRENEAUX)
    kwargs.setdefault("heure_debut", time(hour=8, minute=0))
    alg = AlgoOne(
        filename_candidats=_write_csv(tmp_path / "candidats.csv", _CAND_HDR, candidats),
        filename_examinateurs=_write_csv(tmp_path / "examinateurs.csv", _EXAM_HDR, exams),
        filename_matieres=_write_csv(tmp_path / "preps.csv", _PREPS_HDR, preps),
        **kwargs,
    )
    alg.setup_from_files()
    return alg


def _cand(nom: str, numero: str, m1: str = "Maths", m2: str = "Philo") -> str:
    return f"{nom} ({numero});{m1};{m2};0;;"


def _exam(nom: str, matiere: str, salle: str, heure: int = 8) -> str:
    return f"{nom};{matiere};{salle};{heure};;Loge1"


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAlgoSimplePlacement:
    """Placement basique : 3 candidats, 1 examinateur par matière."""

    def test_tous_les_candidats_ont_deux_oraux(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"100000000{i}") for i in range(3)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        assert len(alg.liste_candidats) == 3
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2, f"{candidat.nom} n'a pas 2 oraux"

    def test_chaque_candidat_a_les_deux_matieres(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"200000000{i}") for i in range(3)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        for candidat in alg.liste_candidats:
            matieres = {o.matiere.nom for o in candidat.oraux}
            assert "Maths" in matieres, f"{candidat.nom} n'a pas d'oral de Maths"
            assert "Philo" in matieres, f"{candidat.nom} n'a pas d'oral de Philo"

    def test_total_oraux_coherent(self, tmp_path):
        n = 5
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"300000000{i}") for i in range(n)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        # 2 oraux par candidat
        assert len(alg.liste_oraux) == 2 * n

    def test_chaque_creneau_examinateur_utilise_une_fois(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"400000000{i}") for i in range(4)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        alg.resoudre()

        for exam in alg.liste_examinateurs:
            creneaux_occupes = [o for o in exam.oraux if o is not None]
            creneaux_uniques = {o.creneau for o in creneaux_occupes if hasattr(o, 'creneau')}
            assert len(creneaux_occupes) == len(creneaux_uniques), \
                f"L'examinateur {exam.nom} a des créneaux en double"


class TestAlgoInsufficientCapacity:
    """Capacité insuffisante : doit lever PasDeCreneauDisponible."""

    def test_trop_de_candidats_pour_un_examinateur(self, tmp_path):
        # 10 candidats Maths, 1 examinateur Maths avec seulement 3 créneaux
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"500000000{i}") for i in range(10)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
            max_creneaux_journee=3,  # seulement 3 créneaux disponibles par prof
        )

        with pytest.raises(PasDeCreneauDisponible) as exc_info:
            alg.resoudre()

        err = exc_info.value
        assert isinstance(err, AlgoError)
        assert err.candidat is not None
        assert err.n_examinateurs >= 1

    def test_message_erreur_contient_contexte(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"60000000{i}") for i in range(10)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
            max_creneaux_journee=3,
        )

        with pytest.raises(PasDeCreneauDisponible) as exc_info:
            alg.resoudre()

        msg = str(exc_info.value)
        # Le message doit contenir le numéro du candidat bloqué
        assert "6000000" in msg or "Cand" in msg

    def test_algo_error_est_une_runtime_error(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"70000000{i}") for i in range(10)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
            max_creneaux_journee=3,
        )

        with pytest.raises(RuntimeError):
            alg.resoudre()


class TestAlgoHorairesCoherence:
    """Vérification que les horaires calculés sont cohérents (pas d'overlap)."""

    def test_pas_de_creneau_identique_chez_un_examinateur(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"800000000{i}") for i in range(6)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        alg.calcul_horaires()

        for exam in alg.liste_examinateurs:
            heures = [o.heure_oral for o in exam.oraux if o is not None and hasattr(o, 'heure_oral')]
            assert len(heures) == len(set(heures)), \
                f"{exam.nom} a des oraux à la même heure : {heures}"

    def test_tous_les_oraux_ont_des_horaires(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"900000000{i}") for i in range(4)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        alg.calcul_horaires()

        for oral in alg.liste_oraux:
            assert oral.heure_sujet is not None, f"Oral sans heure_sujet : {oral}"
            assert oral.heure_oral  is not None, f"Oral sans heure_oral : {oral}"
            assert oral.heure_fin   is not None, f"Oral sans heure_fin : {oral}"

    def test_heure_fin_apres_heure_debut(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"910000000{i}") for i in range(4)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        alg.calcul_horaires()

        for oral in alg.liste_oraux:
            assert oral.heure_fin > oral.heure_sujet, \
                f"heure_fin ({oral.heure_fin}) <= heure_sujet ({oral.heure_sujet})"


class TestCandidatSeparationMinimum:
    """Vérification de l'écart minimum entre les deux oraux d'un candidat."""

    def test_ecart_mini_respecte(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"920000000{i}") for i in range(5)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        alg.calcul_horaires()

        ecart_mini_sec = _ECART_MINI.total_seconds()
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2
            h0 = candidat.oraux[0].heure_sujet
            h1 = candidat.oraux[1].heure_sujet
            from datetime import datetime, date
            diff = abs(
                (datetime.combine(date(1, 1, 1), h1) - datetime.combine(date(1, 1, 1), h0))
                .total_seconds()
            )
            assert diff >= ecart_mini_sec, (
                f"{candidat.nom} : écart {diff/60:.0f} min < "
                f"{ecart_mini_sec/60:.0f} min requis"
            )

    def test_ecart_mini_en_creneaux(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"930000000{i}") for i in range(5)],
            exams=[
                _exam("Prof Maths", "Maths", "A101"),
                _exam("Prof Philo", "Philo", "B101"),
            ],
        )
        alg.resoudre()

        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2
            ecart = abs(candidat.oraux[0].creneau - candidat.oraux[1].creneau)
            assert ecart >= alg.creneaux_minimum_entre_oraux, (
                f"{candidat.nom} : écart {ecart} créneaux < "
                f"{alg.creneaux_minimum_entre_oraux} requis"
            )


class TestAlgoTiming:
    """Mesure des temps d'exécution d'un run unique (sans multiprocessing)."""

    @pytest.mark.parametrize("n_cand,n_exam_par_matiere", [
        (20,  3),
        (50,  6),
        (100, 11),
    ])
    def test_temps_resolution(self, tmp_path, n_cand, n_exam_par_matiere):
        candidats = [_cand(f"Cand{i}", f"9{i:08d}") for i in range(n_cand)]
        exams = (
            [_exam(f"ProfMaths{j}", "Maths", f"M{j:02d}") for j in range(n_exam_par_matiere)]
            + [_exam(f"ProfPhilo{j}", "Philo", f"P{j:02d}") for j in range(n_exam_par_matiere)]
        )

        alg = _build_algo(tmp_path, candidats=candidats, exams=exams)

        t0 = _time.perf_counter()
        alg.resoudre()
        alg.calcul_horaires()
        elapsed = _time.perf_counter() - t0

        assert len(alg.liste_oraux) == 2 * n_cand
        # Seuil souple : 10 secondes par run pour n_cand ≤ 100
        assert elapsed < 10.0, f"Trop lent pour {n_cand} candidats : {elapsed:.2f}s"
        print(f"\n  [{n_cand} candidats] resoudre+calcul_horaires : {elapsed*1000:.0f} ms")
