"""Tests d'intégration Flask — édition manuelle d'un oral
(liens examinateur/salle, créneaux libres, validation)."""

import pytest


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
        "salle": "B02", "identifiant": "examinateur11",
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
        assert '<a href="/examinateur/examinateur11" target="_blank">B02</a>' in body

    def test_get_affiche_le_panneau_creneaux(self, admin_client, db_mock):
        """Le panneau de suggestion de créneaux (cf. project_creneaux_edit_oral)
        doit être présent, avec l'appel JS vers le bon endpoint."""
        db_mock.make_sql_select.side_effect = [
            [self.DONNEES_ORAL], [self.AUTRE_ORAL], [], [],
        ]
        r = admin_client.get("/gestion/edit-oral?oral=1")
        body = r.data.decode()
        assert 'id="creneaux-table"' in body
        assert "/gestion/edit-oral/creneaux?oral=1" in body
        assert "chargerCreneaux()" in body


class TestEditOralCreneaux:
    """Suggestions de créneaux libres pour un examinateur donné, sur l'écran
    d'édition manuelle d'un oral (/gestion/edit-oral/creneaux) — cf.
    project_creneaux_edit_oral."""

    from datetime import timedelta as _td

    ORAL_ACTUEL = {
        "id": 1, "nom": "Dupont Jean", "numero": "N1", "etablissement": "",
        "tiers_temps": 0, "id_candidat": 42, "id_examinateur": 10, "id_matiere": 2,
        "heure_sujet": _td(hours=9), "heure_oral": _td(hours=9, minutes=15),
        "heure_fin": _td(hours=9, minutes=30), "matiere": "Maths",
    }
    AUTRE_ORAL_CANDIDAT = {
        # Volontairement loin (14h) pour ne jamais interférer avec l'écart
        # minimum (défaut 80 min) sur les créneaux candidats 9h/10h ci-dessous.
        "id": 2, "id_candidat": 42, "matiere": "Philo",
        "heure_sujet": _td(hours=14), "heure_oral": _td(hours=14, minutes=15),
        "heure_fin": _td(hours=14, minutes=30),
    }
    ORAUX_MATIERE_DU_JOUR = [
        {"id": 1, "id_candidat": 42, "numero": "N1", "etablissement": "",
         "id_examinateur": 10, "examinateur": "ProfA",
         "heure_sujet": _td(hours=9), "heure_oral": _td(hours=9, minutes=15),
         "heure_fin": _td(hours=9, minutes=30)},
        {"id": 3, "id_candidat": 43, "numero": "N3", "etablissement": "",
         "id_examinateur": 20, "examinateur": "ProfB",
         "heure_sujet": _td(hours=10), "heure_oral": _td(hours=10, minutes=15),
         "heure_fin": _td(hours=10, minutes=30)},
    ]

    @pytest.fixture(autouse=True)
    def _isolation(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")

    def test_requires_admin(self, client):
        r = client.get("/gestion/edit-oral/creneaux?oral=1&examinateur=10&matiere=2",
                       follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_parametres_manquants_400(self, admin_client):
        r = admin_client.get("/gestion/edit-oral/creneaux?oral=1")
        assert r.status_code == 400

    def test_oral_introuvable_404(self, admin_client, db_mock):
        db_mock.make_sql_select.return_value = []
        r = admin_client.get("/gestion/edit-oral/creneaux?oral=999&examinateur=10&matiere=2")
        assert r.status_code == 404

    def test_propose_les_creneaux_libres_de_l_examinateur(self, admin_client, db_mock):
        """L'examinateur 10 est déjà pris à 9h (son propre oral en cours
        d'édition, exclu) et libre à 10h (occupé par ProfB, pas lui) :
        les deux créneaux de la grille doivent donc ressortir."""
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],           # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL_CANDIDAT],   # SELECT_LISTE_EDITION_ORAL
            self.ORAUX_MATIERE_DU_JOUR,   # SELECT_ORAUX_MATIERE_DU_JOUR
            [],                            # SELECT_ORAUX_EXAMINATEUR_CONFLITS (examinateur 10 libre à part son propre oral)
        ]
        r = admin_client.get("/gestion/edit-oral/creneaux?oral=1&examinateur=10&matiere=2")
        assert r.status_code == 200
        assert r.get_json() == ["09:00", "10:00"]

    def test_exclut_les_creneaux_deja_pris_par_l_examinateur(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [self.ORAL_ACTUEL],           # SELECT_INFOS_ORAL
            [self.AUTRE_ORAL_CANDIDAT],   # SELECT_LISTE_EDITION_ORAL
            self.ORAUX_MATIERE_DU_JOUR,   # SELECT_ORAUX_MATIERE_DU_JOUR
            [{"id": 5, "candidat": "Autre Candidat",  # occupe 10h15-10h30 (chevauche le créneau 10h)
              "heure_oral": self._td(hours=10, minutes=15), "heure_fin": self._td(hours=10, minutes=30)}],
        ]
        r = admin_client.get("/gestion/edit-oral/creneaux?oral=1&examinateur=10&matiere=2")
        assert r.status_code == 200
        assert r.get_json() == ["09:00"]


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


