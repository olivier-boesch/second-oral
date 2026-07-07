"""Tests unitaires pour algo.py — algorithme de placement des oraux."""
import random
import sys
import time as _time
import types
from datetime import timedelta, time, datetime, date
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

from algo import (  # noqa: E402
    AlgoOne, AlgoError, PasDeCreneauDisponible, CreneauInterdit, parser_heure_mini,
)


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


def _cand(nom: str, numero: str, m1: str = "Maths", m2: str = "Philo", tt: int = 0) -> str:
    return f"{nom} ({numero});{m1};{m2};{tt};;"


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


class TestSelectionMeilleurAlgo:
    """selectionner_meilleur_algo ne doit jamais élire un run qui viole
    l'écart minimum candidat (stats['candidats']) tant qu'un run conforme
    existe dans le batch — même si ce run non conforme a le meilleur taux
    d'occupation examinateurs (stats['profs']).

    Contexte du bug : le contrôle d'écart pendant le placement compare des
    indices de créneau en supposant 20 min/créneau pour toutes les matières,
    alors que calcul_horaires() peut avancer d'un pas différent selon la
    matière (temps_preparation non multiple de temps_oral). Un run peut donc
    "réussir" tout en violant l'écart réel — la sélection par pourcentage
    seul (ancien comportement) pouvait publier un tel run."""

    def test_rejette_run_non_conforme_au_profit_du_meilleur_conforme(self):
        from algo import selectionner_meilleur_algo

        alg_non_conforme = MagicMock(name="alg_non_conforme")
        alg_conforme_a  = MagicMock(name="alg_conforme_a")
        alg_conforme_b  = MagicMock(name="alg_conforme_b")

        results = [
            # Meilleure occupation (95%) mais écart réel < 80 min : doit être écarté.
            (alg_non_conforme, {"profs": 95.0, "candidats": 42}),
            # Deux runs conformes (>= 80 min) : le meilleur des deux doit être choisi.
            (alg_conforme_a, {"profs": 80.0, "candidats": 80}),
            (alg_conforme_b, {"profs": 88.0, "candidats": 90}),
        ]

        best_alg, best_stats, n_err, run_errors, aucun_run_conforme = (
            selectionner_meilleur_algo(results, ecart_mini_minutes=80)
        )

        assert best_alg is alg_conforme_b
        assert best_stats == {"profs": 88.0, "candidats": 90}
        assert n_err == 0
        assert run_errors == []
        assert aucun_run_conforme is False

    def test_fallback_si_aucun_run_conforme(self):
        from algo import selectionner_meilleur_algo

        alg_a = MagicMock(name="alg_a")
        alg_b = MagicMock(name="alg_b")

        results = [
            (alg_a, {"profs": 70.0, "candidats": 50}),
            (alg_b, {"profs": 90.0, "candidats": 60}),  # meilleure occupation, mais non conforme
        ]

        best_alg, best_stats, n_err, run_errors, aucun_run_conforme = (
            selectionner_meilleur_algo(results, ecart_mini_minutes=80)
        )

        # Aucun run conforme : on retombe sur le meilleur par occupation (alg_b),
        # mais l'appelant doit être averti.
        assert best_alg is alg_b
        assert best_stats == {"profs": 90.0, "candidats": 60}
        assert aucun_run_conforme is True

    def test_erreurs_dedupliquees_et_alg_none_ignores(self):
        from algo import selectionner_meilleur_algo

        alg_ok = MagicMock(name="alg_ok")
        results = [
            (None, "Candidat X — aucun créneau disponible (1 examinateur(s))"),
            (None, "Candidat X — aucun créneau disponible (1 examinateur(s))"),  # doublon
            (None, "Candidat Y — aucun créneau disponible (2 examinateur(s))"),
            (alg_ok, {"profs": 75.0, "candidats": 100}),
        ]

        best_alg, best_stats, n_err, run_errors, aucun_run_conforme = (
            selectionner_meilleur_algo(results, ecart_mini_minutes=80)
        )

        assert best_alg is alg_ok
        assert n_err == 3
        assert len(run_errors) == 2  # dédupliquées
        assert aucun_run_conforme is False

    def test_aucun_run_reussi(self):
        from algo import selectionner_meilleur_algo

        results = [(None, "erreur 1"), (None, "erreur 2")]

        best_alg, best_stats, n_err, run_errors, aucun_run_conforme = (
            selectionner_meilleur_algo(results, ecart_mini_minutes=80)
        )

        assert best_alg is None
        assert best_stats is None
        assert n_err == 2
        assert aucun_run_conforme is False


class TestSelectionMeilleurAlgoCreneauCible:
    """Quand creneau_cible est fourni, la sélection parmi les runs conformes
    à l'écart minimum privilégie celui dont le dernier créneau utilisé est le
    plus petit (au lieu du meilleur taux d'occupation) — objectif souple lié
    à AlgoOne.creneau_cible_fin_journee."""

    def test_prefere_le_run_dont_le_dernier_creneau_est_le_plus_petit(self):
        from algo import selectionner_meilleur_algo

        alg_tot = MagicMock(name="alg_tot")
        alg_tot.dernier_creneau_journee.return_value = 6
        alg_tard = MagicMock(name="alg_tard")
        alg_tard.dernier_creneau_journee.return_value = 10

        results = [
            # Meilleure occupation (95%) mais finit plus tard : doit être écarté.
            (alg_tard, {"profs": 95.0, "candidats": 90}),
            (alg_tot, {"profs": 80.0, "candidats": 90}),
        ]

        best_alg, best_stats, *_ = selectionner_meilleur_algo(
            results, ecart_mini_minutes=80, creneau_cible=6,
        )

        assert best_alg is alg_tot
        assert best_stats == {"profs": 80.0, "candidats": 90}

    def test_egalite_creneau_departagee_par_occupation(self):
        from algo import selectionner_meilleur_algo

        alg_faible = MagicMock(name="alg_faible")
        alg_faible.dernier_creneau_journee.return_value = 6
        alg_fort = MagicMock(name="alg_fort")
        alg_fort.dernier_creneau_journee.return_value = 6

        results = [
            (alg_faible, {"profs": 70.0, "candidats": 90}),
            (alg_fort, {"profs": 92.0, "candidats": 90}),
        ]

        best_alg, *_ = selectionner_meilleur_algo(
            results, ecart_mini_minutes=80, creneau_cible=6,
        )

        assert best_alg is alg_fort

    def test_ignore_par_defaut(self):
        """Sans creneau_cible, comportement inchangé : meilleure occupation élue
        même si elle finit plus tard qu'une alternative conforme."""
        from algo import selectionner_meilleur_algo

        alg_tard_mais_plein = MagicMock(name="alg_tard_mais_plein")
        alg_tard_mais_plein.dernier_creneau_journee.return_value = 10
        alg_tot_mais_creux = MagicMock(name="alg_tot_mais_creux")
        alg_tot_mais_creux.dernier_creneau_journee.return_value = 6

        results = [
            (alg_tard_mais_plein, {"profs": 95.0, "candidats": 90}),
            (alg_tot_mais_creux, {"profs": 80.0, "candidats": 90}),
        ]

        best_alg, *_ = selectionner_meilleur_algo(results, ecart_mini_minutes=80)

        assert best_alg is alg_tard_mais_plein

    def test_creneau_none_traite_comme_pire_cas(self):
        """Un run dont dernier_creneau_journee() est None (aucun oral placé,
        cas pathologique) ne doit jamais être préféré à un run avec une vraie
        valeur."""
        from algo import selectionner_meilleur_algo

        alg_sans_oral = MagicMock(name="alg_sans_oral")
        alg_sans_oral.dernier_creneau_journee.return_value = None
        alg_normal = MagicMock(name="alg_normal")
        alg_normal.dernier_creneau_journee.return_value = 6

        results = [
            (alg_sans_oral, {"profs": 0.0, "candidats": 90}),
            (alg_normal, {"profs": 80.0, "candidats": 90}),
        ]

        best_alg, *_ = selectionner_meilleur_algo(
            results, ecart_mini_minutes=80, creneau_cible=6,
        )

        assert best_alg is alg_normal


class TestTiersTempsNoOverlap:
    """Un candidat tiers-temps prolonge sa propre préparation de
    temps_preparation/3, ce qui retarde d'autant l'oral suivant dans la
    même salle. calcul_horaires() doit compenser ce délai identiquement
    pour le candidat tiers-temps lui-même (heure_oral) et pour le
    positionnement du candidat suivant (heure_courante) — un arrondi
    différent entre les deux (1 min vs 10 min) sous-compensait le second
    de quelques minutes, provoquant un chevauchement réel dans la salle
    (l'oral suivant démarrait avant la fin de l'oral tiers-temps)."""

    def test_pas_de_chevauchement_apres_un_candidat_tiers_temps(self, tmp_path):
        # temps_preparation=40 → 40/3=13.33 min : arrondi à 13 (1 min) vs 10
        # (10 min) avant correctif — écart de 3 min, assez pour chevaucher.
        preps = ["Management;Mana;40;20", "Philo;Philo;20;20"]
        candidats = [
            _cand("Cand0", "9400000000", m1="Management", m2="Philo", tt=0),
            _cand("Cand1", "9400000001", m1="Management", m2="Philo", tt=1),  # tiers-temps
            _cand("Cand2", "9400000002", m1="Management", m2="Philo", tt=0),
            _cand("Cand3", "9400000003", m1="Management", m2="Philo", tt=0),
        ]
        exams = [
            _exam("ProfMana", "Management", "B108"),
            _exam("ProfPhilo", "Philo", "A1"),
        ]

        for seed in range(30):
            random.seed(seed)
            alg = _build_algo(tmp_path, candidats=candidats, exams=exams, preps=preps)
            alg.resoudre()
            alg.calcul_horaires()

            exam_mana = next(e for e in alg.liste_examinateurs if e.nom == "ProfMana")
            oraux = [o for o in exam_mana.oraux if o is not None]
            for a, b in zip(oraux, oraux[1:]):
                fin_a = datetime.combine(date(1, 1, 1), a.heure_fin)
                oral_b = datetime.combine(date(1, 1, 1), b.heure_oral)
                assert fin_a <= oral_b, (
                    f"seed={seed} : {a.candidat.nom} (tiers_temps={a.candidat.tiers_temps}) "
                    f"finit à {a.heure_fin}, {b.candidat.nom} commence son oral à "
                    f"{b.heure_oral} — chevauchement dans la salle {exam_mana.salle}"
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


class TestAlgoRunLogging:
    """algo_run doit logguer le lancement et la fin de chaque run en INFO,
    même quand l'affichage détaillé (DEBUG) est désactivé."""

    def test_logs_lancement_et_fin_en_info(self, tmp_path, caplog):
        import logging
        from algo import algo_run

        parameters = {
            "filename_candidats": _write_csv(
                tmp_path / "candidats.csv", _CAND_HDR, [_cand("Cand0", "1000000000")]),
            "filename_examinateurs": _write_csv(
                tmp_path / "examinateurs.csv", _EXAM_HDR,
                [_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")]),
            "filename_matieres": _write_csv(tmp_path / "preps.csv", _PREPS_HDR, _PREPS_BASE),
            "temps_minimum_entre_oraux": _ECART_MINI,
            "max_creneaux_journee": _MAX_CRENEAUX,
            "heure_debut": time(hour=8, minute=0),
            "numero_run": 7,
        }
        with caplog.at_level(logging.INFO, logger="algo"):
            alg, stats = algo_run(parameters)

        assert alg is not None
        messages = [r.message for r in caplog.records if r.name == "algo"]
        assert any(m == "Run 7 : lancement" for m in messages), messages
        assert any(m.startswith("Run 7 : fin") for m in messages), messages

    def test_pas_de_details_internes_en_info(self, tmp_path, caplog):
        """Les étapes internes (chargement, appairage...) ne doivent plus polluer l'INFO."""
        import logging
        from algo import algo_run

        parameters = {
            "filename_candidats": _write_csv(
                tmp_path / "candidats.csv", _CAND_HDR, [_cand("Cand0", "1000000001")]),
            "filename_examinateurs": _write_csv(
                tmp_path / "examinateurs.csv", _EXAM_HDR,
                [_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")]),
            "filename_matieres": _write_csv(tmp_path / "preps.csv", _PREPS_HDR, _PREPS_BASE),
            "temps_minimum_entre_oraux": _ECART_MINI,
            "max_creneaux_journee": _MAX_CRENEAUX,
            "heure_debut": time(hour=8, minute=0),
            "numero_run": 8,
        }
        with caplog.at_level(logging.INFO, logger="algo"):
            algo_run(parameters)

        messages = [r.message for r in caplog.records if r.name == "algo"]
        assert len(messages) == 2, messages
        assert "Création matières" not in " ".join(messages)
        assert "Démarrage de l'appairage" not in " ".join(messages)


class TestLoggingLockScope:
    """Le verrou inter-processus (_LOG_LOCK) ne doit protéger que `ch` (la
    console, piped vers le serveur web) — pas `fh` (fichier local, jamais lu
    par le serveur), qui reste toujours au niveau DEBUG et reçoit donc
    l'immense majorité du volume de logs. Le verrouiller aussi re-sérialise
    une grande partie du calcul parallèle pour un bénéfice quasi nul (cf.
    régression constatée avec une première tentative basée sur une Queue,
    encore plus lente à cause du pickling par message et d'un listener
    mono-thread ne pouvant pas absorber le débit de 16 workers parallèles)."""

    def test_ch_is_locked(self):
        import algo
        assert isinstance(algo.ch, algo.LockedStreamHandler)

    def test_fh_is_not_locked(self):
        import logging
        import algo
        assert type(algo.fh) is logging.FileHandler
        assert not isinstance(algo.fh, algo._LockedEmitMixin)

    def test_fh_always_debug_level_regardless_of_display_flag(self):
        import logging
        import algo
        assert algo.fh.level == logging.DEBUG


class TestPetitesMatieresFinJournee:
    """AlgoOne._reserver_petites_matieres : repousse les matières peu demandées
    vers la fin de journée, via CreneauInterdit — opt-in (désactivé par défaut,
    cf. AlgoOne.__init__), donc sans impact sur les autres tests de ce fichier."""

    _PREPS_3_MATIERES = [
        "Maths;Maths;20;20",
        "Philo;Philo;20;20",
        "Musique;Musique;20;20",
    ]

    def _construire(self, tmp_path, optimiser=True, **kwargs):
        # Musique : 2 candidats / 1 examinateur -> petite matière (ratio faible).
        # Maths/Philo : 10 candidats chacune / 1 examinateur -> pas petites.
        candidats = (
            [_cand(f"CandM{i}", f"9000000{i}", m1="Maths", m2="Musique") for i in range(2)]
            + [_cand(f"CandP{i}", f"910000{i:02d}", m1="Maths", m2="Philo") for i in range(10)]
        )
        exams = [
            _exam("ProfA", "Maths", "A101"),
            _exam("ProfB", "Philo", "B101"),
            _exam("ProfM", "Musique", "M101"),
        ]
        return _build_algo(
            tmp_path, candidats=candidats, exams=exams, preps=self._PREPS_3_MATIERES,
            optimiser_petites_matieres=optimiser, **kwargs,
        )

    def test_desactive_par_defaut(self, tmp_path):
        """Sans passer optimiser_petites_matieres, aucun changement de comportement."""
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"920000000{i}") for i in range(3)],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        assert alg.optimiser_petites_matieres is False

    def test_petite_matiere_voit_ses_premiers_creneaux_reserves(self, tmp_path):
        alg = self._construire(tmp_path)
        musique = next(m for m in alg.liste_matieres if m.nom == "Musique")
        prof_musique = musique.examinateurs[0]
        n_interdits = sum(1 for o in prof_musique.oraux if isinstance(o, CreneauInterdit))
        # 2 candidats + marge par défaut (2) = 4 créneaux gardés sur 15 -> 11 réservés.
        assert n_interdits == 11
        # Les créneaux réservés sont bien les premiers (donc les plus tôt dans la journée).
        indices_interdits = [i for i, o in enumerate(prof_musique.oraux) if isinstance(o, CreneauInterdit)]
        assert indices_interdits == list(range(11))

    def test_grosse_matiere_non_affectee(self, tmp_path):
        alg = self._construire(tmp_path)
        for nom in ("Maths", "Philo"):
            matiere = next(m for m in alg.liste_matieres if m.nom == nom)
            for examinateur in matiere.examinateurs:
                assert not any(isinstance(o, CreneauInterdit) for o in examinateur.oraux)

    def test_oraux_petite_matiere_places_en_fin_de_journee(self, tmp_path):
        alg = self._construire(tmp_path)
        alg.resoudre()
        creneaux_musique = [o.creneau for o in alg.liste_oraux if o.matiere.nom == "Musique"]
        assert creneaux_musique
        assert all(c >= 11 for c in creneaux_musique)

    def test_desactivation_explicite_ne_reserve_rien(self, tmp_path):
        alg = self._construire(tmp_path, optimiser=False)
        musique = next(m for m in alg.liste_matieres if m.nom == "Musique")
        prof_musique = musique.examinateurs[0]
        assert not any(isinstance(o, CreneauInterdit) for o in prof_musique.oraux)


class TestHeureFinJournee:
    """AlgoOne.heure_fin_journee() : heure de fin du dernier oral placé,
    tous examinateurs confondus."""

    def test_none_avant_calcul_horaires(self, tmp_path):
        """Sans calcul_horaires(), les Oral n'ont pas encore de heure_fin."""
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"960000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        assert alg.heure_fin_journee() is None

    def test_none_sans_aucun_oral_place(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"970000000{i}") for i in range(0)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        assert alg.heure_fin_journee() is None

    def test_egale_au_max_des_heure_fin_individuelles(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"980000000{i}") for i in range(6)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        alg.calcul_horaires()
        attendu = max(o.heure_fin for o in alg.liste_oraux if o.heure_fin is not None)
        assert alg.heure_fin_journee() == attendu


class TestDernierCreneauJournee:
    """AlgoOne.dernier_creneau_journee() : index du dernier créneau utilisé,
    tous examinateurs confondus — exact, disponible dès resoudre() (pas
    besoin de calcul_horaires())."""

    def test_none_sans_aucun_oral_place(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"990000000{i}") for i in range(0)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        assert alg.dernier_creneau_journee() is None

    def test_egale_au_max_des_creneaux_individuels(self, tmp_path):
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"991000000{i}") for i in range(6)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        attendu = max(o.creneau for o in alg.liste_oraux)
        assert alg.dernier_creneau_journee() == attendu

    def test_disponible_avant_calcul_horaires(self, tmp_path):
        """Contrairement à heure_fin_journee(), pas besoin de calcul_horaires()."""
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"992000000{i}") for i in range(3)],
            exams=[_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")],
        )
        alg.resoudre()
        assert alg.dernier_creneau_journee() is not None


class TestEquiteEntreExaminateurs:
    """recherche_creneau doit répartir la charge équitablement entre les
    examinateurs d'une même matière (priorité au moins chargé)."""

    def test_charge_equilibree_entre_deux_examinateurs(self, tmp_path):
        from collections import Counter
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"930000000{i}") for i in range(10)],
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

    def test_charge_equilibree_trois_examinateurs_nombre_non_divisible(self, tmp_path):
        from collections import Counter
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"940000000{i}") for i in range(10)],
            exams=[
                _exam("ProfA", "Maths", "A101"), _exam("ProfA2", "Maths", "A102"),
                _exam("ProfA3", "Maths", "A103"), _exam("ProfB", "Philo", "B101"),
            ],
        )
        alg.resoudre()
        charges = Counter(
            o.examinateur.nom for o in alg.liste_oraux if o.matiere.nom == "Maths"
        )
        assert len(charges) == 3
        assert max(charges.values()) - min(charges.values()) <= 1


class TestPauseMeridienne:
    """calcul_horaires : aucun oral ne doit être en cours (heure_oral -> heure_fin)
    pendant la pause méridienne configurée — un oral qui empièterait dessus est
    repoussé après la pause, une seule fois par examinateur."""

    def _construire(self, tmp_path, n_candidats=6, **kwargs):
        candidats = [_cand(f"Cand{i}", f"95000000{i}") for i in range(n_candidats)]
        exams = [_exam("ProfMaths", "Maths", "A101"), _exam("ProfPhilo", "Philo", "B101")]
        kwargs.setdefault("temps_pause", timedelta(minutes=0))
        kwargs.setdefault("intervalle_pause", 1000)  # neutralise la pause périodique existante
        kwargs.setdefault("heure_debut", time(hour=8, minute=0))
        alg = _build_algo(tmp_path, candidats=candidats, exams=exams, **kwargs)
        alg.resoudre()
        alg.calcul_horaires()
        return alg

    def test_desactivee_par_defaut(self, tmp_path):
        alg = self._construire(tmp_path)
        assert alg.heure_pause_meridienne is None

    def test_aucun_oral_ne_chevauche_la_pause(self, tmp_path):
        pause_debut = time(hour=8, minute=50)
        pause_fin_dt = datetime.combine(date(1, 1, 1), pause_debut) + timedelta(minutes=30)
        alg = self._construire(
            tmp_path,
            heure_pause_meridienne=pause_debut,
            duree_pause_meridienne=timedelta(minutes=30),
        )
        for oral in alg.liste_oraux:
            debut = datetime.combine(date(1, 1, 1), oral.heure_oral)
            fin = datetime.combine(date(1, 1, 1), oral.heure_fin)
            pause_debut_dt = datetime.combine(date(1, 1, 1), pause_debut)
            assert fin <= pause_debut_dt or debut >= pause_fin_dt

    def test_oral_repousse_juste_apres_la_pause(self, tmp_path):
        # Sans pause, l'examinateur Maths enchaînerait 8h00, 8h20, 8h40... :
        # l'oral d'indice 1 (sujet à 8h20, oral 8h40-9h00) empièterait sur la
        # pause [8h50, 9h20) -> il doit être repoussé juste après (9h20).
        alg = self._construire(
            tmp_path,
            heure_pause_meridienne=time(hour=8, minute=50),
            duree_pause_meridienne=timedelta(minutes=30),
        )
        prof_maths = next(m for m in alg.liste_matieres if m.nom == "Maths").examinateurs[0]
        heures_sujet = [o.heure_sujet for o in prof_maths.oraux if o is not None]
        assert heures_sujet[0] == time(hour=8, minute=0)
        assert heures_sujet[1] == time(hour=9, minute=20)

    def test_pause_appliquee_une_seule_fois(self, tmp_path):
        alg = self._construire(
            tmp_path,
            heure_pause_meridienne=time(hour=8, minute=50),
            duree_pause_meridienne=timedelta(minutes=30),
        )
        prof_maths = next(m for m in alg.liste_matieres if m.nom == "Maths").examinateurs[0]
        heures_sujet = sorted(o.heure_sujet for o in prof_maths.oraux if o is not None)
        # Après le rattrapage à 9h20, les créneaux s'enchaînent de nouveau
        # normalement (pas de second saut) : écart constant de 20 min.
        for h1, h2 in zip(heures_sujet[1:], heures_sujet[2:]):
            dt1 = datetime.combine(date(1, 1, 1), h1)
            dt2 = datetime.combine(date(1, 1, 1), h2)
            assert dt2 - dt1 == timedelta(minutes=20)


class TestParserHeureMini:
    """parser_heure_mini : colonne 'Heure mini' de examinateurs.csv —
    heure entière ('9') pour compatibilité, ou heure:minute ('9:30')."""

    def test_heure_entiere(self):
        assert parser_heure_mini("9") == time(hour=9, minute=0)

    def test_heure_avec_minutes(self):
        assert parser_heure_mini("9:30") == time(hour=9, minute=30)

    def test_heure_avec_zero_initial(self):
        assert parser_heure_mini("09:05") == time(hour=9, minute=5)

    def test_espaces_ignores(self):
        assert parser_heure_mini("  9:30  ") == time(hour=9, minute=30)

    def test_valeur_integre_dans_le_placement(self, tmp_path):
        """Les minutes de 'Heure mini' influencent bien le nombre de
        créneaux interdits (pas seulement l'heure ronde)."""
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"96000000{i}") for i in range(3)],
            exams=[_exam("Prof Maths", "Maths", "A101", heure="8:30"),
                   _exam("Prof Philo", "Philo", "B101")],
        )
        prof_maths = next(m for m in alg.liste_matieres if m.nom == "Maths").examinateurs[0]
        # heure_debut de la journée = 8h00 (défaut de _build_algo) ; créneaux
        # de 20 min (_PREPS_BASE) ; 8h30 - 8h00 = 30 min -> 1 créneau interdit
        # (30 // 20 = 1), pas 0 (arrondi à l'heure) ni 2 (arrondi à l'heure sup.).
        n_interdits = sum(1 for o in prof_maths.oraux if isinstance(o, CreneauInterdit))
        assert n_interdits == 1


class TestPremierOralApresCreneauxInterdits:
    """Régression : un examinateur qui commence après le début de journée
    (créneaux interdits en tête, cf. 'Heure mini') voyait son premier oral
    décalé d'un créneau supplémentaire par rapport à l'heure déclarée."""

    def test_premier_oral_respecte_heure_mini_declaree(self, tmp_path):
        # Journée à 7h20, examinateur déclaré à 8h00 -> 40 min d'écart, soit
        # exactement 2 créneaux interdits de 20 min (_PREPS_BASE). Son premier
        # oral doit démarrer pile à 8h00, pas 8h20.
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"97000000{i}") for i in range(3)],
            exams=[_exam("Prof Maths", "Maths", "A101", heure="8:00"),
                   _exam("Prof Philo", "Philo", "B101")],
            heure_debut=time(hour=7, minute=20),
        )
        alg.resoudre()
        alg.calcul_horaires()
        prof_maths = next(m for m in alg.liste_matieres if m.nom == "Maths").examinateurs[0]
        heures_sujet = sorted(
            o.heure_sujet for o in prof_maths.oraux
            if o is not None and not isinstance(o, CreneauInterdit)
        )
        assert heures_sujet[0] == time(hour=8, minute=0)
        assert heures_sujet[1] == time(hour=8, minute=20)

    def test_examinateur_qui_commence_avec_la_journee_inchange(self, tmp_path):
        """Non-régression : un examinateur dont 'Heure mini' == heure_debut
        de la journée (aucun créneau interdit) doit toujours démarrer pile
        à cette heure."""
        alg = _build_algo(
            tmp_path,
            candidats=[_cand(f"Cand{i}", f"98000000{i}") for i in range(2)],
            exams=[_exam("Prof Maths", "Maths", "A101", heure="7:20"),
                   _exam("Prof Philo", "Philo", "B101")],
            heure_debut=time(hour=7, minute=20),
        )
        alg.resoudre()
        alg.calcul_horaires()
        prof_maths = next(m for m in alg.liste_matieres if m.nom == "Maths").examinateurs[0]
        heures_sujet = sorted(o.heure_sujet for o in prof_maths.oraux if o is not None)
        assert heures_sujet[0] == time(hour=7, minute=20)


class TestCacheDonneesWorker:
    """Cache par processus worker (Pool Monte-Carlo) — évite de relire/
    re-parser les CSV à chaque run (cf. algo._initialiser_cache_worker)."""

    def test_initialiser_cache_worker_peuple_le_cache(self, tmp_path):
        import algo as algo_module

        candidats_path = _write_csv(
            tmp_path / "candidats.csv", _CAND_HDR, [_cand("Cand0", "1000000000")],
        )
        exams_path = _write_csv(
            tmp_path / "examinateurs.csv", _EXAM_HDR,
            [_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        preps_path = _write_csv(tmp_path / "preps.csv", _PREPS_HDR, _PREPS_BASE)

        assert algo_module._cache_donnees_worker is None
        try:
            algo_module._initialiser_cache_worker(candidats_path, exams_path, preps_path)
            cache = algo_module._cache_donnees_worker
            assert cache is not None
            assert len(cache["candidats"]) == 1
            assert len(cache["examinateurs"]) == 2
            assert len(cache["matieres"]) == 2
        finally:
            algo_module._cache_donnees_worker = None

    def test_setup_from_files_utilise_le_cache_sans_toucher_au_disque(self, tmp_path, monkeypatch):
        """Une fois le cache peuplé, setup_from_files() ne doit plus jamais
        appeler charger_fichier_comme_liste (donc plus lire le disque) —
        y compris pour une instance dont les chemins de fichiers sont
        invalides, preuve que seul le cache est utilisé."""
        import algo as algo_module

        candidats = [_cand("Cand0", "2000000000")]
        exams = [_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")]
        cache = {
            "candidats": [
                {"CANDIDAT": "Cand0 (2000000000)", "CHOIX DISCIPLINE 1": "Maths",
                 "CHOIX DISCIPLINE 2": "Philo", "TT": "0", "Etab": "", "Profs": ""},
            ],
            "examinateurs": [
                {"Nom": "Prof Maths", "Disc.poste": "Maths", "Salle": "A101",
                 "Heure mini": "8", "Etab": "", "Loge": "Loge1"},
                {"Nom": "Prof Philo", "Disc.poste": "Philo", "Salle": "B101",
                 "Heure mini": "8", "Etab": "", "Loge": "Loge1"},
            ],
            "matieres": [
                {"Matiere": "Maths", "Matière court": "Maths",
                 "Temps preparation (min)": "20", "Duree (min)": "20"},
                {"Matiere": "Philo", "Matière court": "Philo",
                 "Temps preparation (min)": "20", "Duree (min)": "20"},
            ],
        }
        monkeypatch.setattr(algo_module, "_cache_donnees_worker", cache)

        def _echoue_si_appelee(filename):
            raise AssertionError(f"charger_fichier_comme_liste appelée avec {filename!r} "
                                 "alors que le cache worker était disponible")
        monkeypatch.setattr(algo_module, "charger_fichier_comme_liste", _echoue_si_appelee)

        alg = AlgoOne(
            filename_candidats="/inexistant/candidats.csv",
            filename_examinateurs="/inexistant/examinateurs.csv",
            filename_matieres="/inexistant/preps.csv",
            temps_minimum_entre_oraux=_ECART_MINI,
            max_creneaux_journee=_MAX_CRENEAUX,
            heure_debut=time(hour=8, minute=0),
        )
        alg.setup_from_files()

        assert len(alg.liste_candidats) == 1
        assert len(alg.liste_examinateurs) == 2
        assert len(alg.liste_matieres) == 2

    def test_setup_from_files_lit_les_fichiers_si_cache_absent(self, tmp_path):
        """Non-régression : sans cache worker (CP-SAT, tests, script direct),
        le comportement reste la lecture fichier classique."""
        import algo as algo_module
        assert algo_module._cache_donnees_worker is None

        alg = _build_algo(
            tmp_path,
            candidats=[_cand("Cand0", "3000000000")],
            exams=[_exam("Prof Maths", "Maths", "A101"), _exam("Prof Philo", "Philo", "B101")],
        )
        assert len(alg.liste_candidats) == 1
        assert len(alg.liste_examinateurs) == 2
