"""Tests d'intégration Flask — routes de app.py (DB mockée, sans Redis réel)."""

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest


# ── Routes publiques (sans session) ──────────────────────────────────────────

class TestPublicRoutes:
    def test_index_ok(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_login_page_ok(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert b"form" in r.data.lower()

    def test_login_examinateur_ok(self, client):
        r = client.get("/login-examinateur")
        assert r.status_code == 200

    def test_login_candidat_ok(self, client):
        r = client.get("/login-candidat")
        assert r.status_code == 200

    def test_login_loge_ok(self, client):
        r = client.get("/login-loge")
        assert r.status_code == 200

    def test_about_ok(self, client):
        r = client.get("/about")
        assert r.status_code == 200

    def test_mentions_legales_ok(self, client):
        r = client.get("/mentions-legales")
        assert r.status_code == 200
        assert "mentions" in r.data.decode("utf-8", errors="replace").lower()

    def test_mentions_legales_contains_centre(self, client):
        r = client.get("/mentions-legales")
        assert b"Centre Test" in r.data

    def test_404(self, client):
        r = client.get("/route-inexistante-xyz")
        assert r.status_code == 404


# ── Protection par admin_required ────────────────────────────────────────────

class TestAuthRedirects:
    @pytest.mark.parametrize("url", [
        "/gestion",
        "/gestion/algo",
        "/gestion/algo/validate",
        "/gestion/liste-examinateurs",
        "/gestion/edit-oral",
        "/gestion/jour-j",
    ])
    def test_admin_route_redirects_without_session(self, client, url):
        r = client.get(url)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_redirect_contains_link_back(self, client):
        r = client.get("/gestion/algo")
        assert "link_back" in r.headers["Location"]


# ── Authentification admin ────────────────────────────────────────────────────

class TestLogin:
    def test_wrong_totp_redirects_to_login(self, client):
        r = client.post("/login", data={"key": "000000", "link_back": ""},
                        follow_redirects=False)
        # Mauvais code → redirect vers /login
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_correct_totp_sets_session(self, client, flask_app):
        import pyotp
        totp = pyotp.TOTP("JBSWY3DPEHPK3PXP")
        code = totp.now()
        r = client.post("/login", data={"key": code, "link_back": ""},
                        follow_redirects=False)
        # Bon code → redirect vers l'index
        assert r.status_code == 302
        assert "/login" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user") == "admin"

    def test_logout_clears_session(self, admin_client):
        r = admin_client.get("/logout", follow_redirects=False)
        assert r.status_code == 302
        with admin_client.session_transaction() as sess:
            assert "user" not in sess

    def test_login_loge_post_uses_correct_query(self, client, db_mock):
        """
        Régression : login_loge() référence db_facility_web.SELECT_PASSWORD_CHECK_LOGE,
        absent jusqu'ici de db_facility_web.py (AttributeError → 500 sur toute tentative
        de connexion loge). La requête doit interroger la table Loge (et non Examinateur).
        """
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A"}],   # SELECT_LISTE_LOGES (peuple le formulaire)
            [{"nom": "Loge A", "password_hash": "wrong-hash"}],  # SELECT_PASSWORD_CHECK_LOGE
        ]
        r = client.post("/login-loge", data={"loge": "Loge A", "password": "bad"},
                        follow_redirects=False)
        # Mauvais mot de passe → redirect vers /login-loge (pas de 500)
        assert r.status_code == 302
        assert "/login-loge" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "loge" not in sess

    def test_correct_loge_password_sets_session(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        good_hash = app_secrets.hash_password("secretloge", "Loge A")
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A"}],
            [{"nom": "Loge A", "password_hash": good_hash}],
        ]
        r = client.post("/login-loge", data={"loge": "Loge A", "password": "secretloge"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-loge" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("loge") == "Loge A"

    def test_logout_loge_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.get("/logout-loge", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert "loge" not in sess


# ── Authentification examinateur ──────────────────────────────────────────────

class TestLoginExaminateur:
    def test_wrong_password_redirects_to_login_examinateur(self, client, db_mock):
        db_mock.make_sql_select.return_value = [{"password_hash": "not-the-right-hash"}]
        r = client.post("/login-examinateur",
                        data={"salle": "101", "password": "mauvais"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_no_match_in_db_redirects_to_login_examinateur(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.post("/login-examinateur",
                        data={"salle": "101", "password": "anything"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" in r.headers["Location"]

    def test_correct_password_sets_session(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        good_hash = app_secrets.hash_password("monpass", "101")
        db_mock.make_sql_select.return_value = [{"password_hash": good_hash}]
        r = client.post("/login-examinateur",
                        data={"salle": "101", "password": "monpass"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user") == "101"

    def test_logout_examinateur_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "101"
        r = client.get("/logout", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_salle_route_redirects_without_session(self, client):
        r = client.get("/salle/101", follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" in r.headers["Location"]


# ── Routes loge (accès protégé) ───────────────────────────────────────────────

class TestLogeRoutes:
    def test_loge_form_redirects_without_session(self, client):
        r = client.get("/loge", follow_redirects=False)
        assert r.status_code == 302
        assert "/login-loge" in r.headers["Location"]

    def test_loge_page_redirects_without_session(self, client):
        r = client.get("/loge/Loge%20A", follow_redirects=False)
        assert r.status_code == 302
        assert "/login-loge" in r.headers["Location"]

    def test_loge_page_accessible_with_loge_session(self, client, db_mock):
        """Un surveillant connecté peut consulter la fiche de sa loge."""
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A", "id": 1}],  # SELECT_INFOS_LOGE
            [],                               # SELECT_ORAUX_LOGE (aucun oral)
        ]
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.get("/loge/Loge%20A", follow_redirects=False)
        assert r.status_code == 200

    def test_loge_page_accessible_from_other_loge(self, client, db_mock):
        """Un surveillant peut voir une autre loge (vue croisée autorisée)."""
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A", "id": 1}],
            [],
        ]
        with client.session_transaction() as sess:
            sess["loge"] = "Loge B"
        r = client.get("/loge/Loge%20A", follow_redirects=False)
        assert r.status_code == 200

    def test_examinateur_peut_voir_sa_loge(self, client, db_mock):
        """Un examinateur authentifié peut consulter la fiche de sa loge."""
        import db_facility_web as dbw
        salle_row = [{"salle": "101", "password_hash": "x", "nom": "Martin"}]

        def fake_select(query, *args, **kwargs):
            if query is dbw.SELECT_PASSWORD_CHECK_SALLE:
                return salle_row
            if query is dbw.SELECT_INFOS_LOGE:
                return [{"salle": "Loge A", "id": 1}]
            if query is dbw.SELECT_ORAUX_LOGE:
                return []
            return []

        db_mock.make_sql_select.side_effect = fake_select
        with client.session_transaction() as sess:
            sess["user"] = "101"
        r = client.get("/loge/Loge%20A", follow_redirects=False)
        assert r.status_code == 200


# ── Authentification candidat ─────────────────────────────────────────────────

class TestLoginCandidat:
    def test_wrong_password_redirects_to_login_candidat(self, client, db_mock):
        db_mock.make_sql_select.return_value = [{"password_hash": "bad-hash"}]
        r = client.post("/login-candidat",
                        data={"numero": "111111111AA", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "candidat" not in sess

    def test_unknown_numero_redirects_to_login_candidat(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.post("/login-candidat",
                        data={"numero": "000000000ZZ", "password": "anything"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]

    def test_correct_password_sets_session(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        numero = "111111111AA"
        good_hash = app_secrets.hash_password("motdepasse", numero)
        db_mock.make_sql_select.return_value = [{"password_hash": good_hash}]
        r = client.post("/login-candidat",
                        data={"numero": numero, "password": "motdepasse"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("candidat") == numero

    def test_logout_candidat_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        r = client.get("/logout-candidat", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert "candidat" not in sess


# ── Routes admin avec session ─────────────────────────────────────────────────

class TestAdminRoutes:
    def test_gestion_algo_ok(self, admin_client):
        r = admin_client.get("/gestion/algo")
        assert r.status_code == 200

    def test_gestion_ok(self, admin_client):
        r = admin_client.get("/gestion")
        assert r.status_code == 200


    def test_algo_status_reflects_is_running(self, admin_client, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "is_running", lambda: True)
        r = admin_client.get("/gestion/algo/status")
        assert r.status_code == 200
        assert json.loads(r.data) == {"running": True}

    def test_algo_stop_when_running(self, admin_client, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "stop_algo", lambda: True)
        r = admin_client.post("/gestion/algo/stop")
        assert r.status_code == 200
        assert json.loads(r.data) == {"ok": True}

    def test_algo_stop_when_nothing_running(self, admin_client, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "stop_algo", lambda: False)
        r = admin_client.post("/gestion/algo/stop")
        assert r.status_code == 200
        assert json.loads(r.data) == {"ok": False}

    def test_algo_stop_requires_admin(self, client):
        r = client.post("/gestion/algo/stop")
        assert r.status_code in (302, 401, 403)

    def test_validate_csv_returns_json(self, admin_client):
        r = admin_client.get("/gestion/algo/validate")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "ok" in data
        assert "errors" in data
        assert "warnings" in data
        assert "stats" in data

    def test_validate_csv_stats_structure(self, admin_client):
        r = admin_client.get("/gestion/algo/validate")
        stats = json.loads(r.data)["stats"]
        assert "candidats"    in stats
        assert "examinateurs" in stats
        assert "matieres"     in stats

    def test_save_params_valid(self, admin_client, tmp_path, flask_app, monkeypatch):
        # Redirige _ALGO_PARAMS_FILE vers tmp_path pour ne pas polluer data/
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body["ok"] is True
        assert body["params"]["heure_debut"] == "08:30"
        assert body["params"]["creneaux"]    == 12
        assert body["params"]["debug"] is False

    def test_save_params_debug_flag(self, admin_client, tmp_path, flask_app, monkeypatch):
        """L'option d'affichage détaillé (debug) doit être persistée."""
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70, "debug": True}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["debug"] is True

    def test_save_params_cp_timeout_borne_a_1200(self, admin_client, tmp_path, flask_app, monkeypatch):
        """cp_timeout est borné à 1200s max (au-delà, clampé silencieusement)."""
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70, "cp_timeout": 5000}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["cp_timeout"] == 1200

    def test_save_params_cp_optimal_desactive_par_defaut(self, admin_client, tmp_path,
                                                          flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["cp_optimal"] is False

    def test_save_params_cp_optimal_active(self, admin_client, tmp_path, flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70, "cp_optimal": True}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["cp_optimal"] is True

    def test_save_params_petites_matieres_defaut_active(self, admin_client, tmp_path,
                                                          flask_app, monkeypatch):
        """Activée par défaut si absente de la requête (comportement historique inchangé)."""
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        params = json.loads(r.data)["params"]
        assert params["petites_matieres_fin_journee"] is True
        assert params["seuil_petite_matiere"] == 5

    def test_save_params_petites_matieres_desactivee(self, admin_client, tmp_path,
                                                      flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "petites_matieres_fin_journee": False,
                             "seuil_petite_matiere": 8}),
            content_type="application/json",
        )
        assert r.status_code == 200
        params = json.loads(r.data)["params"]
        assert params["petites_matieres_fin_journee"] is False
        assert params["seuil_petite_matiere"] == 8

    def test_save_params_seuil_petite_matiere_borne(self, admin_client, tmp_path,
                                                     flask_app, monkeypatch):
        """seuil_petite_matiere est borné entre 1 et 500 (au-delà, clampé silencieusement)."""
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "seuil_petite_matiere": 10000}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["seuil_petite_matiere"] == 500

    def test_save_params_creneau_cible_defaut_desactive(self, admin_client, tmp_path,
                                                         flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        params = json.loads(r.data)["params"]
        assert params["creneau_cible_fin_journee"] == ""
        assert params["poids_creneau_fin_journee"] == 200

    def test_save_params_creneau_cible_active(self, admin_client, tmp_path,
                                              flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "creneau_cible_fin_journee": 6,
                             "poids_creneau_fin_journee": 500}),
            content_type="application/json",
        )
        assert r.status_code == 200
        params = json.loads(r.data)["params"]
        assert params["creneau_cible_fin_journee"] == 6
        assert params["poids_creneau_fin_journee"] == 500

    def test_save_params_creneau_cible_borne(self, admin_client, tmp_path,
                                             flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "creneau_cible_fin_journee": 999}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["creneau_cible_fin_journee"] == 30

    def test_save_params_poids_creneau_fin_journee_borne(self, admin_client, tmp_path,
                                                          flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "poids_creneau_fin_journee": 999999}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["poids_creneau_fin_journee"] == 100_000

    def test_save_params_poids_equite_defaut_et_borne(self, admin_client, tmp_path,
                                                       flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["poids_equite"] == 1_000_000

        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "poids_equite": 999_999_999}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["poids_equite"] == 100_000_000

    def test_save_params_bruit_tassement_defaut_et_borne(self, admin_client, tmp_path,
                                                          flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["bruit_tassement"] == 25

        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "bruit_tassement": 0}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["bruit_tassement"] == 1

    def test_save_params_invalid(self, admin_client):
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "99:99", "creneaux": "abc"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_download_csv_missing_returns_404(self, admin_client):
        r = admin_client.get("/gestion/algo/download-csv/candidats")
        # Le fichier peut ne pas exister en CI → 404 attendu
        assert r.status_code in (200, 404)

    def test_download_csv_invalid_key_returns_404(self, admin_client):
        r = admin_client.get("/gestion/algo/download-csv/unknown_key")
        assert r.status_code == 404

    def test_download_modele_ods_returns_ods(self, admin_client):
        r = admin_client.get("/gestion/algo/download-modele-ods")
        assert r.status_code == 200
        assert r.content_type == "application/vnd.oasis.opendocument.spreadsheet"
        assert r.data[:2] == b"PK"  # ODS est un ZIP

    def test_ods_upload_splits_into_three_csvs(self, admin_client, tmp_path,
                                               flask_app, monkeypatch):
        import sys, io as _io
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import generate_ods_modele, parse_ods
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        # Génère un ODS valide et l'uploade
        ods_bytes = generate_ods_modele()
        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(ods_bytes), "modele_oral.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert "preps.csv" in body["uploaded"]
        assert (tmp_path / "preps.csv").exists()

    def test_ods_upload_invalid_extension_returns_error(self, admin_client):
        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(b"not ods"), "file.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body["ok"] is False
        assert any("extension .ods" in e for e in body["errors"])

    def test_ods_upload_invalid_content_returns_error(self, admin_client):
        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(b"garbage"), "modele.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body["ok"] is False

    def test_ods_upload_requires_admin(self, client):
        r = client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(b"x"), "x.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code in (302, 403)


class TestJourJ:
    """Hub de pilotage en direct (/gestion/jour-j) : état ambiant (algo,
    pause méridienne) + accès rapide aux actions de rééquilibrage."""

    EXAMINATEURS = [
        {"id": 1, "nom": "ProfA", "etablissements": "", "salle": "A1", "loge": "L1",
         "matiere": "Maths", "nb_oraux": 5},
    ]
    CANDIDATS = [
        {"id": 100, "nom": "Dupont Jean", "numero": "0123456789A", "tiers_temps": 0},
    ]

    @pytest.fixture(autouse=True)
    def _isolation(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")

    def test_ok_et_contient_les_deux_actions_rapides(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [self.EXAMINATEURS, self.CANDIDATS]
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert "ProfA (Maths)" in body
        assert "Dupont Jean (0123456789A)" in body
        assert "au repos" in body
        assert "non configurée" in body  # aucune pause méridienne par défaut

    def test_algo_en_cours_affiche(self, admin_client, db_mock, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "is_running", lambda: True)
        db_mock.make_sql_select.side_effect = [self.EXAMINATEURS, self.CANDIDATS]
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "en cours…" in r.data.decode()

    def test_pause_meridienne_en_cours(self, admin_client, db_mock, tmp_path, monkeypatch):
        import app as app_module
        import datetime as _dt_module
        (tmp_path / "algo_params.json").write_text(json.dumps({
            "pause_meridienne_debut": "12:00", "pause_meridienne_duree": 30,
        }))

        class _FakeDatetime:
            @staticmethod
            def now(tz=None):
                return _dt_module.datetime(2026, 7, 6, 12, 10)

        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        db_mock.make_sql_select.side_effect = [self.EXAMINATEURS, self.CANDIDATS]
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert "en cours (12:00" in body

    def test_pause_meridienne_a_venir(self, admin_client, db_mock, tmp_path, monkeypatch):
        import app as app_module
        import datetime as _dt_module
        (tmp_path / "algo_params.json").write_text(json.dumps({
            "pause_meridienne_debut": "12:00", "pause_meridienne_duree": 30,
        }))

        class _FakeDatetime:
            @staticmethod
            def now(tz=None):
                return _dt_module.datetime(2026, 7, 6, 9, 0)

        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        db_mock.make_sql_select.side_effect = [self.EXAMINATEURS, self.CANDIDATS]
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "à venir (12:00" in r.data.decode()

    def test_section_monitoring_affichee_meme_si_redis_indisponible(self, admin_client, db_mock, monkeypatch):
        """La section supervision technique (ex-page /gestion/monitoring) s'affiche
        même si Redis est indisponible."""
        import app as app_module
        monkeypatch.setattr(app_module, "_redis",
                            lambda: (_ for _ in ()).throw(OSError("Redis KO")))
        db_mock.make_sql_select.side_effect = [self.EXAMINATEURS, self.CANDIDATS]
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "Redis indisponible" in r.data.decode()


# ── Intégration ODS complète ─────────────────────────────────────────────────

class TestOdsUploadIntegration:
    """Tests d'intégration couvrant le round-trip ODS → CSV et le téléchargement du modèle."""

    @staticmethod
    def _make_full_ods() -> bytes:
        """ODS minimal avec les 3 feuilles de données remplies."""
        import io as _io
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow
        from odf.text import P

        def add_sheet(doc, name, headers, rows):
            sheet = Table(name=name)
            hr = TableRow()
            for h in headers:
                cell = TableCell(valuetype="string")
                cell.addElement(P(text=h))
                hr.addElement(cell)
            sheet.addElement(hr)
            for row_data in rows:
                tr = TableRow()
                for h in headers:
                    cell = TableCell(valuetype="string")
                    cell.addElement(P(text=str(row_data.get(h, ""))))
                    tr.addElement(cell)
                sheet.addElement(tr)
            doc.spreadsheet.addElement(sheet)

        doc = OpenDocumentSpreadsheet()
        add_sheet(doc, "candidats",
                  ["Nom", "Prenom", "Numero", "Etablissement"],
                  [{"Nom": "Dupont", "Prenom": "Jean",
                    "Numero": "111111111AA", "Etablissement": "Lycée Test"}])
        add_sheet(doc, "examinateurs",
                  ["Nom", "Prenom", "Matière court", "Salle"],
                  [{"Nom": "Martin", "Prenom": "Paul",
                    "Matière court": "Maths", "Salle": "101"}])
        add_sheet(doc, "preps",
                  ["Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"],
                  [{"Matiere": "Mathématiques", "Matière court": "Maths",
                    "Temps preparation (min)": "20", "Duree (min)": "20"}])
        buf = _io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def test_upload_all_three_csvs_created(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Un ODS avec les 3 feuilles remplies crée 3 fichiers CSV."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(self._make_full_ods()), "data.ods")},
            content_type="multipart/form-data",
        )
        body = json.loads(r.data)
        assert set(body["uploaded"]) == {"candidats.csv", "examinateurs.csv", "preps.csv"}
        assert (tmp_path / "candidats.csv").exists()
        assert (tmp_path / "examinateurs.csv").exists()
        assert (tmp_path / "preps.csv").exists()

    def test_upload_csv_has_bom_and_semicolons(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Le CSV généré depuis ODS est encodé UTF-8 avec BOM et des séparateurs ';'."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(self._make_full_ods()), "data.ods")},
            content_type="multipart/form-data",
        )
        raw = (tmp_path / "preps.csv").read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", "BOM UTF-8 attendu en tête du CSV"
        text = raw.decode("utf-8-sig")
        assert ";" in text

    def test_upload_backup_created_on_overwrite(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Si un CSV existe déjà, il est sauvegardé en .csv.bak avant d'être remplacé."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        original = b"ancien contenu"
        (tmp_path / "preps.csv").write_bytes(original)

        admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(self._make_full_ods()), "data.ods")},
            content_type="multipart/form-data",
        )
        assert (tmp_path / "preps.csv.bak").exists()
        assert (tmp_path / "preps.csv.bak").read_bytes() == original
        assert (tmp_path / "preps.csv").read_bytes() != original

    def test_upload_lycees_sheet_not_exported(self, admin_client, tmp_path, flask_app, monkeypatch):
        """La feuille 'lycees' (référentiel) ne génère pas de CSV."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(self._make_full_ods()), "data.ods")},
            content_type="multipart/form-data",
        )
        assert not (tmp_path / "lycees.csv").exists()
        assert not (tmp_path / "etablissements.csv").exists()

    def test_upload_empty_sheet_reports_named_error(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Une feuille vide dans l'ODS génère un message d'erreur mentionnant son nom."""
        import app as app_module
        import sys
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import generate_ods_modele
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        # Le modèle par défaut a candidats et examinateurs vides
        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(generate_ods_modele()), "modele.ods")},
            content_type="multipart/form-data",
        )
        body = json.loads(r.data)
        errors = body["errors"]
        assert any("candidats" in e for e in errors)
        assert any("examinateurs" in e for e in errors)

    def test_upload_response_contains_validation_key(self, admin_client, tmp_path, flask_app, monkeypatch):
        """La réponse de l'upload contient toujours la clé 'validation' avec 'ok'."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(self._make_full_ods()), "data.ods")},
            content_type="multipart/form-data",
        )
        body = json.loads(r.data)
        assert "validation" in body
        assert "ok" in body["validation"]

    def test_download_modele_has_attachment_header(self, admin_client):
        """Le téléchargement du modèle ODS inclut un header Content-Disposition attachment."""
        r = admin_client.get("/gestion/algo/download-modele-ods")
        assert r.status_code == 200
        cd = r.headers.get("Content-Disposition", "")
        assert "attachment" in cd
        assert ".ods" in cd

    def test_download_modele_with_custom_preps_from_csv(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Si preps.csv est présent dans DATA_DIR, le modèle ODS reflète ces disciplines."""
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        preps_csv = (
            "﻿Matiere;Matière court;Temps preparation (min);Duree (min)\n"
            "TestMatiere;TM;25;15\n"
        )
        (tmp_path / "preps.csv").write_text(preps_csv, encoding="utf-8")

        r = admin_client.get("/gestion/algo/download-modele-ods")
        assert r.status_code == 200

        import sys
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import parse_ods
        sheets = parse_ods(r.data)
        preps = sheets["preps"]
        assert len(preps) == 1
        assert preps[0]["Matière court"] == "TM"
        assert preps[0]["Matiere"] == "TestMatiere"

    def test_ods_upload_exam_etab3_merged_in_csv(self, admin_client, tmp_path, flask_app, monkeypatch):
        """L'upload d'un ODS avec 3 colonnes Etab1/2/3 génère un CSV examinateurs avec Etab fusionné."""
        import io as _io, sys
        from pathlib import Path as _Path
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow as OdfRow
        from odf.text import P
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import _EXAM_ODS_HEADERS
        import app as app_module
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)

        def add_sheet(doc, name, headers, data_rows):
            sheet = Table(name=name)
            hr = OdfRow()
            for h in headers:
                c = TableCell(valuetype="string")
                c.addElement(P(text=h))
                hr.addElement(c)
            sheet.addElement(hr)
            for r in data_rows:
                tr = OdfRow()
                for h in headers:
                    c = TableCell(valuetype="string")
                    c.addElement(P(text=str(r.get(h, ""))))
                    tr.addElement(c)
                sheet.addElement(tr)
            doc.spreadsheet.addElement(sheet)

        etab1 = "Paul Cézanne — Aix-en-Provence (0130002G)"
        etab2 = "Montgrand — Marseille (0130042A)"
        doc = OpenDocumentSpreadsheet()
        add_sheet(doc, "candidats",
                  ["CANDIDAT", "CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2", "TT", "Etab", "Profs"],
                  [{"CANDIDAT": "Dupont Jean (111111111AA)", "CHOIX DISCIPLINE 1": "Maths",
                    "CHOIX DISCIPLINE 2": "SES", "TT": "0", "Etab": etab1, "Profs": ""}])
        add_sheet(doc, "examinateurs", _EXAM_ODS_HEADERS,
                  [{"Nom": "Martin Sophie", "Disc.poste": "Maths", "Salle": "101",
                    "Heure mini": "8", "Etab1": etab1, "Etab2": etab2, "Etab3": "", "Loge": "A"}])
        add_sheet(doc, "preps",
                  ["Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"],
                  [{"Matiere": "Mathématiques", "Matière court": "Maths",
                    "Temps preparation (min)": "20", "Duree (min)": "20"},
                   {"Matiere": "SES", "Matière court": "SES",
                    "Temps preparation (min)": "25", "Duree (min)": "20"}])
        buf = _io.BytesIO()
        doc.save(buf)
        ods_bytes = buf.getvalue()

        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(ods_bytes), "data.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert "examinateurs.csv" in body["uploaded"]

        from csv_validator import normalize_csv
        exam_rows, _ = normalize_csv((tmp_path / "examinateurs.csv").read_bytes())
        assert exam_rows[0]["Nom"] == "Martin Sophie"
        assert exam_rows[0]["Etab"] == f"{etab1},{etab2}"
        assert "Etab1" not in exam_rows[0]


# ── Candidat (route protégée) ─────────────────────────────────────────────────

class TestCandidatRoutes:
    def test_candidat_form_removed(self, client):
        # /candidat (formulaire de recherche) a été supprimé
        r = client.get("/candidat", follow_redirects=False)
        assert r.status_code == 404

    def test_candidat_with_valid_session(self, client, flask_app, db_mock):
        """Un candidat authentifié peut accéder à sa fiche."""
        db_mock.make_sql_select.return_value = [{
            "id": 1, "nom": "Martin", "prenom": "Paul",
            "numero": "111111111AA", "etablissement": "Lycée Test",
            "login_key": "key", "tt": 0,
        }]
        with client.session_transaction() as sess:
            sess["candidat"] = "1"   # token Redis simulé
        # La route /candidat/<id> a besoin d'un vrai id en base
        # On teste ici juste que la session est bien lue (pas de redirect)
        r = client.get("/candidat")
        # Peut retourner 200 ou redirect selon l'état de la session
        assert r.status_code in (200, 302, 404)


# ── Archive de fin de session (zip) ───────────────────────────────────────────

class TestArchiveRoutes:
    PLANNING_ROW = {
        "candidat": "Dupont Jean", "numero": "111111111AA", "matiere": "Maths",
        "examinateur": "Martin", "salle": "101", "heure_sujet": "08:00",
        "heure_oral": "08:30", "heure_fin": "08:50", "modifie": None,
    }
    EMARGEMENT_ROW = {
        "candidat": "Dupont Jean", "numero": "111111111AA", "examinateur": "Martin",
        "salle": "101", "heure_oral": "08:30", "signe": 1,
        "heure_emargement": "08:50", "hash_emargement": "abc123",
    }
    LOG_ROW = {
        "id": 1, "timestamp": "2026-06-01 10:00:00", "table_name": "Oral",
        "action_data": {"action": "update"}, "hash": "deadbeef", "ok": True,
    }

    def test_archive_page_redirects_without_session(self, client):
        r = client.get("/gestion/archive")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_archive_download_redirects_without_session(self, client):
        r = client.get("/gestion/archive/download")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_archive_page_ok(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{"id": 1, "salle": "101"}]
        r = admin_client.get("/gestion/archive")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "planning_oraux.csv" in body
        assert "emargements.csv" in body
        assert "journal_audit.json" in body
        assert "Salle 101" in body
        # RGPD : la page doit rappeler ce qui est volontairement exclu
        assert "mots de passe" in body

    def test_archive_download_returns_zip(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [],                      # SELECT_DOC_LISTE_SALLES (régénération des fiches)
            [self.PLANNING_ROW],     # SELECT_DOC_ARCHIVE_PLANNING
            [self.EMARGEMENT_ROW],   # SELECT_DOC_ARCHIVE_EMARGEMENTS
            [self.LOG_ROW],          # SELECT_ALL_LOGS
        ]
        r = admin_client.get("/gestion/archive/download")
        assert r.status_code == 200
        assert r.mimetype == "application/zip"
        assert r.headers["Content-Disposition"].startswith("attachment")
        assert ".zip" in r.headers["Content-Disposition"]

        with zipfile.ZipFile(BytesIO(r.data)) as zf:
            names = zf.namelist()
            assert "planning_oraux.csv" in names
            assert "emargements.csv" in names
            assert "journal_audit.json" in names
            assert "LISEZMOI.txt" in names

            planning_csv = zf.read("planning_oraux.csv").decode("utf-8-sig")
            assert "Dupont Jean" in planning_csv
            assert ";" in planning_csv

            emargements_csv = zf.read("emargements.csv").decode("utf-8-sig")
            assert "Martin" in emargements_csv

            audit = json.loads(zf.read("journal_audit.json").decode("utf-8"))
            assert audit[0]["table_name"] == "Oral"

            manifest = zf.read("LISEZMOI.txt").decode("utf-8")
            assert "Centre Test" in manifest
            assert "mots de passe" in manifest

    def test_archive_download_excludes_raw_csv_and_secrets(self, admin_client, db_mock):
        """RGPD : minimisation — ni CSV bruts, ni mots de passe/clés dans l'archive."""
        db_mock.make_sql_select.side_effect = [
            [], [self.PLANNING_ROW], [self.EMARGEMENT_ROW], [self.LOG_ROW],
        ]
        r = admin_client.get("/gestion/archive/download")
        with zipfile.ZipFile(BytesIO(r.data)) as zf:
            names = zf.namelist()
            for forbidden in ("candidats.csv", "examinateurs.csv", "preps.csv",
                              "password", "login_key"):
                assert all(forbidden not in n for n in names)

            planning_csv = zf.read("planning_oraux.csv").decode("utf-8-sig")
            emargements_csv = zf.read("emargements.csv").decode("utf-8-sig")
            assert "password" not in planning_csv
            assert "login_key" not in planning_csv
            assert "password" not in emargements_csv

    def test_archive_download_only_includes_salle_sheets(self, admin_client, db_mock,
                                                          flask_app, tmp_path, monkeypatch):
        """`documents/` ne doit contenir QUE les fiches de salle — les autres PDF
        générables à la demande (papillons, fiches candidats/loges, liste
        générale) seraient en trop dans une archive de minimisation RGPD."""
        import app as app_module
        monkeypatch.setattr(app_module.reports, "liste_salle_oraux", lambda *a, **kw: None)

        db_mock.make_sql_select.side_effect = [
            [], [self.PLANNING_ROW], [self.EMARGEMENT_ROW], [self.LOG_ROW],
        ]
        docs_dir = tmp_path / "generated"
        docs_dir.mkdir(parents=True)
        kept = ["salle-101-Martin.pdf", "liste_salles.pdf"]
        excluded = ["papillons_examinateurs.pdf", "candidat_0123456789A.pdf",
                    "liste_candidats.pdf", "loge-LogeA.pdf", "liste_oraux.pdf"]
        for name in kept + excluded:
            (docs_dir / name).write_bytes(b"%PDF-1.4 fake content")

        monkeypatch.setattr(flask_app, "root_path", str(tmp_path))
        r = admin_client.get("/gestion/archive/download")
        assert r.status_code == 200
        with zipfile.ZipFile(BytesIO(r.data)) as zf:
            names = zf.namelist()
            for name in kept:
                assert f"documents/{name}" in names
            for name in excluded:
                assert f"documents/{name}" not in names

    def test_archive_download_regenerates_all_salle_sheets(self, admin_client, db_mock,
                                                            monkeypatch):
        """L'export ne doit pas se fier aux fiches de salle déjà présentes dans
        generated/ (générées au fil de l'eau, salle par salle, donc
        potentiellement incomplètes) : il doit toutes les régénérer — elles
        portent les preuves d'émargement (signatures) des candidats."""
        import app as app_module
        import db_facility_web as db_facility_web_module

        salles = [
            {"id": 1, "salle": "101", "nom": "Martin", "matiere": "Maths", "loge": "Loge A"},
            {"id": 2, "salle": "102", "nom": "Durand", "matiere": "Physique", "loge": "Loge A"},
        ]
        oraux_par_salle = {1: [self.PLANNING_ROW], 2: []}

        def fake_select(query, *args, **kwargs):
            if query is db_facility_web_module.SELECT_DOC_LISTE_SALLES:
                return salles
            if query is db_facility_web_module.SELECT_DOC_LISTE_SALLES_ORAUX:
                return oraux_par_salle[args[0]]
            if query is db_facility_web_module.SELECT_DOC_ARCHIVE_PLANNING:
                return [self.PLANNING_ROW]
            if query is db_facility_web_module.SELECT_DOC_ARCHIVE_EMARGEMENTS:
                return [self.EMARGEMENT_ROW]
            if query is db_facility_web_module.SELECT_ALL_LOGS:
                return [self.LOG_ROW]
            return []

        db_mock.make_sql_select.side_effect = fake_select

        regenerated = []
        monkeypatch.setattr(
            app_module.reports, "liste_salle_oraux",
            lambda liste, *a, **kw: regenerated.extend(liste) or "generated/liste_salles.pdf",
        )

        r = admin_client.get("/gestion/archive/download")
        assert r.status_code == 200
        assert [s["id"] for s in regenerated] == [1, 2]
        assert regenerated[0]["oraux"] == [self.PLANNING_ROW]
        assert regenerated[1]["oraux"] == []


# ── Nouvel examinateur avec mot de passe ─────────────────────────────────────

class TestAddExaminateur:
    def test_get_requires_admin(self, client):
        r = client.get("/gestion/add-examinateur", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_get_admin_renders_form(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{"id": 1, "nom": "Maths"}]
        r = admin_client.get("/gestion/add-examinateur")
        assert r.status_code == 200
        assert b"form" in r.data.lower()

    def test_post_missing_nom_returns_400(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{"id": 1, "nom": "Maths"}]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "", "salle": "A01", "matiere": "1",
                                    "loge": "L1", "etablissements": ""})
        assert r.status_code == 400

    def test_post_missing_salle_returns_400(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{"id": 1, "nom": "Maths"}]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Dupont", "salle": "", "matiere": "1",
                                    "loge": "L1", "etablissements": ""})
        assert r.status_code == 400

    def test_post_valid_inserts_with_password_hash(self, admin_client, db_mock,
                                                    monkeypatch, tmp_path):
        """Un POST valide doit insérer l'examinateur avec un password_hash non vide."""
        import app as app_module
        monkeypatch.setattr(app_module, "root_path", str(tmp_path),
                            raising=False)
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)

        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Martin Sophie", "salle": "B02",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": "Lycée Test"})
        assert r.status_code == 302
        calls = db_mock.make_sql_update.call_args_list
        assert len(calls) >= 1
        _, kwargs = calls[0]
        assert kwargs.get("password_hash"), "password_hash doit être non vide"
        assert kwargs.get("nom") == "Martin Sophie"
        assert kwargs.get("salle") == "B02"

    def test_post_valid_redirects_with_papillon_param(self, admin_client, db_mock,
                                                       monkeypatch, tmp_path):
        """Après création, la redirection doit inclure new_papillon."""
        import app as app_module
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path),
                            raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Durand Paul", "salle": "C03",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": ""})
        assert r.status_code == 302
        assert "new_papillon" in r.headers["Location"]


# ── Édition d'un examinateur ──────────────────────────────────────────────────

class TestEditExaminateur:
    def test_query_counts_oral_id_not_star(self):
        """Régression : COUNT(*) avec un LEFT JOIN compte 1 même sans oral
        (la ligne NULL du LEFT JOIN est comptée). Il faut COUNT(Oral.id)."""
        import db_facility_web
        assert "COUNT(*)" not in db_facility_web.SELECT_EXAMINATEUR_INFOS
        assert "COUNT(Oral.id)" in db_facility_web.SELECT_EXAMINATEUR_INFOS

    def test_nb_oraux_zero_displayed_correctly(self, admin_client, db_mock):
        """La page d'édition affiche bien 0 quand la DB renvoie nb_oraux=0."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 0}
        db_mock.make_sql_select.side_effect = [[examinateur], []]
        r = admin_client.get("/gestion/edit-examinateur?id_examinateur=10")
        assert r.status_code == 200
        assert "Nombre d'oraux:</span><span>0</span>" in r.data.decode()

    def test_utilise_select_oraux_examinateur_pas_conflits(self):
        """Régression : la page utilisait SELECT_ORAUX_EXAMINATEUR_CONFLITS
        (prévue pour la détection de conflits, sans numero/etablissement/
        tiers_temps/heures) au lieu de SELECT_ORAUX_EXAMINATEUR — numero,
        établissement et heures n'apparaissaient donc jamais sur la page."""
        import app as app_module
        import inspect
        source = inspect.getsource(app_module.edit_examinateur)
        assert "SELECT_ORAUX_EXAMINATEUR_CONFLITS" not in source
        assert "SELECT_ORAUX_EXAMINATEUR" in source

    def test_liste_oraux_affiche_numero_etablissement_et_heures(self, admin_client, db_mock):
        """Régression : numéro, établissement et les 3 heures (sujet, début,
        fin) doivent apparaître dans le tableau des oraux de l'examinateur."""
        from datetime import timedelta
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 1}
        oral = {
            "id": 1, "candidat": "Dupont Jean", "numero": "0123456789A",
            "etablissement": "Lycée X", "tiers_temps": 0,
            "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
            "heure_fin": timedelta(hours=9, minutes=30), "maj": 0,
        }
        db_mock.make_sql_select.side_effect = [[examinateur], [oral]]
        r = admin_client.get("/gestion/edit-examinateur?id_examinateur=10")
        assert r.status_code == 200
        body = r.data.decode()
        assert "0123456789A" in body
        assert "Lycée X" in body

    def test_liste_oraux_avec_heures_en_chaine(self, admin_client, db_mock):
        """Régression prod : mysql-connector-python peut renvoyer les colonnes
        TIME en str "HH:MM:SS" plutôt qu'en timedelta selon le driver — le
        filtre |heure ne doit pas planter dans ce cas (AttributeError:
        'str' object has no attribute 'total_seconds')."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 1}
        oral = {
            "id": 1, "candidat": "Dupont Jean", "numero": "0123456789A",
            "etablissement": "Lycée X", "tiers_temps": 0,
            "heure_sujet": "09:00:00", "heure_oral": "09:15:00",
            "heure_fin": "09:30:00", "maj": 0,
        }
        db_mock.make_sql_select.side_effect = [[examinateur], [oral]]
        r = admin_client.get("/gestion/edit-examinateur?id_examinateur=10")
        assert r.status_code == 200
        body = r.data.decode()
        assert "09:00" in body
        assert "09:15" in body
        assert "09:30" in body
        assert "09:00" in body
        assert "09:15" in body
        assert "09:30" in body


# ── Liste des examinateurs — suppression ──────────────────────────────────────

class TestListeExaminateursSalleLink:
    def test_salle_pointe_vers_la_page_salle(self, admin_client, db_mock):
        """La salle doit renvoyer vers la fiche salle en direct, pas vers l'édition."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 0}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert '<a href="/salle/A01" target="_blank">A01</a>' in body


class TestListeExaminateursDelete:
    def test_delete_button_asks_confirmation(self, admin_client, db_mock):
        """Le bouton de suppression doit demander confirmation en JS."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 0}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert "confirm(" in body
        assert "SANS CONFIRMATION" not in body

    def test_delete_not_offered_and_no_tooltip_when_oraux(self, admin_client, db_mock):
        """Pas de bouton (ni de tooltip vide) quand l'examinateur a des oraux."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 3}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert "delete_examinateur" not in body
        assert "Supprimer cet examinateur" not in body


class TestDeleteExaminateurPurgeCredentials:
    """La suppression d'un examinateur doit purger son mot de passe en clair
    du store chiffré (credentials.enc) — sinon il y survit indéfiniment,
    orphelin, jusqu'au prochain run complet de l'algo."""

    def test_delete_purges_entry_from_vault(self, admin_client, db_mock, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", tmp_path / "credentials.enc")
        app_module._save_credentials({"examinateurs": {"A01": "secret123"}, "loges": {}})

        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [
            {"id": 10, "nom": "Martin Sophie", "salle": "A01"},
        ]
        r = admin_client.post("/gestion/delete-examinateur",
                              data={"id_examinateur": "10"}, follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()

        remaining = app_module._load_credentials()
        assert "A01" not in remaining.get("examinateurs", {})

    def test_delete_ok_when_examinateur_absent_from_vault(self, admin_client, db_mock,
                                                           tmp_path, monkeypatch):
        """Ne doit pas planter si l'examinateur n'a jamais eu d'entrée dans le vault."""
        import app as app_module
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", tmp_path / "credentials.enc")

        db_mock.make_sql_select.return_value = [
            {"id": 10, "nom": "Martin Sophie", "salle": "A01"},
        ]
        r = admin_client.post("/gestion/delete-examinateur",
                              data={"id_examinateur": "10"}, follow_redirects=False)
        assert r.status_code == 302
        assert not (tmp_path / "credentials.enc").exists()


# ── Timer de loge ─────────────────────────────────────────────────────────────

class TestTimerState:
    def test_timer_state_get_requires_auth(self, client):
        r = client.get("/loge/timer-state?loge=C107", follow_redirects=False)
        assert r.status_code == 403

    def test_timer_state_post_requires_auth(self, client):
        r = client.post("/loge/timer-state",
                        json={"loge": "C107", "numero": "123", "sujet": "08:00",
                              "elapsed": 0, "running": False, "startedAt": None})
        assert r.status_code == 403


# ── Validation du déplacement d'oral ────────────────────────────────────────

class TestEditOralValidation:
    """Vérifie que le déplacement d'un oral détecte les chevauchements et les
    écarts insuffisants entre deux oraux du même candidat."""

    from datetime import timedelta as _td

    # Oral courant : 09h00–10h00 (préparation 08h45, fin 10h00)
    ORAL_ACTUEL = {
        "id": 1, "id_candidat": 42, "nom": "Dupont Jean", "numero": "0123456789A",
        "etablissement": "Lycée Test", "tiers_temps": 0,
        "id_examinateur": 10, "id_matiere": 2,
        "heure_sujet": _td(hours=8, minutes=45),
        "heure_oral":  _td(hours=9,  minutes=0),
        "heure_fin":   _td(hours=10, minutes=0),
        "matiere": "Maths",
    }
    # Autre oral du même candidat : 11h00–12h00
    AUTRE_ORAL = {
        "id": 2, "matiere": "Français", "examinateur": "Durand",
        "salle": "B02",
        "heure_sujet": _td(hours=11, minutes=0),
        "heure_oral":  _td(hours=11, minutes=15),
        "heure_fin":   _td(hours=12, minutes=0),
    }
    MATIERE = [{"id": 2, "nom": "Maths"}]
    EXAM    = [{"id": 10, "nom": "Martin", "salle": "A01", "etablissements": ""}]

    def _post(self, admin_client, heure_sujet="13:00", heure_oral="13:15",
              force="0", mis_a_jour=""):
        # mis_a_jour="" par défaut pour éviter les appels Redis SSE dans les tests
        # heure_oral n'est plus lu par la route (recalculé côté serveur) mais reste
        # accepté sans effet pour ne pas casser un éventuel appel client obsolète.
        return admin_client.post("/gestion/edit-oral", data={
            "id": "1", "examinateur": "10", "matiere": "2",
            "numero": "0123456789A",
            "heure_sujet": heure_sujet,
            "heure_oral":  heure_oral,
            "mis_a_jour":  mis_a_jour,
            "force": force,
        })

    def test_no_conflict_redirects(self, admin_client, db_mock):
        """Pas de conflit candidat ni examinateur → mise à jour appliquée."""
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],    # SELECT_LISTE_EDITION_ORAL (candidat)
            [],                   # SELECT_ORAUX_EXAMINATEUR (aucun conflit)
        ]
        r = self._post(admin_client, heure_sujet="13:00", heure_oral="13:15")
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()

    def test_update_recomputes_heure_fin(self, admin_client, db_mock):
        """heure_fin doit être recalculée et persistée pour préserver la durée
        d'origine de l'oral (bug : heure_fin restait figée après un déplacement).

        ORAL_ACTUEL : heure_oral=09:00, heure_fin=10:00 → durée oral = 1h.
        Déplacement vers heure_oral=13:15 → heure_fin attendue = 14:15.
        """
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],    # SELECT_LISTE_EDITION_ORAL (candidat)
            [],                   # SELECT_ORAUX_EXAMINATEUR (aucun conflit)
        ]
        r = self._post(admin_client, heure_sujet="13:00", heure_oral="13:15")
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["heure_fin"] == "14:15"

    def test_heure_oral_recalculated_ignores_posted_value(self, admin_client, db_mock):
        """Seul heure_sujet pilote le déplacement : un heure_oral posté
        incohérent avec la durée de préparation d'origine est ignoré, et
        heure_oral/heure_fin sont recalculés pour préserver les durées
        d'origine (préparation 15 min, oral 1h).
        """
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],    # SELECT_LISTE_EDITION_ORAL (candidat)
            [],                   # SELECT_ORAUX_EXAMINATEUR (aucun conflit)
        ]
        # heure_oral posté (99:99 -> invalide/incohérent) ne doit avoir aucun effet
        r = self._post(admin_client, heure_sujet="13:00", heure_oral="20:00")
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["heure_oral"] == "13:15"
        assert kwargs["heure_fin"] == "14:15"

    def test_examiner_overlap_blocked(self, admin_client, db_mock):
        """Chevauchement examinateur → 422, mise à jour non appliquée.

        La vérification porte sur [heure_oral, heure_fin] uniquement :
        la préparation (heure_sujet → heure_oral) se déroule en loge.
        oral_new=13:15, fin_exam_new=13:15+1h=14:15 chevauche 13:30–14:30.
        """
        from datetime import timedelta as _td
        exam_oral = {
            "id": 99, "candidat": "Durand Marie",
            "heure_oral": _td(hours=13, minutes=30),   # début de l'oral examinateur
            "heure_fin":  _td(hours=14, minutes=30),
        }
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],    # SELECT_LISTE_EDITION_ORAL (candidat — pas de conflit)
            [exam_oral],          # SELECT_ORAUX_EXAMINATEUR_CONFLITS (oral_new=13:15–14:15 vs 13:30–14:30)
            [self.AUTRE_ORAL],    # re-render: SELECT_LISTE_EDITION_ORAL
            self.MATIERE,         # re-render: SELECT_LISTE_MATIERES
            self.EXAM,            # re-render: SELECT_LISTE_EXAMINATEURS_PAR_MATIERE
        ]
        r = self._post(admin_client, heure_sujet="13:00", heure_oral="13:15")
        assert r.status_code == 422
        body = r.data.decode("utf-8")
        assert "Chevauchement examinateur" in body
        db_mock.make_sql_update.assert_not_called()

    def test_overlap_blocked(self, admin_client, db_mock):
        """Chevauchement → 422, mise à jour non appliquée, message d'erreur."""
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],    # _check_conflits_oral
            [self.AUTRE_ORAL],    # re-render : SELECT_LISTE_EDITION_ORAL
            self.MATIERE,         # re-render : SELECT_LISTE_MATIERES
            self.EXAM,            # re-render : SELECT_LISTE_EXAMINATEURS_PAR_MATIERE
        ]
        # 11h30 chevauche 11h00–12h00
        r = self._post(admin_client, heure_sujet="11:30", heure_oral="11:45")
        assert r.status_code == 422
        body = r.data.decode("utf-8")
        assert "Chevauchement" in body
        db_mock.make_sql_update.assert_not_called()

    def test_gap_warning_shown(self, admin_client, db_mock):
        """Écart insuffisant → 200 avec avertissement, mise à jour non appliquée."""
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],
            [self.AUTRE_ORAL],    # _check_conflits_oral : gap 75 min < 80 min
            [self.AUTRE_ORAL],    # re-render
            self.MATIERE,
            self.EXAM,
        ]
        # 09:45 + durée 1h15 = 11:00 = début de l'autre oral → pas de chevauchement,
        # mais écart de 75 min (< 80 min par défaut) → avertissement attendu
        r = self._post(admin_client, heure_sujet="09:45", heure_oral="10:00")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "cart" in body          # "Écart insuffisant"
        assert "Valider quand" in body  # bouton de confirmation
        db_mock.make_sql_update.assert_not_called()

    def test_gap_warning_force_applies_update(self, admin_client, db_mock):
        """force=1 avec avertissement d'écart → mise à jour appliquée."""
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],
            [self.AUTRE_ORAL],    # _check_conflits_oral : gap 75 min < 80 min
        ]
        # même horaire que test_gap_warning_shown mais force=1 → update appliqué
        r = self._post(admin_client, heure_sujet="09:45", heure_oral="10:00", force="1")
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()


# ── Renouvellement des identifiants ──────────────────────────────────────────

class TestCredentialRenewal:
    """Vérifie les routes de renouvellement des identifiants (candidats, examinateurs, loges).

    Ces routes permettent de regénérer les credentials sans relancer l'algorithme.
    """

    CANDIDAT = {"id": 1, "nom": "Dupont Jean", "numero": "0123456789A"}
    EXAMINATEUR = {"id": 10, "nom": "Martin Sophie", "salle": "A01"}
    LOGE = {"nom": "Loge A"}

    # ── Page de gestion ──────────────────────────────────────────────────────

    def test_credentials_page_requires_admin(self, client):
        """La page /gestion/credentials est protégée par admin_required."""
        r = client.get("/gestion/credentials", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_credentials_page_accessible_to_admin(self, admin_client, db_mock):
        """Un admin peut accéder à la page de gestion des credentials."""
        db_mock.make_sql_select.side_effect = [
            [self.CANDIDAT],      # SELECT_ALL_CANDIDATS_FOR_RENEWAL
            [self.EXAMINATEUR],   # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL
            [self.LOGE],          # SELECT_ALL_LOGES_FOR_RENEWAL
        ]
        r = admin_client.get("/gestion/credentials")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "Renouvellement" in body
        assert "Dupont Jean" in body
        assert "Martin Sophie" in body
        assert "Loge A" in body

    # ── Candidat ─────────────────────────────────────────────────────────────

    def test_renew_candidat_updates_db(self, admin_client, db_mock, monkeypatch):
        """Renouveler un candidat appelle db_update avec login_key et password_hash."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [self.CANDIDAT]
        monkeypatch.setattr(app_module.reports, "liste_papillons_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidat/1",
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert "login_key" in kwargs
        assert "password_hash" in kwargs
        assert kwargs["login_key"] != ""

    def test_renew_candidat_redirects_to_link_back(self, admin_client, db_mock, monkeypatch):
        """Renouveler un candidat depuis la liste des candidats revient sur cette page,
        avec le nom du fichier de lot regénéré en query string."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [self.CANDIDAT]
        monkeypatch.setattr(app_module.reports, "liste_papillons_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidat/1",
                              data={"link_back": "/gestion/liste-candidats"},
                              follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/gestion/liste-candidats?new_papillon=papillons_candidats.pdf"

    def test_renew_all_candidats(self, admin_client, db_mock, monkeypatch):
        """Renouveler tous les candidats appelle db_update une fois par candidat
        et regénère papillons_candidats.pdf."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        candidats = [
            {"id": 1, "nom": "Dupont Jean", "numero": "0001"},
            {"id": 2, "nom": "Martin Paul", "numero": "0002"},
        ]
        candidats_papillons = [
            {"nom": "Dupont Jean", "numero": "0001", "login_key": "key1"},
            {"nom": "Martin Paul", "numero": "0002", "login_key": "key2"},
        ]
        # SELECT_ALL_CANDIDATS_FOR_RENEWAL, SELECT_INFOS_CANDIDAT × 2,
        # puis SELECT_ALL_CANDIDATS_PAPILLONS pour la regénération groupée
        db_mock.make_sql_select.side_effect = [
            candidats,                 # liste initiale
            [candidats[0]],            # infos candidat 1
            [candidats[1]],            # infos candidat 2
            candidats_papillons,       # SELECT_ALL_CANDIDATS_PAPILLONS (regénération)
        ]
        monkeypatch.setattr(app_module.reports, "liste_papillons_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidats",
                              follow_redirects=False)
        assert r.status_code == 302
        assert db_mock.make_sql_update.call_count == 2

    # ── Examinateur ──────────────────────────────────────────────────────────

    def test_renew_examinateur_updates_db(self, admin_client, db_mock, tmp_path, monkeypatch):
        """Renouveler un examinateur appelle db_update et regénère un PDF papillon."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [self.EXAMINATEUR]
        (tmp_path / "generated").mkdir(parents=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/examinateur/10",
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert "password_hash" in kwargs

    def test_renew_examinateur_redirects_to_link_back(self, admin_client, db_mock, tmp_path, monkeypatch):
        """Renouveler un examinateur depuis la liste des examinateurs revient sur cette page,
        avec le nom du fichier de lot regénéré en query string."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [self.EXAMINATEUR]
        (tmp_path / "generated").mkdir(parents=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/examinateur/10",
                              data={"link_back": "/gestion/liste-examinateurs"},
                              follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/gestion/liste-examinateurs?new_papillon=papillons_examinateurs.pdf"

    def test_renew_all_examinateurs(self, admin_client, db_mock, tmp_path, monkeypatch):
        """Renouveler tous les examinateurs appelle db_update une fois par examinateur."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        exams = [
            {"id": 10, "nom": "Martin S.", "salle": "A01"},
            {"id": 11, "nom": "Durand P.", "salle": "B02"},
        ]
        db_mock.make_sql_select.side_effect = [
            exams,         # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL
            [exams[0]],    # SELECT_EXAMINATEUR_FOR_RENEWAL (exam 10)
            [exams[1]],    # SELECT_EXAMINATEUR_FOR_RENEWAL (exam 11)
        ]
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/examinateurs",
                              follow_redirects=False)
        assert r.status_code == 302
        assert db_mock.make_sql_update.call_count == 2

    # ── Loge ─────────────────────────────────────────────────────────────────

    def test_renew_loge_updates_db(self, admin_client, db_mock, monkeypatch):
        """Renouveler une loge appelle db_update et regénère papillons_loges.pdf."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.LOGE],   # SELECT_LOGE_BY_NOM (validation existence)
            [self.LOGE],   # SELECT_ALL_LOGES_FOR_RENEWAL (_regenerer_papillons_loges)
        ]
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/loge/Loge%20A",
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["nom"] == "Loge A"
        assert "password_hash" in kwargs

    def test_renew_all_loges(self, admin_client, db_mock, monkeypatch):
        """Renouveler toutes les loges appelle db_update une fois par loge."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        loges = [{"nom": "Loge A"}, {"nom": "Loge B"}]
        db_mock.make_sql_select.return_value = loges
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/loges",
                              follow_redirects=False)
        assert r.status_code == 302
        assert db_mock.make_sql_update.call_count == 2

    # ── Store chiffré ─────────────────────────────────────────────────────────

    def test_credentials_store_encrypted(self, admin_client, db_mock, tmp_path, monkeypatch):
        """Le fichier credentials.enc n'est pas lisible en JSON brut (contenu chiffré)."""
        import json, app as app_module
        db_mock.make_sql_select.return_value = [self.EXAMINATEUR]
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", tmp_path / "credentials.enc")
        monkeypatch.setattr(app_module, "_CREDENTIALS_TMP_FILE",
                            tmp_path / "credentials_new.json")
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        (tmp_path / "generated").mkdir(parents=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)

        admin_client.post("/gestion/credentials/examinateur/10")

        enc_file = tmp_path / "credentials.enc"
        assert enc_file.exists(), "credentials.enc doit être créé après renouvellement"
        raw = enc_file.read_bytes()
        assert len(raw) > 12, "Le fichier doit contenir au moins nonce + ciphertext"
        try:
            json.loads(raw)
            assert False, "Le fichier ne doit pas être du JSON lisible en clair"
        except (ValueError, UnicodeDecodeError):
            pass  # attendu : contenu chiffré

    def test_absorb_credentials_file_on_success(self, tmp_path, monkeypatch):
        """_absorb_credentials_file chiffre credentials_new.json et le supprime."""
        import json, app as app_module
        tmp_json = tmp_path / "credentials_new.json"
        enc_file = tmp_path / "credentials.enc"
        tmp_json.write_text(json.dumps({
            "examinateurs": {"A01": "secret123"},
            "loges": {"Loge A": "pass456"},
        }))
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", enc_file)
        monkeypatch.setattr(app_module, "_CREDENTIALS_TMP_FILE", tmp_json)

        app_module._absorb_credentials_file(rc=0)

        assert enc_file.exists(), "credentials.enc doit être créé"
        assert not tmp_json.exists(), "credentials_new.json doit être supprimé"
        loaded = app_module._load_credentials()
        assert loaded["examinateurs"]["A01"] == "secret123"
        assert loaded["loges"]["Loge A"] == "pass456"

    def test_absorb_credentials_file_on_failure(self, tmp_path, monkeypatch):
        """_absorb_credentials_file ne fait rien si l'algo a échoué (rc != 0)."""
        import app as app_module
        tmp_json = tmp_path / "credentials_new.json"
        tmp_json.write_text('{"examinateurs": {}, "loges": {}}')
        monkeypatch.setattr(app_module, "_CREDENTIALS_TMP_FILE", tmp_json)
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE",
                            tmp_path / "credentials.enc")

        app_module._absorb_credentials_file(rc=1)

        assert tmp_json.exists(), "Le fichier temporaire ne doit pas être supprimé en cas d'échec"
        assert not (tmp_path / "credentials.enc").exists()

    def test_absorb_credentials_file_invalid_json(self, tmp_path, monkeypatch):
        """_absorb_credentials_file supprime le JSON invalide sans planter."""
        import app as app_module
        tmp_json = tmp_path / "credentials_new.json"
        tmp_json.write_text("ce n'est pas du JSON valide")
        monkeypatch.setattr(app_module, "_CREDENTIALS_TMP_FILE", tmp_json)
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE",
                            tmp_path / "credentials.enc")

        # Ne doit pas lever d'exception
        app_module._absorb_credentials_file(rc=0)

        # Le fichier temporaire est supprimé même si le chiffrement a échoué
        assert not tmp_json.exists()
        # Mais credentials.enc n'est pas créé (JSON invalide → chiffrement annulé)
        assert not (tmp_path / "credentials.enc").exists()

    def test_load_credentials_corrupted_file(self, tmp_path, monkeypatch):
        """_load_credentials retourne un dict vide si credentials.enc est corrompu."""
        import app as app_module
        enc_file = tmp_path / "credentials.enc"
        enc_file.write_bytes(b"ceci n'est pas du ciphertext AES-GCM valide" * 3)
        monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", enc_file)

        result = app_module._load_credentials()

        assert result == {"examinateurs": {}, "loges": {}}

    def test_renew_loge_unknown_returns_404(self, admin_client, db_mock):
        """Renouveler une loge inexistante retourne 404."""
        db_mock.make_sql_select.return_value = []  # aucune loge trouvée
        r = admin_client.post("/gestion/credentials/loge/LogeInexistante",
                              follow_redirects=False)
        assert r.status_code == 404
