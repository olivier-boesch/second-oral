"""Tests d'intégration pour la route /gestion/edit-candidat.

Depuis le 2026-07-10, le tiers-temps ne se modifie plus depuis ce formulaire
(la case à cocher a été remplacée par un lien vers la prévisualisation
dédiée) — la logique d'adaptation d'horaires (extension/réduction +
cascade) est couverte par tests/integration/test_declarer_tiers_temps_candidat.py
et tests/unit/test_rebalance.py::TestPlanifierTiersTemps.
"""
import pytest


CANDIDAT_SANS_TT = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0}
CANDIDAT_AVEC_TT = {"id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 1}


def _side_effect(sql, *args):
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
        return [CANDIDAT_SANS_TT]
    return []


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")


class TestEditCandidatSimple:
    def test_edit_nom_numero(self, admin_client, db_mock):
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
        assert kwargs["nom"] == "Nouveau Nom"

    def test_post_preserves_existing_tiers_temps(self, admin_client, db_mock):
        """Le formulaire ne peut plus modifier le tiers-temps : la valeur en
        base (ici déjà posée) doit être reconduite telle quelle."""
        def _side_effect_avec_tt(sql, *args):
            import db_facility_web as dfw
            if sql is dfw.SELECT_CANDIDAT_TIERS_TEMPS:
                return [CANDIDAT_AVEC_TT]
            return []
        db_mock.make_sql_select.side_effect = _side_effect_avec_tt
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100",
        })
        assert r.status_code == 302
        import db_facility_web as dfw
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["tiers_temps"] == 1

    def test_get_unknown_candidat_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.get("/gestion/edit-candidat?id=999")
        assert r.status_code == 404

    def test_post_unknown_candidat_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "999", "nom": "X", "numero": "N999",
        })
        assert r.status_code == 404


class TestEditCandidatTelephone:
    """Numéro de mobile candidat (ajouté 2026-07-09) : modifiable depuis
    /gestion/edit-candidat, admin uniquement (cf. docs/securite.md#rgpd)."""

    def test_post_saves_telephone(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100",
            "telephone": "0612345678",
        })
        assert r.status_code == 302
        import db_facility_web as dfw
        db_mock.make_sql_update.assert_called_once()
        args, kwargs = db_mock.make_sql_update.call_args
        assert args[0] is dfw.UPDATE_CANDIDAT_INFOS
        assert kwargs["telephone"] == "0612345678"

    def test_get_renders_telephone_field(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{
            "id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0,
            "login_key": "k", "telephone": "0612345678",
        }]
        r = admin_client.get("/gestion/edit-candidat?id=100")
        assert r.status_code == 200
        assert "0612345678" in r.data.decode()


class TestEditCandidatMatieres:
    """Fusion vue candidat/oraux (2026-07-09) : la fiche d'édition expose
    aussi les matières actuelles + un lien vers changer_matiere_candidat,
    remplaçant le bouton '🔄 Changer' de l'ancienne liste des candidats."""

    def test_get_renders_matieres_and_change_link(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{
            "id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0,
            "login_key": "k", "telephone": "", "choix1": 1, "choix2": 2,
            "matiere1": "Maths", "matiere2": "Philo",
        }]
        r = admin_client.get("/gestion/edit-candidat?id=100")
        assert r.status_code == 200
        body = r.data.decode()
        assert "Maths" in body
        assert "Philo" in body
        assert "/gestion/candidat/changer-matiere?id_candidat=100" in body


class TestEditCandidatTiersTempsButton:
    """Depuis le 2026-07-10, plus de case à cocher : un lien/bouton renvoie
    vers l'écran de prévisualisation dédié (/gestion/candidat/tiers-temps)."""

    def test_get_renders_declarer_link_when_not_set(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{
            "id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 0,
            "login_key": "k", "telephone": "",
        }]
        r = admin_client.get("/gestion/edit-candidat?id=100")
        body = r.data.decode()
        assert "/gestion/candidat/tiers-temps?id_candidat=100" in body
        assert "⏱️ Déclarer" in body
        assert 'type="checkbox" name="tiers_temps"' not in body

    def test_get_renders_retirer_link_when_already_set(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{
            "id": 100, "nom": "Cand Test", "numero": "N100", "tiers_temps": 1,
            "login_key": "k", "telephone": "",
        }]
        r = admin_client.get("/gestion/edit-candidat?id=100")
        body = r.data.decode()
        assert "⏱️ Retirer" in body

    def test_post_does_not_touch_oral_schedule(self, admin_client, db_mock):
        """Le POST ne doit plus jamais appeler _appliquer_oraux_tiers_temps
        (UPDATE_INFOS_ORAL) : un seul UPDATE_CANDIDAT_INFOS, quoi qu'il arrive."""
        db_mock.make_sql_select.side_effect = _side_effect
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/edit-candidat", data={
            "id": "100", "nom": "Cand Test", "numero": "N100",
        })
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
