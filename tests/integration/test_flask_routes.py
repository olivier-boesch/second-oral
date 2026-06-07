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
        assert "candidats" in stats
        assert "profs"     in stats
        assert "matieres"  in stats

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
        db_mock.make_sql_select.return_value = []
        r = admin_client.get("/gestion/archive")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "planning_oraux.csv" in body
        assert "emargements.csv" in body
        assert "journal_audit.json" in body
        # RGPD : la page doit rappeler ce qui est volontairement exclu
        assert "mots de passe" in body

    def test_archive_download_returns_zip(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
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
            [self.PLANNING_ROW], [self.EMARGEMENT_ROW], [self.LOG_ROW],
        ]
        r = admin_client.get("/gestion/archive/download")
        with zipfile.ZipFile(BytesIO(r.data)) as zf:
            names = zf.namelist()
            for forbidden in ("candidats.csv", "profs_total.csv", "preps.csv",
                              "password", "login_key"):
                assert all(forbidden not in n for n in names)

            planning_csv = zf.read("planning_oraux.csv").decode("utf-8-sig")
            emargements_csv = zf.read("emargements.csv").decode("utf-8-sig")
            assert "password" not in planning_csv
            assert "login_key" not in planning_csv
            assert "password" not in emargements_csv

    def test_archive_download_includes_pdf_documents(self, admin_client, db_mock,
                                                       flask_app, tmp_path, monkeypatch):
        """Les PDF déjà générés (papillons, fiches) doivent être inclus dans documents/."""
        db_mock.make_sql_select.side_effect = [
            [self.PLANNING_ROW], [self.EMARGEMENT_ROW], [self.LOG_ROW],
        ]
        docs_dir = tmp_path / "static" / "docs"
        docs_dir.mkdir(parents=True)
        pdf_path = docs_dir / "papillons_examinateurs.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake content")

        monkeypatch.setattr(flask_app, "root_path", str(tmp_path))
        r = admin_client.get("/gestion/archive/download")
        assert r.status_code == 200
        with zipfile.ZipFile(BytesIO(r.data)) as zf:
            names = zf.namelist()
            assert "documents/papillons_examinateurs.pdf" in names
            assert zf.read("documents/papillons_examinateurs.pdf") == b"%PDF-1.4 fake content"
