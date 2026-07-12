"""Tests d'intégration Flask — archive de fin de session (/gestion/archive)."""

import json
import zipfile
from io import BytesIO


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


