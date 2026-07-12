"""Tests d'intégration Flask — renouvellement des identifiants
(/gestion/credentials) et purge du store chiffré à la suppression."""


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
