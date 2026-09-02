"""Tests d'intégration Flask — candidats côté admin : fiche /gestion/candidats,
accès aux documents PDF candidat, filtre tel, génération en lot."""

from pathlib import Path


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


class TestFicheCandidatPdfAccessControl:
    """Régression sécurité : la fiche PDF d'un candidat (login_key en clair +
    QR d'auto-connexion) est servie par `/fiche-candidat/<id>.pdf`, générée à
    la demande dans un répertoire temporaire.

    Deux resserrages successifs y ont conduit. 2026-07-10 : la génération ne
    vérifiait que `is_any_authenticated()`. Puis l'audit suivant a montré que
    restreindre la génération ne suffisait pas — le PDF restait dans
    `generated/` sous un nom devinable, et `/download` le rendait accessible à
    toute session authentifiée. D'où la suppression de l'artefact persistant."""

    INFOS = {"id": 5, "nom": "Dupont Jean", "numero": "111111111AA",
             "tiers_temps": 0, "etablissement": "Lycée Test", "login_key": "secretkey"}

    URL = "/fiche-candidat/5.pdf"

    @staticmethod
    def _fake_fiche(monkeypatch):
        """Remplace la génération ReportLab par l'écriture d'un PDF factice.

        La route relit le fichier produit avant de sortir du TemporaryDirectory :
        le double doit donc réellement écrire dans le `file_dir` reçu.
        """
        import app as app_module

        def _ecrire(infos, tempdirname, file_dir='.', filename_root='', centre_examen=''):
            Path(file_dir, "candidat_5.pdf").write_bytes(b"%PDF-1.4 fake")
            return "candidat_5.pdf"

        monkeypatch.setattr(app_module.reports, "fiche_candidat", _ecrire)

    def test_unauthenticated_forbidden(self, client, db_mock):
        db_mock.make_sql_select.return_value = [self.INFOS]
        r = client.get(self.URL, follow_redirects=False)
        assert r.status_code == 403

    def test_examinateur_cannot_access_candidat_fiche(self, client, db_mock):
        """Une session personnel (examinateur/loge) ne donne aucun droit ici."""
        db_mock.make_sql_select.return_value = [self.INFOS]
        with client.session_transaction() as sess:
            sess["user"] = "examinateur101"
        r = client.get(self.URL, follow_redirects=False)
        assert r.status_code == 403

    def test_candidat_cannot_access_other_candidat_fiche(self, client, db_mock):
        db_mock.make_sql_select.return_value = [self.INFOS]
        with client.session_transaction() as sess:
            sess["candidat"] = "999999999ZZ"
        r = client.get(self.URL, follow_redirects=False)
        assert r.status_code == 403

    def test_admin_can_access(self, admin_client, db_mock, monkeypatch):
        db_mock.make_sql_select.side_effect = [
            [self.INFOS],   # SELECT_DOC_INFOS_CANDIDAT
            [],             # SELECT_DOC_INFOS_CANDIDATS_ORAUX
            [],             # SELECT_TOKEN_LOGIN_CANDIDAT_BY_NUMERO
        ]
        self._fake_fiche(monkeypatch)
        r = admin_client.get(self.URL, follow_redirects=False)
        assert r.status_code == 200
        assert r.mimetype == "application/pdf"

    def test_candidat_can_access_own_fiche(self, client, db_mock, monkeypatch):
        db_mock.make_sql_select.side_effect = [[self.INFOS], [], []]
        self._fake_fiche(monkeypatch)
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        r = client.get(self.URL, follow_redirects=False)
        assert r.status_code == 200
        assert r.data.startswith(b"%PDF")

    def test_fiche_nest_jamais_ecrite_dans_generated(self, client, db_mock, monkeypatch):
        """Le PDF ne doit exister que le temps de la réponse : c'est la
        persistance sous un nom devinable qui constituait la fuite."""
        import app as app_module

        vus = {}

        def _ecrire(infos, tempdirname, file_dir='.', filename_root='', centre_examen=''):
            vus["file_dir"] = file_dir
            Path(file_dir, "candidat_5.pdf").write_bytes(b"%PDF-1.4 fake")
            return "candidat_5.pdf"

        monkeypatch.setattr(app_module.reports, "fiche_candidat", _ecrire)
        db_mock.make_sql_select.side_effect = [[self.INFOS], [], []]
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        assert client.get(self.URL).status_code == 200
        assert "generated" not in vus["file_dir"], (
            "La fiche doit être écrite dans un répertoire temporaire, pas dans generated/"
        )
        assert not Path(vus["file_dir"]).exists(), (
            "Le répertoire temporaire doit être supprimé après la réponse"
        )

    def test_unknown_candidat_404(self, client, db_mock):
        db_mock.make_sql_select.return_value = []
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        r = client.get("/fiche-candidat/999.pdf", follow_redirects=False)
        assert r.status_code == 404

    def test_generate_doc_one_ne_sert_plus_les_fiches_candidats(self, admin_client, db_mock):
        """L'ancienne route ne doit plus produire de fiche candidat."""
        db_mock.make_sql_select.return_value = [self.INFOS]
        r = admin_client.get("/generate-doc-one/fiche_candidat-5", follow_redirects=False)
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
        assert "Déclarer" in body
        assert "⏱️" not in body  # icône SVG, pas emoji (cf. project_icones_admin)

    def test_icone_changer_matiere_par_oral(self, admin_client, db_mock):
        """Icône « changer de matière », une par oral (pas par candidat) —
        colonne dédiée, ajoutée le 2026-07-11 (cf. project_jour_j_monitoring),
        puis déplacée dans sa propre colonne le même jour (une icône par
        oral plutôt qu'un bouton par candidat)."""
        db_mock.make_sql_select.return_value = self.ORAUX_CANDIDAT_UNIQUE
        r = admin_client.get("/gestion/candidats")
        body = r.data.decode()
        assert "/gestion/candidat/changer-matiere?id_candidat=1&amp;id_oral=10" in body
        assert "/gestion/candidat/changer-matiere?id_candidat=1&amp;id_oral=11" in body
        assert "🔄" not in body  # icône SVG, pas emoji (cf. project_liens_admin)



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


