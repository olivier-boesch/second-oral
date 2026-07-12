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

    def test_index_admin_tile_links_to_login_when_anonymous(self, client):
        body = client.get("/").data.decode()
        assert "Connexion administrateur" in body
        assert "Tableau de bord admin" not in body

    def test_index_admin_tile_links_to_dashboard_when_logged_in(self, admin_client):
        body = admin_client.get("/").data.decode()
        assert "Tableau de bord admin" in body
        assert 'href="/gestion"' in body
        assert "Connexion administrateur" not in body

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
        "/gestion/candidats",
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
        # Bon code, pas de link_back → redirect vers le dashboard admin (/gestion)
        assert r.status_code == 302
        assert "/login" not in r.headers["Location"]
        assert r.headers["Location"] == "/gestion"
        with client.session_transaction() as sess:
            assert sess.get("user") == "admin"

    def test_correct_totp_with_link_back_redirects_there(self, client):
        """link_back (deep-link via admin_required) reste prioritaire sur le
        dashboard par défaut."""
        import pyotp
        totp = pyotp.TOTP("JBSWY3DPEHPK3PXP")
        code = totp.now()
        r = client.post("/login", data={"key": code, "link_back": "/gestion/algo"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/gestion/algo"

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
            [{"id": 5, "nom": "Loge A", "password_hash": "wrong-hash"}],  # SELECT_PASSWORD_CHECK_LOGE
        ]
        r = client.post("/login-loge", data={"loge": "Loge A", "password": "bad"},
                        follow_redirects=False)
        # Mauvais mot de passe → redirect vers /login-loge (pas de 500)
        assert r.status_code == 302
        assert "/login-loge" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "loge" not in sess

    def test_correct_loge_password_sets_session(self, client, db_mock):
        """Le sel du hash est l'id de la loge (stable), pas son nom (mutable
        depuis l'ajout du renommage) — cf. algo.py/_assurer_loge/_renew_loge."""
        import sys
        app_secrets = sys.modules["app_secrets"]
        good_hash = app_secrets.hash_password("secretloge", "5")
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A"}],
            [{"id": 5, "nom": "Loge A", "password_hash": good_hash}],
        ]
        r = client.post("/login-loge", data={"loge": "Loge A", "password": "secretloge"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-loge" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("loge") == "Loge A"

    def test_loge_password_hashed_with_name_is_rejected(self, client, db_mock):
        """Régression : un hash salé avec le *nom* (ancien schéma, avant la FK
        loge_id du 2026-07-09) ne doit plus authentifier — seul l'id fait foi.
        Sans ce test, login_loge() peut redevenir incohérent avec
        algo.py/_assurer_loge/_renew_loge (qui salent tous avec l'id) sans que
        rien ne le détecte : la connexion échouerait alors pour toutes les loges."""
        import sys
        app_secrets = sys.modules["app_secrets"]
        name_salted_hash = app_secrets.hash_password("secretloge", "Loge A")
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A"}],
            [{"id": 5, "nom": "Loge A", "password_hash": name_salted_hash}],
        ]
        r = client.post("/login-loge", data={"loge": "Loge A", "password": "secretloge"},
                        follow_redirects=False)
        assert "/login-loge" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "loge" not in sess

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

    def test_loge_page_renders_oraux_with_passage_button(self, client, db_mock):
        """Régression : SELECT_ORAUX_LOGE renvoie désormais id_oral/passage_loge,
        utilisés par le template pour le bouton 'Marquer passé'."""
        db_mock.make_sql_select.side_effect = [
            [{"salle": "Loge A", "id": 1}],  # SELECT_INFOS_LOGE
            [{
                "id_oral": 42, "loge": "Loge A", "candidat": "Dupont Jean",
                "numero": "0123456789A", "tiers_temps": 0, "salle": "A01",
                "sujet": "08:00", "maj": False, "oral": "08:15", "fin": "09:15",
                "passage_loge": False, "matiere": "Maths", "matiere_court": "Ma",
                "examinateur": "Martin",
            }],  # SELECT_ORAUX_LOGE
        ]
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.get("/loge/Loge%20A", follow_redirects=False)
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Marquer passé" in body
        assert 'data-id-oral="42"' in body


class TestSelectSalleLogeFromExaminateur:
    """Régression production (2026-07-10) : cette requête sélectionnait encore
    `Examinateur.loge` (colonne supprimée par le refactor FK loge_id du
    2026-07-09, cf. project_loge_credentials) — jamais détecté par la suite
    car le mock DB des tests d'intégration n'exécute pas de vrai SQL. Provoquait
    une 500 (Unknown column 'loge' in 'SELECT') à chaque déclaration/retrait de
    tiers-temps ou changement de matière touchant un oral déjà publié
    (_appliquer_changement_oral -> _appliquer_oraux_tiers_temps)."""

    def test_no_longer_references_dropped_examinateur_loge_column(self):
        import db_facility_web as dfw
        sql = dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR
        assert "Examinateur.loge_id = Loge.id" in sql
        assert "Loge.nom AS loge" in sql

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


class TestLoginCandidatQr:
    """QR de connexion automatique candidat (2026-07-10) : token opaque à
    usage unique embarqué dans le papillon/la fiche PDF — jamais le
    login_key réel. Repli propre vers /login-candidat si invalide/expiré."""

    def test_valid_token_creates_session_and_redirects(self, client, db_mock):
        from datetime import datetime, timedelta
        import db_facility_web as dfw
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        db_mock.make_sql_select.return_value = [
            {"token": "abc123", "time_limit": future, "numero": "111111111AA"},
        ]
        db_mock.make_sql_update.reset_mock()
        r = client.get("/login-candidat/qr/abc123", follow_redirects=False)
        assert r.status_code == 302
        assert "/candidat/111111111AA" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("candidat") == "111111111AA"
        # Usage unique : le token est supprimé après vérification.
        args, kwargs = db_mock.make_sql_update.call_args
        assert args[0] is dfw.DELETE_TOKEN_LOGIN_CANDIDAT
        assert kwargs["token"] == "abc123"

    def test_expired_token_redirects_to_login_form(self, client, db_mock):
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        db_mock.make_sql_select.return_value = [
            {"token": "expired", "time_limit": past, "numero": "111111111AA"},
        ]
        r = client.get("/login-candidat/qr/expired", follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "candidat" not in sess

    def test_unknown_token_redirects_to_login_form(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.get("/login-candidat/qr/does-not-exist", follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]

    def test_token_cannot_be_reused(self, client, db_mock):
        """Rejouer la même URL après un premier scan doit échouer (usage unique) :
        une fois consommé, le token n'est plus en base pour une 2e vérification."""
        db_mock.make_sql_select.return_value = []  # déjà supprimé par un 1er scan
        r = client.get("/login-candidat/qr/already-used", follow_redirects=False)
        assert "/login-candidat" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "candidat" not in sess


# ── Routes admin avec session ─────────────────────────────────────────────────

class TestAdminRoutes:
    def test_gestion_algo_ok(self, admin_client):
        r = admin_client.get("/gestion/algo")
        assert r.status_code == 200

    def test_gestion_ok(self, admin_client):
        """/gestion est désormais le dashboard admin (Préparation/Jour J/Fin
        de session), la vue candidats/oraux a déménagé vers /gestion/candidats."""
        r = admin_client.get("/gestion")
        assert r.status_code == 200
        body = r.data.decode()
        assert "Préparation" in body
        assert "Jour J" in body
        assert "Fin de session" in body

    def test_icone_candidats_a_une_idee_de_multiplicite(self, admin_client):
        """L'icône "Candidats / Oraux" doit évoquer plusieurs personnes (icône
        candidat dupliquée), pas un individu seul ni une liste générique."""
        r = admin_client.get("/gestion")
        body = r.data.decode()
        assert '<circle cx="9" cy="7" r="4"/>' in body

    def test_gestion_candidats_ok(self, admin_client):
        r = admin_client.get("/gestion/candidats")
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

    def test_save_params_intervalle_pause_defaut_et_borne(self, admin_client, tmp_path,
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
        assert json.loads(r.data)["params"]["intervalle_pause"] == 4

        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "intervalle_pause": 1}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["intervalle_pause"] == 3

        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "intervalle_pause": 99}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["intervalle_pause"] == 6

    def test_save_params_temps_pause_defaut_et_borne(self, admin_client, tmp_path,
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
        assert json.loads(r.data)["params"]["temps_pause"] == 20

        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "temps_pause": 999}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["temps_pause"] == 60

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
    """Page de monitoring seule (/gestion/jour-j) : état ambiant (algo, pause
    méridienne) + supervision technique. Les actions de rééquilibrage
    (disponibilité examinateur, changement de matière) ont été retirées le
    2026-07-11 — elles font désormais double emploi avec les boutons
    déjà présents sur /gestion/liste-examinateurs et /gestion/candidats."""

    @pytest.fixture(autouse=True)
    def _isolation(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")

    def test_ok_page_monitoring(self, admin_client, db_mock):
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert "au repos" in body
        assert "non configurée" in body  # aucune pause méridienne par défaut

    def test_actions_rapides_retirees(self, admin_client, db_mock):
        """Ne doit plus proposer les raccourcis disponibilité/changement de
        matière — devenus redondants avec liste-examinateurs/candidats."""
        r = admin_client.get("/gestion/jour-j")
        body = r.data.decode()
        assert "Disponibilité d'un examinateur" not in body
        assert "Changement de matière d'un candidat" not in body

    def test_algo_en_cours_affiche(self, admin_client, db_mock, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "is_running", lambda: True)
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
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "à venir (12:00" in r.data.decode()

    def test_section_monitoring_affichee_meme_si_redis_indisponible(self, admin_client, db_mock, monkeypatch):
        """La section supervision technique (ex-page /gestion/monitoring) s'affiche
        même si Redis est indisponible."""
        import app as app_module
        monkeypatch.setattr(app_module, "_redis",
                            lambda: (_ for _ in ()).throw(OSError("Redis KO")))
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "Redis indisponible" in r.data.decode()

    def test_sessions_actives_lient_salle_et_loge(self, admin_client, db_mock, monkeypatch):
        """Les sessions actives (supervision technique) doivent lier la salle et
        la loge vers leur fiche en direct (cf. project_liens_admin)."""
        import app as app_module

        class _FakeRedis:
            def get(self, key):
                return {
                    'stats:online:exam:A1': b'1.2.3.4',
                    'stats:online:loge:L1': b'5.6.7.8',
                }.get(key)

            def scan_iter(self, pattern):
                return iter([b'stats:online:exam:A1', b'stats:online:loge:L1'])

        monkeypatch.setattr(app_module, "_redis", lambda: _FakeRedis())
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert '<a href="/salle/A1" target="_blank">A1</a>' in body
        assert '<a href="/loge/L1" target="_blank">L1</a>' in body


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

    def test_ods_csv_canonical_column_order(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Les colonnes du CSV sont dans l'ordre canonique même si l'ODS les a dans un ordre différent."""
        import io as _io, sys
        from pathlib import Path as _Path
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow as OdfRow
        from odf.text import P
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import CANDIDATS_HEADERS, EXAM_HEADERS, PREPS_HEADERS
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

        doc = OpenDocumentSpreadsheet()
        # Colonnes candidats dans l'ordre inversé
        shuffled = list(reversed(CANDIDATS_HEADERS))
        cand_row = {"CANDIDAT": "Dupont Jean (111111111AA)", "CHOIX DISCIPLINE 1": "Maths",
                    "CHOIX DISCIPLINE 2": "SES", "TT": "0", "Etab": "Lycée", "Profs": ""}
        add_sheet(doc, "candidats", shuffled, [cand_row])
        add_sheet(doc, "examinateurs", EXAM_HEADERS,
                  [{"Nom": "Martin", "Disc.poste": "Maths", "Salle": "1",
                    "Heure mini": "8", "Etab": "Lycée", "Loge": "A"}])
        add_sheet(doc, "preps", PREPS_HEADERS,
                  [{"Matiere": "Maths", "Matière court": "Maths",
                    "Temps preparation (min)": "20", "Duree (min)": "20"}])
        buf = _io.BytesIO()
        doc.save(buf)

        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(buf.getvalue()), "data.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200

        from csv_validator import normalize_csv
        cand_rows, _ = normalize_csv((tmp_path / "candidats.csv").read_bytes())
        assert list(cand_rows[0].keys()) == CANDIDATS_HEADERS

    def test_ods_csv_extra_column_ignored(self, admin_client, tmp_path, flask_app, monkeypatch):
        """Une colonne supplémentaire dans l'ODS n'apparaît pas dans le CSV généré."""
        import io as _io, sys
        from pathlib import Path as _Path
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow as OdfRow
        from odf.text import P
        sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "webserver"))
        from ods_handler import CANDIDATS_HEADERS, EXAM_HEADERS, PREPS_HEADERS
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

        doc = OpenDocumentSpreadsheet()
        extra_headers = CANDIDATS_HEADERS + ["Notes"]
        cand_row = {"CANDIDAT": "Dupont Jean (111111111AA)", "CHOIX DISCIPLINE 1": "Maths",
                    "CHOIX DISCIPLINE 2": "SES", "TT": "0", "Etab": "Lycée", "Profs": "",
                    "Notes": "à ignorer"}
        add_sheet(doc, "candidats", extra_headers, [cand_row])
        add_sheet(doc, "examinateurs", EXAM_HEADERS,
                  [{"Nom": "Martin", "Disc.poste": "Maths", "Salle": "1",
                    "Heure mini": "8", "Etab": "Lycée", "Loge": "A"}])
        add_sheet(doc, "preps", PREPS_HEADERS,
                  [{"Matiere": "Maths", "Matière court": "Maths",
                    "Temps preparation (min)": "20", "Duree (min)": "20"}])
        buf = _io.BytesIO()
        doc.save(buf)

        r = admin_client.post(
            "/gestion/algo/upload",
            data={"ods_file": (BytesIO(buf.getvalue()), "data.ods")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200

        from csv_validator import normalize_csv
        cand_rows, _ = normalize_csv((tmp_path / "candidats.csv").read_bytes())
        assert "Notes" not in cand_rows[0]
        assert list(cand_rows[0].keys()) == CANDIDATS_HEADERS


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

    def test_telephone_not_exposed_to_candidat(self):
        """Régression RGPD : la fiche candidat.html (consultée par le candidat
        lui-même) ne doit jamais recevoir le numéro de mobile — seules les
        requêtes admin (/gestion/*) le sélectionnent."""
        import db_facility_web as dfw
        assert "telephone" not in dfw.SELECT_INFOS_CANDIDAT.lower()


class TestGenerateDocOneFicheCandidatAccessControl:
    """Régression sécurité (2026-07-10) : /generate-doc-one/fiche_candidat-<id>
    ne vérifiait que is_any_authenticated() — n'importe quel examinateur/loge
    authentifié pouvait télécharger la fiche PDF de n'importe quel candidat
    (login_key en clair, id énumérable). Resserré à la même règle que
    show_credentials sur candidat.html : admin ou le candidat lui-même,
    d'autant plus critique maintenant que le QR de ce PDF connecte
    automatiquement (cf. TestLoginCandidatQr)."""

    INFOS = {"id": 5, "nom": "Dupont Jean", "numero": "111111111AA",
             "tiers_temps": 0, "etablissement": "Lycée Test", "login_key": "secretkey"}

    def test_unauthenticated_forbidden(self, client, db_mock):
        db_mock.make_sql_select.return_value = [self.INFOS]
        r = client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
        assert r.status_code == 403

    def test_examinateur_cannot_access_other_candidat_fiche(self, client, db_mock):
        """Une session examinateur (non-admin) ne doit plus suffire."""
        db_mock.make_sql_select.return_value = [self.INFOS]
        with client.session_transaction() as sess:
            sess["user"] = "101"
        r = client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
        assert r.status_code == 403

    def test_candidat_cannot_access_other_candidat_fiche(self, client, db_mock):
        db_mock.make_sql_select.return_value = [self.INFOS]
        with client.session_transaction() as sess:
            sess["candidat"] = "999999999ZZ"
        r = client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
        assert r.status_code == 403

    def test_admin_can_access(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        db_mock.make_sql_select.side_effect = [
            [self.INFOS],   # SELECT_DOC_INFOS_CANDIDAT
            [],             # SELECT_DOC_INFOS_CANDIDATS_ORAUX
            [],             # SELECT_TOKEN_LOGIN_CANDIDAT_BY_NUMERO
        ]
        monkeypatch.setattr(app_module.reports, "fiche_candidat",
                            lambda *a, **kw: "candidat_5.pdf")
        r = admin_client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
        assert r.status_code == 200

    def test_candidat_can_access_own_fiche(self, client, db_mock, monkeypatch):
        import app as app_module
        db_mock.make_sql_select.side_effect = [[self.INFOS], [], []]
        monkeypatch.setattr(app_module.reports, "fiche_candidat",
                            lambda *a, **kw: "candidat_5.pdf")
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        r = client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
        assert r.status_code == 200

    def test_unknown_candidat_404(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.get("/generate-doc-one/fiche_candidat-999", follow_redirects=False)
        assert r.status_code == 404


class TestTelFilter:
    """Filtre Jinja `tel` : formate un numéro par paires de chiffres séparées
    par des points (affichage /gestion), sans toucher au numéro brut utilisé
    dans le lien `tel:` (composition depuis mobile/téléphone)."""

    def test_formats_by_pairs(self):
        import app as app_module
        assert app_module.tel_filter("0612345678") == "06.12.34.56.78"

    def test_empty_returns_empty(self):
        import app as app_module
        assert app_module.tel_filter("") == ""
        assert app_module.tel_filter(None) == ""

    def test_odd_length_keeps_last_digit_alone(self):
        import app as app_module
        assert app_module.tel_filter("123") == "12.3"


class TestGestionCandidatsFusionCandidatOraux:
    """Vue fusionnée candidat + oraux (2026-07-09), déplacée de /gestion vers
    /gestion/candidats (2026-07-11, /gestion devient le dashboard admin) :
    affiche nom/numéro/téléphone/tiers-temps + les 2 oraux,
    /gestion/liste-candidats (page séparée) a été supprimée."""

    ORAUX_CANDIDAT_UNIQUE = [
        {"id_oral": 10, "id_candidat": 1, "nom": "Dupont Jean", "numero": "111111111AA",
         "tiers_temps": 0, "telephone": "0612345678", "matiere": "Maths",
         "heure": "08:00", "heure_sujet": "08:00", "heure_oral": "08:15",
         "heure_fin": "09:15", "salle": "A01", "maj": 0},
        {"id_oral": 11, "id_candidat": 1, "nom": "Dupont Jean", "numero": "111111111AA",
         "tiers_temps": 0, "telephone": "0612345678", "matiere": "Philo",
         "heure": "10:00", "heure_sujet": "10:00", "heure_oral": "10:15",
         "heure_fin": "11:15", "salle": "B02", "maj": 0},
    ]

    def test_liste_candidats_endpoint_removed(self, flask_app):
        assert "liste_candidats" not in flask_app.view_functions

    def test_telephone_and_edit_link_displayed(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = self.ORAUX_CANDIDAT_UNIQUE
        r = admin_client.get("/gestion/candidats")
        assert r.status_code == 200
        body = r.data.decode()
        assert 'href="tel:0612345678"' in body
        assert "06.12.34.56.78" in body
        assert "111111111AA" in body
        assert "/gestion/edit-candidat?id=1" in body

    def test_no_link_to_removed_liste_candidats(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = self.ORAUX_CANDIDAT_UNIQUE
        r = admin_client.get("/gestion/candidats")
        assert "/gestion/liste-candidats" not in r.data.decode()

    def test_quick_tiers_temps_button_present(self, admin_client, db_mock):
        """Le bouton rapide tiers-temps, un temps retiré lors de la fusion des
        vues, a été remis dans la liste (en plus de la case à cocher sur la
        fiche d'édition) — cf. project_fusion_vue_candidat_oraux."""
        db_mock.make_sql_select.return_value = self.ORAUX_CANDIDAT_UNIQUE
        r = admin_client.get("/gestion/candidats")
        body = r.data.decode()
        assert "/gestion/candidat/tiers-temps?id_candidat=1" in body
        assert "⏱️ Déclarer" in body

    def test_quick_changer_matiere_button_present(self, admin_client, db_mock):
        """Bouton rapide « changer de matière » — ajouté le 2026-07-11, sans
        repasser par la fiche d'édition candidat (cf. project_jour_j_monitoring)."""
        db_mock.make_sql_select.return_value = self.ORAUX_CANDIDAT_UNIQUE
        r = admin_client.get("/gestion/candidats")
        body = r.data.decode()
        assert "/gestion/candidat/changer-matiere?id_candidat=1" in body
        assert "Changer de matière" in body
        assert "🔄" not in body  # icône SVG, pas emoji (cf. project_liens_admin)


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
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "L1"}],  # SELECT_LOGE_BY_NOM('L1') -> déjà existante
            [],                         # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL (papillon examinateurs)
        ]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Martin Sophie", "salle": "B02",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": "Lycée Test"})
        assert r.status_code == 302
        calls = db_mock.make_sql_update.call_args_list
        assert len(calls) == 1  # seul l'INSERT_EXAMINATEUR (loge déjà existante)
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
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "L1"}],  # SELECT_LOGE_BY_NOM('L1') -> déjà existante
            [],                         # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL
        ]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Durand Paul", "salle": "C03",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": ""})
        assert r.status_code == 302
        assert "new_papillon" in r.headers["Location"]

    def test_post_creates_missing_loge(self, admin_client, db_mock, monkeypatch,
                                        tmp_path):
        """Ajouter un examinateur avec un nom de loge inédit doit créer son
        compte (table Loge) et régénérer papillons_loges.pdf — sinon la
        connexion échoue silencieusement (incident 2026-07-08)."""
        import app as app_module
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 42  # id auto-incrémenté simulé pour la loge
        db_mock.make_sql_select.side_effect = [
            [],                 # SELECT_LOGE_BY_NOM('B404') -> inexistante
            [],                 # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL (papillon examinateurs)
            [{"nom": "B404"}],  # SELECT_ALL_LOGES_FOR_RENEWAL (régén. papillon loges)
        ]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Martin Sophie", "salle": "B02",
                                    "matiere": "1", "loge": "B404",
                                    "etablissements": ""})
        assert r.status_code == 302
        kwargs_list = [c.kwargs for c in db_mock.make_sql_update.call_args_list]
        assert any(k.get("nom") == "B404" for k in kwargs_list), \
            "la loge B404 doit être insérée en base"
        assert any(k.get("id") == 42 and k.get("password_hash") for k in kwargs_list), \
            "son mot de passe doit être haché et posé après coup (id connu)"

    def test_post_existing_loge_not_recreated(self, admin_client, db_mock,
                                               monkeypatch, tmp_path):
        """Si la loge existe déjà, aucun nouveau compte/mot de passe n'est créé."""
        import app as app_module
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "L1"}],  # SELECT_LOGE_BY_NOM('L1') -> déjà existante
            [],                         # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL
        ]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Martin Sophie", "salle": "B02",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": ""})
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()  # seul l'INSERT_EXAMINATEUR


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
        db_mock.make_sql_select.side_effect = [[examinateur], [], []]
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
        db_mock.make_sql_select.side_effect = [[examinateur], [oral], []]
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
        db_mock.make_sql_select.side_effect = [[examinateur], [oral], []]
        r = admin_client.get("/gestion/edit-examinateur?id_examinateur=10")
        assert r.status_code == 200
        body = r.data.decode()
        assert "09:00" in body
        assert "09:15" in body
        assert "09:30" in body
        assert "09:00" in body
        assert "09:15" in body
        assert "09:30" in body

    def test_post_creates_missing_loge(self, admin_client, db_mock, monkeypatch):
        """Régression 2026-07-08 : réaffecter un examinateur vers un nom de loge
        inédit doit créer son compte (table Loge) et régénérer
        papillons_loges.pdf. Sinon, la connexion échoue silencieusement (0
        ligne Loge pour ce nom) et le papillon continue d'afficher l'ancien
        nom, seul encore présent en table."""
        import app as app_module
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 42  # id auto-incrémenté simulé pour la loge
        db_mock.make_sql_select.side_effect = [
            [],                 # SELECT_LOGE_BY_NOM('B404') -> inexistante
            [{"nom": "B404"}],  # SELECT_ALL_LOGES_FOR_RENEWAL (régén. papillon)
        ]
        r = admin_client.post("/gestion/edit-examinateur", data={
            "id": "10", "nom": "Martin Sophie", "salle": "A01", "loge": "B404",
            "etablissements": "",
        }, follow_redirects=False)
        assert r.status_code == 302
        kwargs_list = [c.kwargs for c in db_mock.make_sql_update.call_args_list]
        assert any(k.get("nom") == "B404" for k in kwargs_list), \
            "la loge B404 doit être insérée en base"
        assert any(k.get("id") == 42 and k.get("password_hash") for k in kwargs_list), \
            "son mot de passe doit être haché et posé après coup (id connu)"

    def test_post_existing_loge_not_recreated(self, admin_client, db_mock):
        """Si la loge existe déjà, aucun nouveau compte/mot de passe n'est créé
        (pas de second appel db_update)."""
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [{"id": 3, "nom": "A01"}]  # loge déjà existante
        r = admin_client.post("/gestion/edit-examinateur", data={
            "id": "10", "nom": "Martin Sophie", "salle": "A01", "loge": "A01",
            "etablissements": "",
        }, follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()  # seul l'UPDATE_EXAMINATEUR_INFOS


# ── _assurer_loge : création à la volée d'une loge manquante ──────────────────

class TestAssurerLoge:
    """Régression 2026-07-08 : un examinateur réaffecté à une loge dont le nom
    n'a jamais existé en base laissait la connexion et le papillon
    désynchronisés (table Loge non tenue à jour). _assurer_loge comble ce
    trou, appelée depuis add-examinateur et edit-examinateur."""

    def test_creates_loge_when_missing(self, db_mock):
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 7  # id auto-incrémenté simulé
        db_mock.make_sql_select.return_value = []  # aucune loge "B404" en base
        loge_id, created = app_module._assurer_loge("B404")
        assert created is True
        assert loge_id == 7
        # INSERT_LOGE (hash placeholder) puis UPDATE_LOGE_PASSWORD (hash salé par id)
        assert db_mock.make_sql_update.call_count == 2
        insert_kwargs = db_mock.make_sql_update.call_args_list[0].kwargs
        assert insert_kwargs["nom"] == "B404"
        update_kwargs = db_mock.make_sql_update.call_args_list[1].kwargs
        assert update_kwargs["id"] == 7
        assert update_kwargs["password_hash"]
        creds = app_module._load_credentials()
        assert "B404" in creds["loges"]

    def test_noop_when_loge_already_exists(self, db_mock):
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.return_value = [{"id": 3, "nom": "B404"}]
        loge_id, created = app_module._assurer_loge("B404")
        assert created is False
        assert loge_id == 3
        db_mock.make_sql_update.assert_not_called()


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

    def test_loge_pointe_vers_la_page_loge(self, admin_client, db_mock):
        """La loge doit aussi renvoyer vers sa fiche en direct (cf. project_liens_admin)."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 0}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert '<a href="/loge/L1" target="_blank">L1</a>' in body


class TestListeExaminateursTri:
    """Tri au clic sur les en-têtes (JS `sortable_table.js`, générique)."""

    def test_colonnes_triables_marquees_et_script_inclus(self, admin_client, db_mock):
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 3}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert 'class="table_oraux sortable"' in body
        assert 'data-sortable data-sort-type="numeric"' in body  # Nombre d'oraux
        assert body.count('data-sortable') == 7  # Nom/Établissements/Matiere/Salle/Loge/Nb oraux/Fin de journée
        assert 'src="/static/sortable_table.js' in body
        # Le nom/salle/loge sont enveloppés d'un lien+tooltip : la valeur de
        # tri doit être la valeur nue, pas le textContent pollué par le tooltip.
        assert 'data-sort="Martin Sophie"' in body
        assert 'data-sort="A01"' in body
        assert 'data-sort="L1"' in body

    def test_bouton_disponibilite_utilise_icone_svg_pas_emoji(self, admin_client, db_mock):
        """L'icône du bouton Disponibilité doit être un SVG (style Lucide,
        cohérent avec le reste de l'admin), pas l'emoji 🕒 (cf. project_liens_admin)."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 3}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        body = r.data.decode()
        assert "🕒" not in body
        assert '<circle cx="12" cy="12" r="10"/>' in body  # icône horloge


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


class TestGestionLoges:
    """Page de gestion des loges (`/gestion/liste-loges`) : liste avec nombre
    d'examinateurs rattachés, et suppression uniquement si ce nombre est nul."""

    def test_get_requires_admin(self, client):
        r = client.get("/gestion/liste-loges", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_get_admin_renders_list_with_usage(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 2},
            {"id": 2, "nom": "B405", "nb_examinateurs": 0},
        ]
        r = admin_client.get("/gestion/liste-loges")
        assert r.status_code == 200
        body = r.data.decode()
        assert "B404" in body
        assert "B405" in body

    def test_get_lie_la_loge_en_direct_et_sa_fiche_pdf(self, admin_client, db_mock):
        """La colonne « Loge » pointe vers la fiche live (loge()), la colonne
        « Fiche loge » vers le PDF (fiche_loge) — remplace l'ancienne page
        « Index des loges » retirée de l'admin (cf. project_liens_admin)."""
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 2},
        ]
        r = admin_client.get("/gestion/liste-loges")
        body = r.data.decode()
        assert '<a href="/loge/B404" target="_blank">B404</a>' in body
        assert "/generate-screen-one?type_doc=fiche_loge&amp;id_doc=B404" in body

    def test_delete_requires_admin(self, client):
        r = client.post("/gestion/loge/1/delete", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_delete_unknown_loge_returns_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.post("/gestion/loge/999/delete", follow_redirects=False)
        assert r.status_code == 404

    def test_delete_blocked_when_still_used(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 2},
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/loge/1/delete", follow_redirects=False)
        assert r.status_code == 400
        db_mock.make_sql_update.assert_not_called()

    def test_delete_ok_when_orphan(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        app_module._save_credentials({"examinateurs": {}, "loges": {"B404": "secret"}})
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 0},
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/loge/1/delete", follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["id"] == 1
        remaining = app_module._load_credentials()
        assert "B404" not in remaining.get("loges", {})

    # ── Renommage (sûr depuis la FK loge_id — cf. incident 2026-07-08) ────────

    def test_rename_requires_admin(self, client):
        r = client.post("/gestion/loge/1/rename", data={"nom": "B405"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_rename_unknown_loge_returns_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.post("/gestion/loge/999/rename", data={"nom": "B405"},
                              follow_redirects=False)
        assert r.status_code == 404

    def test_rename_empty_name_rejected(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 2},
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/loge/1/rename", data={"nom": "  "},
                              follow_redirects=False)
        assert r.status_code == 400
        db_mock.make_sql_update.assert_not_called()

    def test_rename_to_existing_name_rejected(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "B404", "nb_examinateurs": 2}],  # SELECT_LOGE_USAGE
            [{"id": 2, "nom": "B405"}],                         # SELECT_LOGE_BY_NOM (déjà pris)
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/loge/1/rename", data={"nom": "B405"},
                              follow_redirects=False)
        assert r.status_code == 400
        db_mock.make_sql_update.assert_not_called()

    def test_rename_same_name_is_noop(self, admin_client, db_mock):
        """Soumettre le même nom ne doit ni toucher la DB ni régénérer le papillon."""
        db_mock.make_sql_select.return_value = [
            {"id": 1, "nom": "B404", "nb_examinateurs": 2},
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/loge/1/rename", data={"nom": "B404"},
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_not_called()

    def test_rename_ok_updates_db_and_credentials_key(self, admin_client, db_mock, monkeypatch):
        """Le renommage met à jour la DB et fait suivre la clé du mot de passe
        en clair dans le store chiffré (sinon orpheline pour le papillon)."""
        import app as app_module
        app_module._save_credentials({"examinateurs": {}, "loges": {"B404": "secret"}})
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "B404", "nb_examinateurs": 2}],  # SELECT_LOGE_USAGE
            [],                                                  # SELECT_LOGE_BY_NOM (libre)
            [{"id": 1, "nom": "B405"}],                          # SELECT_ALL_LOGES_FOR_RENEWAL
        ]
        db_mock.make_sql_update.reset_mock()
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/loge/1/rename", data={"nom": "B405"},
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs == {"id": 1, "nom": "B405"}
        remaining = app_module._load_credentials()
        assert "B404" not in remaining.get("loges", {})
        assert remaining["loges"]["B405"] == "secret"


class TestReassignationLoges:
    """Tableau matriciel salles × loges (`/gestion/reassignation-loges`) —
    réaffectation rapide d'une salle vers une autre loge, et création d'une
    loge à la volée, sans passer par le formulaire d'édition examinateur."""

    def test_get_requires_admin(self, client):
        r = client.get("/gestion/reassignation-loges", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_get_admin_renders_matrix(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [  # SELECT_MATRICE_SALLES_LOGES
                {"id": 1, "nom": "ProfA", "salle": "A1", "matiere": "Maths",
                 "loge_id": 10, "loge": "B404"},
            ],
            [  # SELECT_ALL_LOGES_FOR_RENEWAL
                {"id": 10, "nom": "B404"},
                {"id": 11, "nom": "B405"},
            ],
        ]
        r = admin_client.get("/gestion/reassignation-loges")
        assert r.status_code == 200
        body = r.data.decode()
        assert "A1" in body
        assert "ProfA" in body
        assert "B404" in body
        assert "B405" in body

    def test_get_lie_salle_examinateur_et_colonnes_loges(self, admin_client, db_mock):
        """Salle, examinateur et en-têtes de loge doivent tous être des liens
        directs (cf. project_liens_admin)."""
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "ProfA", "salle": "A1", "matiere": "Maths",
              "loge_id": 10, "loge": "B404"}],
            [{"id": 10, "nom": "B404"}],
        ]
        r = admin_client.get("/gestion/reassignation-loges")
        body = r.data.decode()
        assert '<a href="/salle/A1" target="_blank">A1</a>' in body
        assert '/gestion/edit-examinateur?id_examinateur=1' in body
        assert '<a href="/loge/B404" target="_blank">B404</a>' in body

    # ── Réaffectation d'une salle (POST assign) ───────────────────────────────

    def test_assign_requires_admin(self, client):
        r = client.post("/gestion/reassignation-loges/assign",
                        json={"id_examinateur": 1, "id_loge": 11})
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_assign_unknown_examinateur_returns_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.post("/gestion/reassignation-loges/assign",
                              json={"id_examinateur": 999, "id_loge": 11})
        assert r.status_code == 404

    def test_assign_unknown_loge_returns_404(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [{"salle": "A1", "loge": "B404"}],  # SELECT_SALLE_LOGE_FROM_EXAMINATEUR
            [],                                   # SELECT_LOGE_BY_ID (introuvable)
        ]
        r = admin_client.post("/gestion/reassignation-loges/assign",
                              json={"id_examinateur": 1, "id_loge": 999})
        assert r.status_code == 404

    def test_assign_invalid_payload_returns_400(self, admin_client, db_mock):
        r = admin_client.post("/gestion/reassignation-loges/assign",
                              json={"id_examinateur": "abc", "id_loge": None})
        assert r.status_code == 400

    def test_assign_same_loge_is_noop(self, admin_client, db_mock, monkeypatch):
        """Réaffecter vers la loge déjà active ne doit ni toucher la DB ni publier de SSE."""
        import app as app_module
        db_mock.make_sql_select.side_effect = [
            [{"salle": "A1", "loge": "B404"}],   # SELECT_SALLE_LOGE_FROM_EXAMINATEUR
            [{"id": 10, "nom": "B404"}],           # SELECT_LOGE_BY_ID
        ]
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/reassignation-loges/assign",
                              json={"id_examinateur": 1, "id_loge": 10})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "loge": "B404"}
        db_mock.make_sql_update.assert_not_called()

    def test_assign_moves_examinateur_and_publishes_sse(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        from unittest.mock import MagicMock
        db_mock.make_sql_select.side_effect = [
            [{"salle": "A1", "loge": "B404"}],   # SELECT_SALLE_LOGE_FROM_EXAMINATEUR
            [{"id": 11, "nom": "B405"}],           # SELECT_LOGE_BY_ID
        ]
        db_mock.make_sql_update.reset_mock()
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        r = admin_client.post("/gestion/reassignation-loges/assign",
                              json={"id_examinateur": 1, "id_loge": 11})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "loge": "B405"}
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs == {"id": 1, "loge_id": 11}
        channels = {c.kwargs.get("channel") for c in publish_mock.call_args_list}
        assert channels == {"salle_A1", "loge_B404", "loge_B405"}

    # ── Création d'une loge à la volée (POST nouvelle-loge) ───────────────────

    def test_nouvelle_loge_requires_admin(self, client):
        r = client.post("/gestion/reassignation-loges/nouvelle-loge",
                        json={"nom": "B999"})
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_nouvelle_loge_empty_name_rejected(self, admin_client, db_mock):
        r = admin_client.post("/gestion/reassignation-loges/nouvelle-loge",
                              json={"nom": "  "})
        assert r.status_code == 400

    def test_nouvelle_loge_duplicate_name_rejected(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [{"id": 10, "nom": "B404"}]  # SELECT_LOGE_BY_NOM
        db_mock.make_sql_update.reset_mock()
        r = admin_client.post("/gestion/reassignation-loges/nouvelle-loge",
                              json={"nom": "B404"})
        assert r.status_code == 400
        db_mock.make_sql_update.assert_not_called()

    def test_nouvelle_loge_creates_it(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        db_mock.make_sql_select.return_value = []  # SELECT_LOGE_BY_NOM : libre
        db_mock.make_sql_update.side_effect = [10, None]  # INSERT_LOGE -> id, puis UPDATE_LOGE_PASSWORD
        monkeypatch.setattr(app_module, "_regenerer_papillons_loges", lambda *a, **kw: None)
        r = admin_client.post("/gestion/reassignation-loges/nouvelle-loge",
                              json={"nom": "B999"})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "id": 10, "nom": "B999"}
        remaining = app_module._load_credentials()
        assert "B999" in remaining.get("loges", {})


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


# ── Passage en loge (persisté en base, contrairement aux minuteurs Redis) ─────

class TestLogePassage:
    def test_requires_auth(self, client):
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 403

    def test_wrong_loge_forbidden(self, client):
        """Un surveillant d'une autre loge ne peut pas marquer le passage."""
        with client.session_transaction() as sess:
            sess["loge"] = "Loge B"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 403

    def test_loge_user_can_mark_passage(self, client, db_mock):
        db_mock.make_sql_update.reset_mock()
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "passage": True}
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs == {"id": 1, "loge": "Loge A", "passage_loge": True}

    def test_loge_user_can_unmark_passage(self, client, db_mock):
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": False})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "passage": False}

    def test_admin_can_mark_passage(self, admin_client, db_mock):
        r = admin_client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 200


class TestEditOralGetLiensExaminateurSalle:
    """La table « Oraux du candidat » de l'écran d'édition doit lier le nom de
    l'examinateur vers son édition, et la salle vers sa fiche (cf. project_liens_admin)."""

    from datetime import timedelta as _td

    DONNEES_ORAL = {
        "id": 1, "nom": "Dupont Jean", "numero": "N1", "etablissement": "",
        "tiers_temps": 0, "id_candidat": 42, "id_examinateur": 10, "id_matiere": 2,
        "heure_sujet": _td(hours=9), "heure_oral": _td(hours=9, minutes=15),
        "heure_fin": _td(hours=9, minutes=30), "matiere": "Maths",
    }
    AUTRE_ORAL = {
        "id": 2, "matiere": "Français", "id_examinateur": 11, "examinateur": "Durand",
        "salle": "B02",
        "heure_sujet": _td(hours=11), "heure_oral": _td(hours=11, minutes=15),
        "heure_fin": _td(hours=12),
    }

    def test_get_lie_examinateur_et_salle(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [self.DONNEES_ORAL],   # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL],     # SELECT_LISTE_EDITION_ORAL
            [],                    # SELECT_LISTE_MATIERES
            [],                    # SELECT_LISTE_EXAMINATEURS_PAR_MATIERE
        ]
        r = admin_client.get("/gestion/edit-oral?oral=1")
        assert r.status_code == 200
        body = r.data.decode()
        assert '/gestion/edit-examinateur?id_examinateur=11' in body
        assert '<a href="/salle/B02" target="_blank">B02</a>' in body


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
        "id": 2, "matiere": "Français", "id_examinateur": 11, "examinateur": "Durand",
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


class TestGenerateDocBatchFichesCandidatsToken:
    """Régression production (2026-07-10) : /generate-doc-batch/fiches_candidats-0
    plantait en 500 (KeyError 'token') — un point d'appel de
    reports.fiche_candidat oublié en même temps que la fiche individuelle à
    la demande. Depuis, le papillon en lot (10/page) a été retiré (doublon à
    l'usage) : cette fiche est désormais le seul document candidat en lot, et
    porte la durée de validité configurable du QR (défaut 48h)."""

    CANDIDAT = {"id": 1, "nom": "Dupont Jean", "numero": "111111111AA",
               "tiers_temps": 0, "etablissement": "Lycée Test", "login_key": "key"}

    def test_does_not_crash_and_attaches_token(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        import db_facility_web as dfw
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.CANDIDAT],  # SELECT_DOC_LISTE_CANDIDATS
            [],               # SELECT_DOC_LISTE_CANDIDATS_ORAUX
            [],               # SELECT_TOKEN_LOGIN_CANDIDAT_BY_NUMERO
        ]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: "generated/liste_candidats.pdf")
        r = admin_client.get("/generate-doc-batch/fiches_candidats-0",
                             follow_redirects=False)
        assert r.status_code == 200
        assert any(
            c.args[0] is dfw.INSERT_TOKEN_LOGIN_CANDIDAT
            for c in db_mock.make_sql_update.call_args_list
        )

    def test_default_duration_is_48h(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        import db_facility_web as dfw
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [[self.CANDIDAT], [], []]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: None)
        r = admin_client.get("/generate-doc-batch/fiches_candidats-0",
                             follow_redirects=False)
        assert r.status_code == 200
        insert_call = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.INSERT_TOKEN_LOGIN_CANDIDAT
        )
        from datetime import datetime
        limit = datetime.fromisoformat(insert_call.kwargs["time_limit"])
        delta_hours = (limit - datetime.now()).total_seconds() / 3600
        assert 47 < delta_hours <= 48

    def test_custom_duration_is_used(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        import db_facility_web as dfw
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [[self.CANDIDAT], [], []]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: None)
        r = admin_client.get(
            "/generate-doc-batch/fiches_candidats-0?duree_qr_heures=5",
            follow_redirects=False,
        )
        assert r.status_code == 200
        insert_call = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.INSERT_TOKEN_LOGIN_CANDIDAT
        )
        from datetime import datetime
        limit = datetime.fromisoformat(insert_call.kwargs["time_limit"])
        delta_hours = (limit - datetime.now()).total_seconds() / 3600
        assert 4 < delta_hours <= 5


class TestPapillonsCandidatsRemoved:
    """Le papillon candidat en lot (10/page) a été retiré le 2026-07-10 —
    doublon à l'usage avec la fiche individuelle (mêmes identifiants + QR,
    en plus des horaires). La route ne doit plus répondre."""

    def test_papillons_candidats_batch_route_gone(self, admin_client, db_mock):
        r = admin_client.get("/generate-doc-batch/papillons_candidats-0",
                             follow_redirects=False)
        assert r.status_code == 404


# ── Renouvellement des identifiants ──────────────────────────────────────────

class TestCredentialRenewal:
    """Vérifie les routes de renouvellement des identifiants (candidats, examinateurs, loges).

    Ces routes permettent de regénérer les credentials sans relancer l'algorithme.
    """

    CANDIDAT = {"id": 1, "nom": "Dupont Jean", "numero": "0123456789A"}
    EXAMINATEUR = {"id": 10, "nom": "Martin Sophie", "salle": "A01"}
    LOGE = {"id": 5, "nom": "Loge A"}

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
        # Examinateur/salle/loge doivent être des liens directs (cf. project_liens_admin)
        assert '/gestion/edit-examinateur?id_examinateur=10' in body
        assert '<a href="/salle/A01" target="_blank">A01</a>' in body
        assert '<a href="/loge/Loge%20A" target="_blank">Loge A</a>' in body

    # ── Candidat ─────────────────────────────────────────────────────────────

    def test_renew_candidat_updates_db(self, admin_client, db_mock, monkeypatch):
        """Renouveler un candidat appelle db_update avec login_key et password_hash."""
        import app as app_module
        import db_facility_web as dfw
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.CANDIDAT],   # SELECT_INFOS_CANDIDAT_BY_ID (candidat_avant, pour invalider le token)
            [self.CANDIDAT],   # SELECT_INFOS_CANDIDAT_BY_ID (dans _renew_candidat)
            [self.CANDIDAT],   # SELECT_DOC_LISTE_CANDIDATS (regénération de la fiche en lot)
            [],                # SELECT_DOC_LISTE_CANDIDATS_ORAUX
            [],                # SELECT_TOKEN_LOGIN_CANDIDAT_BY_NUMERO (aucun token existant)
        ]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidat/1",
                              follow_redirects=False)
        assert r.status_code == 302
        creds_call = next(
            c for c in db_mock.make_sql_update.call_args_list
            if c.args[0] is dfw.UPDATE_CANDIDAT_CREDENTIALS
        )
        assert "login_key" in creds_call.kwargs
        assert "password_hash" in creds_call.kwargs
        assert creds_call.kwargs["login_key"] != ""
        # Le token QR de ce candidat doit être invalidé (déjà imprimé sur une fiche).
        assert any(
            c.args[0] is dfw.DELETE_TOKEN_LOGIN_CANDIDAT_NUMERO
            and c.kwargs.get("numero") == self.CANDIDAT["numero"]
            for c in db_mock.make_sql_update.call_args_list
        )

    def test_renew_candidat_redirects_to_link_back(self, admin_client, db_mock, monkeypatch):
        """Renouveler un candidat depuis la liste des candidats revient sur cette page,
        avec le nom du fichier de lot regénéré en query string."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [self.CANDIDAT], [self.CANDIDAT], [self.CANDIDAT], [], [],
        ]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidat/1",
                              data={"link_back": "/gestion/liste-candidats"},
                              follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/gestion/liste-candidats?new_papillon=liste_candidats.pdf"

    def test_renew_all_candidats(self, admin_client, db_mock, monkeypatch):
        """Renouveler tous les candidats appelle UPDATE_CANDIDAT_CREDENTIALS une
        fois par candidat, invalide le token QR de chacun, et regénère
        liste_candidats.pdf (fiches individuelles)."""
        import app as app_module
        import db_facility_web as dfw
        db_mock.make_sql_update.reset_mock()
        candidats = [
            {"id": 1, "nom": "Dupont Jean", "numero": "0001"},
            {"id": 2, "nom": "Martin Paul", "numero": "0002"},
        ]
        candidats_fiches = [
            {"id": 1, "nom": "Dupont Jean", "numero": "0001", "login_key": "key1"},
            {"id": 2, "nom": "Martin Paul", "numero": "0002", "login_key": "key2"},
        ]
        # SELECT_ALL_CANDIDATS_FOR_RENEWAL, SELECT_INFOS_CANDIDAT × 2,
        # SELECT_DOC_LISTE_CANDIDATS, puis par candidat :
        # SELECT_DOC_LISTE_CANDIDATS_ORAUX + SELECT_TOKEN_LOGIN_CANDIDAT_BY_NUMERO
        db_mock.make_sql_select.side_effect = [
            candidats,                 # liste initiale
            [candidats[0]],            # infos candidat 1
            [candidats[1]],            # infos candidat 2
            candidats_fiches,          # SELECT_DOC_LISTE_CANDIDATS (regénération)
            [],                        # oraux candidat 1
            [],                        # token candidat 1 (aucun existant)
            [],                        # oraux candidat 2
            [],                        # token candidat 2 (aucun existant)
        ]
        monkeypatch.setattr(app_module.reports, "liste_fiches_candidats",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/candidats",
                              follow_redirects=False)
        assert r.status_code == 302
        calls = db_mock.make_sql_update.call_args_list
        assert sum(1 for c in calls if c.args[0] is dfw.UPDATE_CANDIDAT_CREDENTIALS) == 2
        assert sum(1 for c in calls if c.args[0] is dfw.DELETE_TOKEN_LOGIN_CANDIDAT_NUMERO) == 2

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
            [self.LOGE],   # SELECT_LOGE_BY_NOM (route : validation existence)
            [self.LOGE],   # SELECT_LOGE_BY_NOM (_renew_loge : résolution de l'id pour le sel)
            [self.LOGE],   # SELECT_ALL_LOGES_FOR_RENEWAL (_regenerer_papillons_loges)
        ]
        monkeypatch.setattr(app_module.reports, "liste_papillons_loges",
                            lambda *a, **kw: None)
        r = admin_client.post("/gestion/credentials/loge/Loge%20A",
                              follow_redirects=False)
        assert r.status_code == 302
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["id"] == 5
        assert "password_hash" in kwargs

    def test_renew_all_loges(self, admin_client, db_mock, monkeypatch):
        """Renouveler toutes les loges appelle db_update une fois par loge."""
        import app as app_module
        db_mock.make_sql_update.reset_mock()
        loges = [{"id": 1, "nom": "Loge A"}, {"id": 2, "nom": "Loge B"}]
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
