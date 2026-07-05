"""Tests d'intégration pour la route /gestion/examinateur/disponibilite
(rééquilibrage des oraux suite à une absence/retard/renfort d'examinateur).

La logique de replanification elle-même (rebalance.py) est testée en détail
dans tests/unit/test_rebalance.py — ces tests-ci vérifient uniquement le
câblage de la route (requêtes DB dans le bon ordre, gabarit rendu, notification
SSE déclenchée à la confirmation).
"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


EXAMINATEUR_MATIERE = {"id": 1, "nom": "ProfA", "id_matiere": 5, "matiere": "Maths"}

ORAL_MATIERE_DU_JOUR = {
    "id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
    "id_examinateur": 1, "examinateur": "ProfA",
    "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
    "heure_fin": timedelta(hours=9, minutes=30),
}

EXAMINATEURS_MATIERE = [
    {"id": 1, "nom": "ProfA", "etablissements": "", "salle": "A1"},
    {"id": 2, "nom": "ProfB", "etablissements": "", "salle": "A2"},
]

AUTRE_ORAL_CANDIDAT = [
    {"id": 10, "matiere": "Maths", "examinateur": "ProfA", "salle": "A1",
     "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
     "heure_fin": timedelta(hours=9, minutes=30)},
    {"id": 20, "matiere": "Philo", "examinateur": "ProfC", "salle": "B1",
     "heure_sujet": timedelta(hours=11), "heure_oral": timedelta(hours=11, minutes=15),
     "heure_fin": timedelta(hours=11, minutes=30)},
]


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    """Isole les tests des fichiers réels (algo_params.json, candidats.csv)."""
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")
    monkeypatch.setattr(app_module, "_charger_profs_a_eviter", lambda: {})


class TestDisponibiliteExaminateurForm:
    def test_get_sans_id_404(self, admin_client):
        r = admin_client.get("/gestion/examinateur/disponibilite")
        assert r.status_code == 404

    def test_get_affiche_le_formulaire(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [[EXAMINATEUR_MATIERE]]
        r = admin_client.get("/gestion/examinateur/disponibilite?id_examinateur=1")
        assert r.status_code == 200
        assert "ProfA" in r.data.decode()
        assert "Maths" in r.data.decode()

    def test_post_sans_aucune_heure_erreur_400(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [[EXAMINATEUR_MATIERE]]
        r = admin_client.post("/gestion/examinateur/disponibilite", data={
            "id_examinateur": "1", "etape": "previsualisation",
            "indisponible_a_partir_de": "", "disponible_a_nouveau_a_partir_de": "",
        })
        assert r.status_code == 400


class TestDisponibiliteExaminateurPrevisualisation:
    def _post_previsualisation(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = [
            [EXAMINATEUR_MATIERE],       # SELECT_EXAMINATEUR_MATIERE (route)
            [ORAL_MATIERE_DU_JOUR],      # SELECT_ORAUX_MATIERE_DU_JOUR
            EXAMINATEURS_MATIERE,        # SELECT_LISTE_EXAMINATEURS_PAR_MATIERE
            AUTRE_ORAL_CANDIDAT,         # SELECT_LISTE_EDITION_ORAL (candidat 100)
        ]
        return admin_client.post("/gestion/examinateur/disponibilite", data={
            "id_examinateur": "1", "etape": "previsualisation",
            "indisponible_a_partir_de": "09:00", "disponible_a_nouveau_a_partir_de": "",
        })

    def test_previsualisation_propose_le_bon_examinateur(self, admin_client, db_mock):
        r = self._post_previsualisation(admin_client, db_mock)
        assert r.status_code == 200
        body = r.data.decode()
        assert "ProfB" in body
        assert "N100" in body
        assert "1</strong> oral" in body

    def test_aucune_mise_a_jour_pendant_la_previsualisation(self, admin_client, db_mock):
        db_mock.make_sql_update.reset_mock()
        self._post_previsualisation(admin_client, db_mock)
        db_mock.make_sql_update.assert_not_called()


ORAUX_MATIERE_DIFFICILES = [
    {"id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
     "id_examinateur": 1, "examinateur": "ProfA",
     "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
     "heure_fin": timedelta(hours=9, minutes=30)},
    {"id": 11, "id_candidat": 101, "numero": "N101", "etablissement": "",
     "id_examinateur": 2, "examinateur": "ProfB",
     "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
     "heure_fin": timedelta(hours=9, minutes=30)},
    {"id": 12, "id_candidat": 102, "numero": "N102", "etablissement": "",
     "id_examinateur": 2, "examinateur": "ProfB",
     "heure_sujet": timedelta(hours=9, minutes=30), "heure_oral": timedelta(hours=9, minutes=45),
     "heure_fin": timedelta(hours=10)},
]


def _side_effect_matiere_difficile(sql, *args):
    """Dispatch par identité de requête SQL — robuste à l'ordre d'itération
    (contrairement à une liste positionnelle) puisque plusieurs candidats
    distincts déclenchent chacun un appel SELECT_LISTE_EDITION_ORAL."""
    import db_facility_web as dfw
    if sql is dfw.SELECT_EXAMINATEUR_MATIERE:
        return [EXAMINATEUR_MATIERE]
    if sql is dfw.SELECT_ORAUX_MATIERE_DU_JOUR:
        return ORAUX_MATIERE_DIFFICILES
    if sql is dfw.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE:
        return EXAMINATEURS_MATIERE
    if sql is dfw.SELECT_LISTE_EDITION_ORAL:
        return []  # aucun des candidats n'a d'autre oral pertinent pour ce test
    if sql is dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR:
        return [{"salle": "A2", "loge": "LogeA"}]
    return []


class TestDisponibiliteExaminateurResolutionPoussee:
    """ProfB (seul autre examinateur de Maths) est occupé aux deux seuls
    horaires déjà utilisés aujourd'hui (9h00 et 9h30) : le glouton (palier 1)
    ne peut donc replacer l'oral de ProfA nulle part. Vérifie les paliers 2
    (même grille, échoue aussi) et 3 (grille étendue, réussit)."""

    def _post(self, admin_client, db_mock, etape):
        db_mock.make_sql_select.side_effect = _side_effect_matiere_difficile
        return admin_client.post("/gestion/examinateur/disponibilite", data={
            "id_examinateur": "1", "etape": etape,
            "indisponible_a_partir_de": "09:00", "disponible_a_nouveau_a_partir_de": "",
        })

    def test_palier_1_glouton_echoue(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock, "previsualisation")
        assert r.status_code == 200
        body = r.data.decode()
        assert "N100" in body
        assert "non replacé" in body

    def test_palier_2_meme_grille_echoue_aussi(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock, "resolution_poussee_grille")
        assert r.status_code == 200
        assert "non replacé" in r.data.decode()

    def test_palier_3_grille_etendue_reussit(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock, "resolution_poussee_etendue")
        assert r.status_code == 200
        body = r.data.decode()
        assert "non replacé" not in body
        assert "ProfB" in body
        assert "🕐" in body  # marqueur d'extension d'horaire


class TestDisponibiliteExaminateurConfirmation:
    def test_confirmer_applique_et_notifie(self, admin_client, db_mock, monkeypatch):
        import app as app_module
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = [
            [EXAMINATEUR_MATIERE],       # SELECT_EXAMINATEUR_MATIERE (route)
            [ORAL_MATIERE_DU_JOUR],      # SELECT_ORAUX_MATIERE_DU_JOUR
            EXAMINATEURS_MATIERE,        # SELECT_LISTE_EXAMINATEURS_PAR_MATIERE
            AUTRE_ORAL_CANDIDAT,         # SELECT_LISTE_EDITION_ORAL (candidat 100)
            [{"salle": "A2", "loge": "LogeA"}],  # SELECT_SALLE_LOGE_FROM_EXAMINATEUR
        ]
        r = admin_client.post("/gestion/examinateur/disponibilite", data={
            "id_examinateur": "1", "etape": "confirmer",
            "indisponible_a_partir_de": "09:00", "disponible_a_nouveau_a_partir_de": "",
        })
        assert r.status_code == 200
        assert "redistribué" in r.data.decode()
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs["examinateur"] == 2
        assert publish_mock.called
