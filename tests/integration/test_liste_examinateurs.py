"""Tests d'intégration Flask — CRUD examinateurs (/gestion/liste-examinateurs,
add/edit-examinateur) et création de loge à la volée (_assurer_loge)."""


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
        """Un POST valide doit insérer l'examinateur (password_hash vide, l'id
        n'étant connu qu'après l'INSERT) puis poser son password_hash (non
        vide, salé par l'identifiant dérivé de cet id) dans un second temps —
        même pattern que _assurer_loge."""
        import app as app_module
        monkeypatch.setattr(app_module, "root_path", str(tmp_path),
                            raising=False)
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)

        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 42
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
        assert len(calls) == 2  # INSERT_EXAMINATEUR (hash vide) puis UPDATE_EXAMINATEUR_PASSWORD
        insert_kwargs = calls[0].kwargs
        assert insert_kwargs.get("password_hash") == ""
        assert insert_kwargs.get("nom") == "Martin Sophie"
        assert insert_kwargs.get("salle") == "B02"
        update_kwargs = calls[1].kwargs
        assert update_kwargs.get("id") == 42
        assert update_kwargs.get("password_hash"), "password_hash doit être non vide"

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
        """Si la loge existe déjà, aucun compte de loge n'est créé — seuls
        l'INSERT de l'examinateur et la pose de son mot de passe (2 appels)."""
        import app as app_module
        (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "root_path", str(tmp_path), raising=False)
        monkeypatch.setattr(app_module.reports, "liste_papillons_connexion",
                            lambda *a, **kw: None)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_update.return_value = 42
        db_mock.make_sql_select.side_effect = [
            [{"id": 1, "nom": "L1"}],  # SELECT_LOGE_BY_NOM('L1') -> déjà existante
            [],                         # SELECT_ALL_EXAMINATEURS_FOR_RENEWAL
        ]
        r = admin_client.post("/gestion/add-examinateur",
                              data={"nom": "Martin Sophie", "salle": "B02",
                                    "matiere": "1", "loge": "L1",
                                    "etablissements": ""})
        assert r.status_code == 302
        assert db_mock.make_sql_update.call_count == 2  # INSERT + pose du mot de passe


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
        """La salle doit renvoyer vers la fiche examinateur en direct, pas vers l'édition."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "identifiant": "examinateur10",
                       "loge": "L1", "matiere": "Maths", "etablissements": "",
                       "nb_oraux": 0}
        db_mock.make_sql_select.return_value = [examinateur]
        r = admin_client.get("/gestion/liste-examinateurs")
        assert r.status_code == 200
        body = r.data.decode()
        assert '<a href="/examinateur/examinateur10" target="_blank">A01</a>' in body

    def test_loge_pointe_vers_la_page_loge(self, admin_client, db_mock):
        """La loge doit aussi renvoyer vers sa fiche en direct (cf. project_liens_admin)."""
        examinateur = {"id": 10, "nom": "Martin Sophie", "salle": "A01",
                       "identifiant": "examinateur10",
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


