"""Tests unitaires pour algo_cp.py — moteur CP-SAT de placement des oraux."""
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
    """L'écart minimum candidat est une contrainte dure : toujours respecté."""

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

        for candidat in alg.liste_candidats:
            c1, c2 = candidat.oraux[0].creneau, candidat.oraux[1].creneau
            assert abs(c1 - c2) >= alg.creneaux_minimum_entre_oraux, (
                f"{candidat.nom} : écart {abs(c1 - c2)} créneaux "
                f"< minimum requis {alg.creneaux_minimum_entre_oraux}"
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


class TestCutoffCreneauFinJournee:
    """AlgoCP._cutoff_creneau_fin_journee : borne creneau_cible_fin_journee
    à [0, max_creneau] — testée directement, sans passer par le solveur
    CP-SAT. Aucune conversion (le réglage est déjà un index de créneau)."""

    def test_none_si_creneau_cible_non_defini(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"400000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        assert alg._cutoff_creneau_fin_journee(max_creneau=14) is None

    def test_valeur_inchangee_si_dans_les_bornes(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"410000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            creneau_cible_fin_journee=5,
        )
        assert alg._cutoff_creneau_fin_journee(max_creneau=14) == 5

    def test_borne_a_zero_si_cible_negative(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"430000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            creneau_cible_fin_journee=-3,
        )
        assert alg._cutoff_creneau_fin_journee(max_creneau=14) == 0

    def test_borne_a_max_creneau_si_cible_trop_grande(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"440000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            creneau_cible_fin_journee=999,
        )
        assert alg._cutoff_creneau_fin_journee(max_creneau=14) == 14


class TestAlgoCPCreneauCibleFinJournee:
    """Résolution complète avec creneau_cible_fin_journee : objectif souple,
    ne doit jamais empêcher un placement par ailleurs faisable."""

    def test_poids_defaut(self):
        import algo_cp
        assert algo_cp.ALGO_POIDS_CRENEAU_FIN_JOURNEE == 200

    def test_placement_toujours_complet_avec_cible_active(self, tmp_path):
        alg = _build_algo_cp(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"450000000{i}") for i in range(6)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
            heure_debut=time(hour=8, minute=0),
            creneau_cible_fin_journee=1,
        )
        alg.resoudre()

        assert len(alg.liste_candidats) == 6
        for candidat in alg.liste_candidats:
            assert len(candidat.oraux) == 2
            ecart = abs(candidat.oraux[0].creneau - candidat.oraux[1].creneau)
            assert ecart >= alg.creneaux_minimum_entre_oraux


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
