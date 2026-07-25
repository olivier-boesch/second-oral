"""Tests d'intégration Flask — algorithme de placement (/gestion/algo) : paramètres, upload CSV/ODS, statut, dashboard admin."""

import json
from io import BytesIO



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

    def test_save_params_heure_cible_defaut_desactivee(self, admin_client, tmp_path,
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
        assert params["heure_cible_fin_journee"] == ""
        assert params["poids_fin_journee"] == 25

    def test_save_params_heure_cible_active(self, admin_client, tmp_path,
                                            flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "heure_cible_fin_journee": "17:30",
                             "poids_fin_journee": 500}),
            content_type="application/json",
        )
        assert r.status_code == 200
        params = json.loads(r.data)["params"]
        assert params["heure_cible_fin_journee"] == "17:30"
        assert params["poids_fin_journee"] == 500

    def test_save_params_heure_cible_normalisee(self, admin_client, tmp_path,
                                                flask_app, monkeypatch):
        """Même normalisation HH:MM que la pause méridienne."""
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "heure_cible_fin_journee": "9:5"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["heure_cible_fin_journee"] == "09:05"

    def test_save_params_heure_cible_invalide_rejetee(self, admin_client, tmp_path,
                                                      flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "heure_cible_fin_journee": "pas une heure"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        assert json.loads(r.data)["reason"] == "invalid_params"

    def test_save_params_poids_fin_journee_borne(self, admin_client, tmp_path,
                                                 flask_app, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE",
                            tmp_path / "algo_params.json")
        monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
        r = admin_client.post(
            "/gestion/algo/params",
            data=json.dumps({"heure_debut": "08:30", "creneaux": 12,
                             "n_run": 500, "ecart_mini": 70,
                             "poids_fin_journee": 999999}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert json.loads(r.data)["params"]["poids_fin_journee"] == 100_000

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


