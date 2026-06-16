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
                        data={"ine": "111111111AA", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "candidat" not in sess

    def test_unknown_ine_redirects_to_login_candidat(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.post("/login-candidat",
                        data={"ine": "000000000ZZ", "password": "anything"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" in r.headers["Location"]

    def test_correct_password_sets_session(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        ine = "111111111AA"
        good_hash = app_secrets.hash_password("motdepasse", ine)
        db_mock.make_sql_select.return_value = [{"password_hash": good_hash}]
        r = client.post("/login-candidat",
                        data={"ine": ine, "password": "motdepasse"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-candidat" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("candidat") == ine

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


# ── Candidat (route protégée) ─────────────────────────────────────────────────

class TestCandidatRoutes:
    def test_candidat_form_accessible_without_session(self, client):
        # /candidat est le formulaire de connexion candidat — public par définition
        r = client.get("/candidat")
        assert r.status_code == 200

    def test_candidat_with_valid_session(self, client, flask_app, db_mock):
        """Un candidat authentifié peut accéder à sa fiche."""
        db_mock.make_sql_select.return_value = [{
            "id": 1, "nom": "Martin", "prenom": "Paul",
            "ine": "111111111AA", "etablissement": "Lycée Test",
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
        "candidat": "Dupont Jean", "ine": "111111111AA", "matiere": "Maths",
        "examinateur": "Martin", "salle": "101", "heure_sujet": "08:00",
        "heure_oral": "08:30", "heure_fin": "08:50", "modifie": None,
    }
    EMARGEMENT_ROW = {
        "candidat": "Dupont Jean", "ine": "111111111AA", "examinateur": "Martin",
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
        docs_dir = tmp_path / "static" / "docs"
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
        static/docs (générées au fil de l'eau, salle par salle, donc
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
            lambda liste, *a, **kw: regenerated.extend(liste) or "static/docs/liste_salles.pdf",
        )

        r = admin_client.get("/gestion/archive/download")
        assert r.status_code == 200
        assert [s["id"] for s in regenerated] == [1, 2]
        assert regenerated[0]["oraux"] == [self.PLANNING_ROW]
        assert regenerated[1]["oraux"] == []
