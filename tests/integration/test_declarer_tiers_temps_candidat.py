"""Tests d'intégration pour la route /gestion/candidat/tiers-temps
(un candidat déclare — ou retire, si posé par erreur — un tiers-temps en
cours de journée après le placement initial). Le sens de l'action se déduit
de l'état actuel du candidat : pas de tiers-temps -> déclaration (préparation
étendue), déjà tiers-temps -> retrait (préparation réduite). Dans les deux
cas, les oraux suivants chez les deux mêmes examinateurs sont cascadés du
même délai.

La logique de replanification elle-même (rebalance.py) est testée en détail
dans tests/unit/test_rebalance.py::TestPlanifierTiersTemps et
TestPlanifierTiersTempsRetrait — ces tests-ci vérifient uniquement le
câblage de la route (requêtes DB, gabarit rendu, mise à jour du flag
tiers_temps, notifications SSE ciblant le candidat et les cascadés).
"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


def _td(h, m=0):
    return timedelta(hours=h, minutes=m)


CANDIDAT_INFO = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0}
CANDIDAT_INFO_TT = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 1}

# ── Déclaration : oraux pas encore étendus ───────────────────────────────────
ORAUX_CANDIDAT = [
    {"id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 1, "examinateur": "ProfMaths",
     "heure_sujet": _td(9), "heure_oral": _td(9, 20), "heure_fin": _td(9, 40)},
    {"id": 20, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 2, "examinateur": "ProfPhilo",
     "heure_sujet": _td(11), "heure_oral": _td(11, 15), "heure_fin": _td(11, 30)},
]

# ProfMaths (id=1) : l'oral du candidat 100 + un candidat suivant (101) juste après
ORAUX_EXAMINATEUR_MATHS = [
    {"id_candidat": 100, "id": 10, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(9), "heure_oral": _td(9, 20), "heure_fin": _td(9, 40), "maj": 0},
    {"id_candidat": 101, "id": 30, "candidat": "Cand Suivant", "numero": "N101", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(9, 40), "heure_oral": _td(10), "heure_fin": _td(10, 20), "maj": 0},
]

# ProfPhilo (id=2) : juste l'oral du candidat 100, personne après
ORAUX_EXAMINATEUR_PHILO = [
    {"id_candidat": 100, "id": 20, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(11), "heure_oral": _td(11, 15), "heure_fin": _td(11, 30), "maj": 0},
]

# Autre oral du candidat cascadé (101) — loin, aucun souci d'écart minimum
AUTRE_ORAL_101 = [
    {"id": 30, "heure_sujet": _td(9, 40)},
    {"id": 31, "heure_sujet": _td(14)},
]

# ── Retrait : mêmes candidats, mais oraux DÉJÀ étendus (tiers-temps actif) ───
ORAUX_CANDIDAT_TT = [
    {"id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 1, "examinateur": "ProfMaths",
     "heure_sujet": _td(9), "heure_oral": _td(9, 27), "heure_fin": _td(9, 47)},
    {"id": 20, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 2, "examinateur": "ProfPhilo",
     "heure_sujet": _td(11), "heure_oral": _td(11, 20), "heure_fin": _td(11, 35)},
]

ORAUX_EXAMINATEUR_MATHS_TT = [
    {"id_candidat": 100, "id": 10, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 1, "heure_sujet": _td(9), "heure_oral": _td(9, 27), "heure_fin": _td(9, 47), "maj": 0},
    {"id_candidat": 101, "id": 30, "candidat": "Cand Suivant", "numero": "N101", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(9, 47), "heure_oral": _td(10, 7), "heure_fin": _td(10, 27), "maj": 0},
]

ORAUX_EXAMINATEUR_PHILO_TT = [
    {"id_candidat": 100, "id": 20, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 1, "heure_sujet": _td(11), "heure_oral": _td(11, 20), "heure_fin": _td(11, 35), "maj": 0},
]


def _side_effect_defaut(sql, *args):
    """Dispatch par identité de requête SQL — robuste à l'ordre d'appel."""
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
        return [CANDIDAT_INFO]
    if sql is dfw.SELECT_ORAUX_CANDIDAT_TIERS_TEMPS:
        return ORAUX_CANDIDAT
    if sql is dfw.SELECT_ORAUX_EXAMINATEUR:
        id_examinateur = args[0]
        return ORAUX_EXAMINATEUR_MATHS if id_examinateur == 1 else ORAUX_EXAMINATEUR_PHILO
    if sql is dfw.SELECT_LISTE_EDITION_ORAL:
        id_candidat = args[0]
        return AUTRE_ORAL_101 if id_candidat == 101 else []
    if sql is dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR:
        return [{"salle": "X", "identifiant": "examinateurX", "loge": "LogeX"}]
    return []


def _side_effect_retrait(sql, *args):
    """Même candidat, mais tiers-temps déjà actif (oraux déjà étendus)."""
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
        return [CANDIDAT_INFO_TT]
    if sql is dfw.SELECT_ORAUX_CANDIDAT_TIERS_TEMPS:
        return ORAUX_CANDIDAT_TT
    if sql is dfw.SELECT_ORAUX_EXAMINATEUR:
        id_examinateur = args[0]
        return ORAUX_EXAMINATEUR_MATHS_TT if id_examinateur == 1 else ORAUX_EXAMINATEUR_PHILO_TT
    if sql is dfw.SELECT_LISTE_EDITION_ORAL:
        id_candidat = args[0]
        return AUTRE_ORAL_101 if id_candidat == 101 else []
    if sql is dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR:
        return [{"salle": "X", "identifiant": "examinateurX", "loge": "LogeX"}]
    return []


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    """Isole les tests des fichiers réels (algo_params.json)."""
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")


class TestDeclarerTiersTempsForm:
    def test_get_sans_id_404(self, admin_client):
        r = admin_client.get("/gestion/candidat/tiers-temps")
        assert r.status_code == 404

    def test_get_candidat_introuvable_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.get("/gestion/candidat/tiers-temps?id_candidat=999")
        assert r.status_code == 404


class TestDeclarerTiersTempsPrevisualisation:
    def test_previsualisation_affiche_les_deux_oraux_et_la_cascade(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        r = admin_client.get("/gestion/candidat/tiers-temps?id_candidat=100")
        assert r.status_code == 200
        body = r.data.decode()
        assert "N100" in body
        assert "N101" in body  # oral cascadé
        assert "ProfMaths" in body
        assert "ProfPhilo" in body
        assert "/gestion/edit-examinateur?id_examinateur=1" in body
        assert "/gestion/edit-examinateur?id_examinateur=2" in body

    def test_aucune_mise_a_jour_pendant_la_previsualisation(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        db_mock.make_sql_update.reset_mock()
        admin_client.get("/gestion/candidat/tiers-temps?id_candidat=100")
        db_mock.make_sql_update.assert_not_called()


class TestDeclarerTiersTempsConfirmation:
    def test_confirmer_met_a_jour_candidat_et_les_trois_oraux(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = _side_effect_defaut

        r = admin_client.post("/gestion/candidat/tiers-temps", data={
            "id_candidat": "100", "etape": "confirmer",
        })
        assert r.status_code == 200
        body = r.data.decode()
        assert "Tiers-temps déclaré" in body

        import db_facility_web as dfw
        sql_appelees = [c.args[0] for c in db_mock.make_sql_update.call_args_list]
        assert dfw.UPDATE_CANDIDAT_TIERS_TEMPS in sql_appelees
        assert sql_appelees.count(dfw.UPDATE_INFOS_ORAL) == 3  # 2 propres + 1 cascadé
        assert db_mock.make_sql_update.call_count == 4

        # Le flag est bien mis à 1 pour le bon candidat
        _, kwargs_tt = next(
            c for c in db_mock.make_sql_update.call_args_list if c.args[0] is dfw.UPDATE_CANDIDAT_TIERS_TEMPS
        )
        assert kwargs_tt["tiers_temps"] == 1
        assert kwargs_tt["id_candidat"] == 100

        # Vérifie les nouveaux horaires appliqués à l'oral Maths du candidat
        _, kwargs_oral_maths = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.UPDATE_INFOS_ORAL and c.kwargs.get("id") == 10
        )
        assert kwargs_oral_maths["heure_oral"] == "09:27"
        assert kwargs_oral_maths["heure_fin"] == "09:47"

        # Vérifie le décalage cascadé de l'oral suivant (candidat 101)
        _, kwargs_oral_cascade = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.UPDATE_INFOS_ORAL and c.kwargs.get("id") == 30
        )
        assert kwargs_oral_cascade["heure_sujet"] == "09:47"

        # Notifications : le candidat lui-même ET le candidat cascadé
        assert publish_mock.called
        canaux = [c.kwargs.get("channel") for c in publish_mock.call_args_list]
        assert "candidat_N100" in canaux
        assert "candidat_N101" in canaux


class TestRetirerTiersTemps:
    """Un candidat qui a déjà un tiers-temps se voit proposer un RETRAIT
    (préparation réduite + cascade plus tôt) plutôt qu'un blocage."""

    def test_previsualisation_propose_le_retrait(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_retrait
        r = admin_client.get("/gestion/candidat/tiers-temps?id_candidat=100")
        assert r.status_code == 200
        body = r.data.decode()
        assert "N100" in body
        assert "N101" in body

    def test_confirmer_retire_le_flag_et_restaure_les_horaires(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = _side_effect_retrait

        r = admin_client.post("/gestion/candidat/tiers-temps", data={
            "id_candidat": "100", "etape": "confirmer",
        })
        assert r.status_code == 200

        import db_facility_web as dfw
        _, kwargs_tt = next(
            c for c in db_mock.make_sql_update.call_args_list if c.args[0] is dfw.UPDATE_CANDIDAT_TIERS_TEMPS
        )
        assert kwargs_tt["tiers_temps"] == 0

        # L'oral Maths du candidat retrouve son horaire d'origine (9h20/9h40)
        _, kwargs_oral_maths = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.UPDATE_INFOS_ORAL and c.kwargs.get("id") == 10
        )
        assert kwargs_oral_maths["heure_oral"] == "09:20"
        assert kwargs_oral_maths["heure_fin"] == "09:40"

        # La cascade retrouve aussi son horaire d'origine (9h40)
        _, kwargs_oral_cascade = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.UPDATE_INFOS_ORAL and c.kwargs.get("id") == 30
        )
        assert kwargs_oral_cascade["heure_sujet"] == "09:40"
