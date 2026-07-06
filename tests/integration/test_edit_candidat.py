"""Tests d'intégration pour la route /gestion/edit-candidat.

Couvre en particulier le branchement de la case « Tiers temps » sur la même
adaptation d'horaires que /gestion/candidat/tiers-temps (extension de
préparation + cascade) quand elle est cochée pour un candidat qui ne l'avait
pas déjà — cf. tests/integration/test_declarer_tiers_temps_candidat.py pour
le détail du calcul, et tests/unit/test_rebalance.py::TestPlanifierTiersTemps
pour la logique elle-même.
"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


def _td(h, m=0):
    return timedelta(hours=h, minutes=m)


CANDIDAT_SANS_TT = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0}
CANDIDAT_AVEC_TT = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 1}

DONNEES_CANDIDAT_SANS_TT = {
    "id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0, "login_key": "k",
}

ORAUX_CANDIDAT = [
    {"id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 1, "examinateur": "ProfMaths",
     "heure_sujet": _td(9), "heure_oral": _td(9, 20), "heure_fin": _td(9, 40)},
    {"id": 20, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 2, "examinateur": "ProfPhilo",
     "heure_sujet": _td(11), "heure_oral": _td(11, 15), "heure_fin": _td(11, 30)},
]

ORAUX_EXAMINATEUR_MATHS = [
    {"id_candidat": 100, "id": 10, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(9), "heure_oral": _td(9, 20), "heure_fin": _td(9, 40), "maj": 0},
]
ORAUX_EXAMINATEUR_PHILO = [
    {"id_candidat": 100, "id": 20, "candidat": "Cand Test", "numero": "N100", "etablissement": "",
     "tiers_temps": 0, "heure_sujet": _td(11), "heure_oral": _td(11, 15), "heure_fin": _td(11, 30), "maj": 0},
]


def _side_effect(sql, *args):
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
        return [CANDIDAT_SANS_TT]
    if sql is dfw.SELECT_ORAUX_CANDIDAT_TIERS_TEMPS:
        return ORAUX_CANDIDAT
    if sql is dfw.SELECT_ORAUX_EXAMINATEUR:
        return ORAUX_EXAMINATEUR_MATHS if args[0] == 1 else ORAUX_EXAMINATEUR_PHILO
    if sql is dfw.SELECT_LISTE_EDITION_ORAL:
        return []
    if sql is dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR:
        return [{"salle": "X", "loge": "LogeX"}]
    return []


def _side_effect_sans_oraux(sql, *args):
    """Candidat sans oral publié (avant tout lancement d'algo)."""
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
        return [CANDIDAT_SANS_TT]
    if sql is dfw.SELECT_ORAUX_CANDIDAT_TIERS_TEMPS:
        return []
    return []


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")


class TestEditCandidatSimple:
    def test_edit_nom_numero_sans_tiers_temps(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Nouveau Nom", "numero": "N100",
        })
        assert r.status_code == 302
        import db_facility_web as dfw
        db_mock.make_sql_update.assert_called_once()
        args, kwargs = db_mock.make_sql_update.call_args
        assert args[0] is dfw.UPDATE_CANDIDAT_INFOS
        assert kwargs["tiers_temps"] == 0


class TestEditCandidatDeclareTiersTemps:
    def test_cocher_declenche_la_cascade(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        db_mock.make_sql_select.side_effect = _side_effect
        db_mock.make_sql_update.reset_mock()

        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100", "tiers_temps": "on",
        })
        assert r.status_code == 302

        import db_facility_web as dfw
        sql_appelees = [c.args[0] for c in db_mock.make_sql_update.call_args_list]
        # 2 UPDATE_INFOS_ORAL (les deux oraux du candidat, pas de cascade ici
        # puisqu'aucun oral suivant n'est mocké) + 1 UPDATE_CANDIDAT_INFOS
        assert sql_appelees.count(dfw.UPDATE_INFOS_ORAL) == 2
        assert dfw.UPDATE_CANDIDAT_INFOS in sql_appelees
        _, kwargs_candidat = next(
            c for c in db_mock.make_sql_update.call_args_list if c.args[0] is dfw.UPDATE_CANDIDAT_INFOS
        )
        assert kwargs_candidat["tiers_temps"] == 1
        assert publish_mock.called

    def test_cocher_sans_oral_publie_pose_juste_le_flag(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_sans_oraux
        db_mock.make_sql_update.reset_mock()

        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100", "tiers_temps": "on",
        })
        assert r.status_code == 302
        import db_facility_web as dfw
        db_mock.make_sql_update.assert_called_once()
        args, kwargs = db_mock.make_sql_update.call_args
        assert args[0] is dfw.UPDATE_CANDIDAT_INFOS
        assert kwargs["tiers_temps"] == 1

    def test_decocher_ne_declenche_pas_la_cascade(self, admin_client, db_mock):
        def _side_effect_deja_tt(sql, *args):
            import db_facility_web as dfw
            if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
                return [CANDIDAT_AVEC_TT]
            return []
        db_mock.make_sql_select.side_effect = _side_effect_deja_tt
        db_mock.make_sql_update.reset_mock()

        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100",
            # case décochée : pas de "tiers_temps" dans le formulaire
        })
        assert r.status_code == 302
        import db_facility_web as dfw
        db_mock.make_sql_update.assert_called_once()
        args, kwargs = db_mock.make_sql_update.call_args
        assert args[0] is dfw.UPDATE_CANDIDAT_INFOS
        assert kwargs["tiers_temps"] == 0

    def test_conflit_bloquant_empeche_toute_mise_a_jour(self, admin_client, db_mock):
        # Écart minimum très faible entre les deux matières -> chevauchement
        # après extension, comme dans TestPlanifierTiersTemps.
        oraux_serres = [
            {"id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
             "id_examinateur": 1, "examinateur": "ProfMaths",
             "heure_sujet": _td(9), "heure_oral": _td(9, 20), "heure_fin": _td(9, 40)},
            {"id": 20, "id_candidat": 100, "numero": "N100", "etablissement": "",
             "id_examinateur": 2, "examinateur": "ProfPhilo",
             "heure_sujet": _td(9, 42), "heure_oral": _td(9, 57), "heure_fin": _td(10, 12)},
        ]

        def _side_effect_serre(sql, *args):
            import db_facility_web as dfw
            if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
                return [CANDIDAT_SANS_TT]
            if sql is dfw.SELECT_ORAUX_CANDIDAT_TIERS_TEMPS:
                return oraux_serres
            if sql is dfw.SELECT_ORAUX_EXAMINATEUR:
                return []
            return []

        db_mock.make_sql_select.side_effect = _side_effect_serre
        db_mock.make_sql_update.reset_mock()

        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100", "tiers_temps": "on",
        })
        assert r.status_code == 400
        assert "chevauche" in r.data.decode().lower()
        db_mock.make_sql_update.assert_not_called()
