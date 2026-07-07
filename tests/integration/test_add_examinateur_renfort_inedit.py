"""Tests d'intégration pour le « renfort inédit » : lors de l'ajout d'un
nouvel examinateur (avec une matière), une suggestion apparaît sur
/gestion/credentials pour rééquilibrer dès maintenant vers lui les oraux déjà
en cours pour cette matière — le lien pré-remplit simplement le formulaire
existant de /gestion/examinateur/disponibilite (aucune nouvelle logique de
rééquilibrage : cf. tests/unit/test_rebalance.py et
tests/integration/test_disponibilite_examinateur.py).
"""
import re

import pytest


EXAMINATEUR_MATIERE = {"id": 1, "nom": "ProfA", "id_matiere": 5, "matiere": "Maths"}


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")
    monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", tmp_path / "credentials.enc")
    (tmp_path / "generated").mkdir(parents=True)
    monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
    monkeypatch.setattr(app_module.reports, "liste_papillons_connexion", lambda *a, **kw: None)


def _side_effect_ajout(sql, *args):
    import db_facility_web as dfw
    if sql is dfw.SELECT_LISTE_MATIERES:
        return [{"id": 5, "nom": "Maths"}]
    if sql is dfw.SELECT_ALL_EXAMINATEURS_FOR_RENEWAL:
        return [{"id": 42, "nom": "Nouveau Prof", "salle": "Z9"}]
    return []


class TestAjoutExaminateurSuggestionRenfort:
    def _post(self, admin_client, db_mock, matiere="5"):
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 42  # id auto-incrémenté simulé
        db_mock.make_sql_select.side_effect = _side_effect_ajout
        data = {"nom": "Nouveau Prof", "salle": "Z9"}
        if matiere:
            data["matiere"] = matiere
        return admin_client.post("/gestion/add-examinateur", data=data, follow_redirects=False)

    def test_avec_matiere_redirige_avec_suggestion(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock, matiere="5")
        assert r.status_code == 302
        assert "nouvel_examinateur_id=42" in r.headers["Location"]

    def test_sans_matiere_ne_suggere_rien(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock, matiere="")
        assert r.status_code == 302
        assert "nouvel_examinateur_id" not in r.headers["Location"]


class TestGestionCredentialsBandeauRenfort:
    def test_affiche_le_bandeau_si_id_connu(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_ajout
        r = admin_client.get("/gestion/credentials?nouvel_examinateur_id=42")
        assert r.status_code == 200
        body = r.data.decode()
        assert "Nouveau Prof" in body
        assert "Configurer le renfort" in body
        assert "/gestion/examinateur/disponibilite?id_examinateur=42&amp;renfort=1" in body

    def test_aucun_bandeau_si_id_inconnu(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_ajout
        r = admin_client.get("/gestion/credentials?nouvel_examinateur_id=999")
        assert r.status_code == 200
        assert "Configurer le renfort" not in r.data.decode()

    def test_aucun_bandeau_sans_parametre(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_ajout
        r = admin_client.get("/gestion/credentials")
        assert r.status_code == 200
        assert "Configurer le renfort" not in r.data.decode()


class TestDisponibilitePrefillRenfort:
    def test_renfort_1_prefill_une_heure(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [[EXAMINATEUR_MATIERE]]
        r = admin_client.get("/gestion/examinateur/disponibilite?id_examinateur=1&renfort=1")
        assert r.status_code == 200
        body = r.data.decode()
        match = re.search(
            r'name="disponible_a_nouveau_a_partir_de"[^>]*value="(\d{2}:\d{2})"', body,
        )
        assert match is not None
        assert match.group(1) != ""

    def test_sans_renfort_aucun_prefill(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [[EXAMINATEUR_MATIERE]]
        r = admin_client.get("/gestion/examinateur/disponibilite?id_examinateur=1")
        assert r.status_code == 200
        body = r.data.decode()
        assert 'name="disponible_a_nouveau_a_partir_de" id="disponible_a_nouveau_a_partir_de"\n           value=""' in body
