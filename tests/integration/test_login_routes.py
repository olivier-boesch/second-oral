"""Tests d'intégration Flask — connexion et routes publiques (login admin/examinateur/candidat/loge)."""

import pytest


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
                        data={"identifiant": "examinateur101", "password": "mauvais"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_no_match_in_db_redirects_to_login_examinateur(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = client.post("/login-examinateur",
                        data={"identifiant": "examinateur101", "password": "anything"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" in r.headers["Location"]

    def test_correct_password_sets_session(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        good_hash = app_secrets.hash_password("monpass", "examinateur101")
        db_mock.make_sql_select.return_value = [{"password_hash": good_hash}]
        r = client.post("/login-examinateur",
                        data={"identifiant": "examinateur101", "password": "monpass"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/login-examinateur" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user") == "examinateur101"

    def test_logout_examinateur_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["user"] = "examinateur101"
        r = client.get("/logout", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert "user" not in sess

    def test_salle_route_redirects_without_session(self, client):
        r = client.get("/examinateur/examinateur101", follow_redirects=False)
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
            sess["user"] = "examinateur101"
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


class TestHeaderUserActions:
    """Depuis le 2026-07-13, l'icône utilisateur du header (auth_icon.html)
    renvoie vers la page de l'utilisateur connecté (dashboard/salle/fiche/loge)
    au lieu de /logout — une icône de déconnexion séparée a été ajoutée à sa
    droite pour chaque rôle."""

    def test_admin_header_links_to_dashboard_with_separate_logout(self, admin_client):
        body = admin_client.get("/about").data.decode()
        assert 'class="site-header__user" href="/gestion"' in body
        assert 'class="site-header__logout"' in body
        assert 'href="/logout' in body

    def test_examinateur_header_links_to_salle_with_separate_logout(self, client, db_mock):
        db_mock.make_sql_select.return_value = [{"nom": "Martin"}]
        with client.session_transaction() as sess:
            sess["user"] = "examinateur101"
        body = client.get("/about").data.decode()
        assert 'class="site-header__user" href="/examinateur/examinateur101"' in body
        assert 'class="site-header__logout"' in body
        assert 'href="/logout' in body

    def test_candidat_header_links_to_fiche_with_separate_logout(self, client, db_mock):
        db_mock.make_sql_select.return_value = [{"nom": "Dupont Jean"}]
        with client.session_transaction() as sess:
            sess["candidat"] = "1234567890A"
        body = client.get("/about").data.decode()
        assert 'class="site-header__user" href="/candidat/1234567890A"' in body
        assert 'class="site-header__logout" href="/logout-candidat"' in body

    def test_loge_header_links_to_loge_page_with_separate_logout(self, client):
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        body = client.get("/about").data.decode()
        assert 'class="site-header__user" href="/loge/Loge%20A"' in body
        assert 'class="site-header__logout" href="/logout-loge"' in body


class TestSalleLibrementPartagee:
    """Depuis le 2026-07-13, deux examinateurs peuvent partager la même salle
    (à des horaires différents dans la journée) sans que la connexion, le
    sel du mot de passe ou le canal SSE de l'un n'interfère avec l'autre —
    l'identité (session/connexion) repose sur l'identifiant (examinateurN,
    dérivé de l'id DB), la salle redevenant une simple étiquette affichée."""

    def test_deux_examinateurs_meme_salle_se_connectent_independamment(self, client, db_mock):
        import sys
        app_secrets = sys.modules["app_secrets"]
        hash_1 = app_secrets.hash_password("pass1", "examinateur1")
        hash_2 = app_secrets.hash_password("pass2", "examinateur2")

        db_mock.make_sql_select.return_value = [{"password_hash": hash_1}]
        r1 = client.post("/login-examinateur",
                         data={"identifiant": "examinateur1", "password": "pass1"},
                         follow_redirects=False)
        assert r1.status_code == 302
        assert "/examinateur/examinateur1" in r1.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user") == "examinateur1"

        db_mock.make_sql_select.return_value = [{"password_hash": hash_2}]
        r2 = client.post("/login-examinateur",
                         data={"identifiant": "examinateur2", "password": "pass2"},
                         follow_redirects=False)
        assert r2.status_code == 302
        assert "/examinateur/examinateur2" in r2.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user") == "examinateur2"

    def test_query_selects_by_id_not_by_salle(self):
        """La requête de vérification du mot de passe ne doit plus filtrer par
        salle (non unique) mais par id — sinon deux lignes correspondraient et
        `len(infos) == 1` échouerait systématiquement pour une salle partagée."""
        import db_facility_web as dfw
        assert "WHERE id = %s" in dfw.SELECT_PASSWORD_CHECK_SALLE
        assert "salle" not in dfw.SELECT_PASSWORD_CHECK_SALLE


class TestSalleFormGroupedBySharedSalle:
    """La page /salle (index de toutes les salles) regroupe les examinateurs
    partageant une même salle sous une seule tuile, plutôt que des tuiles
    identiques en apparence mais menant (avant ce correctif) à la même fiche
    ambiguë (cf. project_identifiant_examinateur)."""

    def test_salle_non_partagee_reste_une_tuile_simple(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = [
            {"id": 1, "salle": "B102", "nom": "Leroy", "identifiant": "examinateur1",
             "matiere": "SVT", "heure_debut": None},
        ]
        body = admin_client.get("/salle").data.decode()
        assert '<a href="/examinateur/examinateur1">' in body
        assert "B102" in body and "Leroy" in body

    def test_salle_partagee_affiche_une_tuile_groupee_avec_un_lien_par_occupant(
        self, admin_client, db_mock,
    ):
        from datetime import timedelta
        # Déjà trié par heure_debut (comme le fait la requête SQL réelle) :
        # le mock ne rejoue pas l'ORDER BY, l'ordre fourni ici simule son résultat.
        db_mock.make_sql_select.return_value = [
            {"id": 1, "salle": "B101", "nom": "Dupont", "identifiant": "examinateur1",
             "matiere": "Maths", "heure_debut": timedelta(hours=8)},
            {"id": 2, "salle": "B101", "nom": "Martin", "identifiant": "examinateur2",
             "matiere": "PC", "heure_debut": timedelta(hours=13)},
        ]
        body = admin_client.get("/salle").data.decode()
        assert body.index("Dupont") < body.index("Martin")
        assert '<a href="/examinateur/examinateur1">' in body
        assert '<a href="/examinateur/examinateur2">' in body
        assert body.count("B101") == 1, "un seul numéro de salle affiché, pas une tuile par occupant"

    def test_order_by_includes_heure_debut(self):
        """Vérifie que la requête trie bien les occupants d'une même salle par
        heure de début (MIN(Oral.heure_oral)), NULL (pas encore placé) en dernier."""
        import db_facility_web as dfw
        assert "ORDER BY Examinateur.salle, heure_debut IS NULL, heure_debut" in dfw.SELECT_LISTE_SALLES

