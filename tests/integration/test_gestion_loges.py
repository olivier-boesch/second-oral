"""Tests d'intégration Flask — gestion des loges (/gestion/liste-loges) et
réaffectation rapide salles ↔ loges (/gestion/reassignation-loges)."""


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


