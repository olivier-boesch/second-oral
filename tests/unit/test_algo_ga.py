"""Tests unitaires pour algo_ga.py — moteur génétique de placement des oraux."""
import sys
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
    if "app_secrets" not in sys.modules:
        sys.modules["app_secrets"] = _as

# ── Mock db_facility_save (évite toute tentative de connexion MariaDB) ───────
if "db_facility_save" not in sys.modules:
    _dfs = types.ModuleType("db_facility_save")
    _dfs.DbFacility = MagicMock
    sys.modules["db_facility_save"] = _dfs

from algo import AlgoError, PasDeCreneauDisponible  # noqa: E402
from algo_ga import AlgoGA, AucuneSolutionGA  # noqa: E402


# ── Constantes CSV (mêmes conventions que test_algo.py / test_algo_cp.py) ───

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


def _build_algo_ga(tmp_path: Path, candidats: list[str], exams: list[str],
                    preps: list[str] | None = None, **kwargs) -> AlgoGA:
    """Construit et initialise un AlgoGA depuis des listes de lignes CSV."""
    if preps is None:
        preps = _PREPS_BASE
    kwargs.setdefault("temps_minimum_entre_oraux", _ECART_MINI)
    kwargs.setdefault("max_creneaux_journee", _MAX_CRENEAUX)
    kwargs.setdefault("heure_debut", time(hour=8, minute=0))
    alg = AlgoGA(
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

class TestAlgoGASimplePlacement:
    """Placement basique : tous les candidats doivent obtenir leurs 2 oraux."""

    def test_tous_les_candidats_ont_deux_oraux(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"100000000{i}") for i in range(6)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()

        assert len(alg.liste_candidats) == 6
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2, f"{candidat.nom} n'a pas 2 oraux"

    def test_chaque_candidat_a_les_deux_matieres(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"200000000{i}") for i in range(6)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()

        for candidat in alg.liste_candidats:
            matieres = {o.matiere.nom for o in candidat.oraux}
            assert "Maths" in matieres
            assert "Philo" in matieres

    def test_chaque_creneau_examinateur_utilise_une_fois(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"300000000{i}") for i in range(8)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()

        for exam in alg.liste_examinateurs:
            creneaux_occupes = [o for o in exam.oraux if o is not None]
            creneaux_uniques = {o.creneau for o in creneaux_occupes if hasattr(o, 'creneau')}
            assert len(creneaux_occupes) == len(creneaux_uniques), \
                f"L'examinateur {exam.nom} a des créneaux en double"


class TestAlgoGAExclusions:
    """Exclusions établissement / prof à éviter : règle métier absolue — jamais
    de solution résiduelle non conforme (cf. AlgoGA.resoudre())."""

    def test_etablissement_a_eviter_respecte(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[
                _cand("Cand0", "5000000000", etab="LyceeX"),
                *[_cand(f"Cand{i}", f"500000000{i}") for i in range(1, 8)],
            ],
            exams=[
                _exam("ProfA", "Maths", "A101", etab="LyceeX"),
                _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
                _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()

        cand0 = next(c for c in alg.liste_candidats if c.nom == "Cand0")
        maths_oral = next(o for o in cand0.oraux if o.matiere.nom == "Maths")
        assert maths_oral.examinateur.nom != "ProfA"

    def test_prof_a_eviter_respecte(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[
                _cand("Cand0", "6000000000", profs="ProfA"),
                *[_cand(f"Cand{i}", f"600000000{i}") for i in range(1, 8)],
            ],
            exams=[
                _exam("ProfA", "Maths", "A101"),
                _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
                _exam("ProfB2", "Philo", "B102"),
            ],
        )
        alg.resoudre()

        cand0 = next(c for c in alg.liste_candidats if c.nom == "Cand0")
        maths_oral = next(o for o in cand0.oraux if o.matiere.nom == "Maths")
        assert maths_oral.examinateur.nom != "ProfA"


class TestAlgoGAInfaisable:
    """Cas infaisables : erreurs explicites plutôt qu'un plantage silencieux."""

    def test_candidat_sans_examinateur_disponible_leve_pas_de_creneau_disponible(self, tmp_path):
        # Seul examinateur de Maths à éviter par ce candidat -> domaine vide.
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand("Cand0", "8100000000", profs="ProfA")],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )
        with pytest.raises(PasDeCreneauDisponible) as exc_info:
            alg.resoudre()
        assert isinstance(exc_info.value, AlgoError)

    def test_pas_assez_de_creneaux_leve_aucune_solution_ga(self, tmp_path):
        # 10 candidats, 3 créneaux par examinateur : capacité insuffisante,
        # détectée avant même de lancer l'évolution.
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"800000000{i}") for i in range(10)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
            max_creneaux_journee=3,
        )
        with pytest.raises(AucuneSolutionGA) as exc_info:
            alg.resoudre()
        assert isinstance(exc_info.value, AlgoError)


class TestAlgoGARandomisation:
    """Le moteur doit varier d'un run à l'autre (population/mutation stochastiques)."""

    def test_deux_runs_donnent_des_affectations_differentes(self, tmp_path):
        exams = [
            _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
            _exam("ProfB", "Philo", "B101"), _exam("ProfB2", "Philo", "B102"),
        ]
        candidats = [_cand(f"Cand{i}", f"11000000{i:02d}") for i in range(8)]

        def _placement(numero: int):
            subdir = tmp_path / f"run{numero}"
            subdir.mkdir()
            alg = _build_algo_ga(subdir, candidats=list(candidats), exams=list(exams))
            alg.resoudre()
            return tuple(
                (o.examinateur.nom, o.creneau)
                for c in alg.liste_candidats for o in c.oraux
            )

        resultats = {_placement(i) for i in range(5)}
        assert len(resultats) > 1, "les 5 runs ont produit exactement la même affectation"


class TestAlgoGAEquiteEntreExaminateurs:
    """Le fitness doit répartir la charge équitablement entre examinateurs
    d'une même matière (pénalité de déséquilibre)."""

    def test_charge_equilibree_entre_deux_examinateurs(self, tmp_path):
        from collections import Counter
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"130000000{i}") for i in range(10)],
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


class TestAlgoGAReparationEcart:
    """_reparer_ecart doit réduire les violations d'écart minimum sans casser
    les exclusions établissement/prof à éviter."""

    def test_reduit_les_violations_ecart(self, tmp_path):
        import random
        random.seed(42)
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"14000000{i:02d}") for i in range(8)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )
        matiere_data = alg._construire_matiere_data()
        # Permutation identité : chaque candidat i est au créneau i dans les
        # DEUX matières (même ordre de candidats des deux côtés) -> écart nul
        # pour tout le monde, violation maximale (creneaux_minimum_entre_oraux
        # >= 1 forcément > 0).
        individu = {
            mid: list(range(len(data['slots']))) for mid, data in matiere_data.items()
        }
        avant = alg._evaluer(individu, matiere_data).violations_ecart
        assert avant > 0

        for _ in range(30):
            alg._reparer_ecart(individu, matiere_data)
        apres = alg._evaluer(individu, matiere_data)
        assert apres.violations_ecart < avant
        assert apres.violations_exclusion == 0


class TestAlgoGAReparationDesequilibre:
    """_reparer_desequilibre doit réduire l'écart de charge entre examinateurs
    d'une même matière sans casser les exclusions."""

    def test_reduit_le_desequilibre(self, tmp_path):
        import random
        random.seed(42)
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"15000000{i:02d}") for i in range(10)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfB", "Philo", "B101"),
            ],
        )
        matiere_data = alg._construire_matiere_data()
        # Permutation identité : comme les créneaux de ProfA précèdent ceux de
        # ProfA2 dans la liste des slots (ordre par examinateur), les 10
        # premiers indices tombent tous chez ProfA -> déséquilibre maximal.
        individu = {
            mid: list(range(len(data['slots']))) for mid, data in matiere_data.items()
        }
        avant = alg._evaluer(individu, matiere_data).desequilibre_charge
        assert avant > 1

        for _ in range(30):
            alg._reparer_desequilibre(individu, matiere_data)
        apres = alg._evaluer(individu, matiere_data)
        assert apres.desequilibre_charge < avant
        assert apres.violations_exclusion == 0


class TestAlgoGAMutationAdaptative:
    """Le taux de mutation contrôle effectivement si une mutation se produit."""

    def test_taux_zero_ne_change_rien(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"16000000{i:02d}") for i in range(6)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )
        matiere_data = alg._construire_matiere_data()
        individu = {
            mid: list(range(len(data['slots']))) for mid, data in matiere_data.items()
        }
        avant = {mid: list(perm) for mid, perm in individu.items()}
        alg._mutation(individu, matiere_data, taux=0.0)
        assert individu == avant

    def test_taux_un_modifie_toutes_les_matieres(self, tmp_path):
        alg = _build_algo_ga(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"17000000{i:02d}") for i in range(6)],
            exams=[_exam("ProfA", "Maths", "A101"), _exam("ProfB", "Philo", "B101")],
        )
        matiere_data = alg._construire_matiere_data()
        individu = {
            mid: list(range(len(data['slots']))) for mid, data in matiere_data.items()
        }
        avant = {mid: list(perm) for mid, perm in individu.items()}
        alg._mutation(individu, matiere_data, taux=1.0)
        assert any(individu[mid] != avant[mid] for mid in matiere_data)
