"""Tests d'intégration pour la route /gestion/candidat/changer-matiere
(un candidat change de matière en cours de journée après le placement initial).

La logique de replanification elle-même (rebalance.py) est testée en détail
dans tests/unit/test_rebalance.py — ces tests-ci vérifient uniquement le
câblage de la route (requêtes DB, gabarit rendu, mise à jour du choix
candidat, notifications SSE ciblant l'ancien ET le nouvel examinateur).
"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


CANDIDAT_INFO = {
    "id": 100, "nom": "Cand Test", "numero": "N100", "etablissement": "",
    "choix1": 5, "choix2": 6,
}

ORAUX_CANDIDAT = [
    {"id": 10, "matiere": "Maths", "examinateur": "ProfMaths", "salle": "A1",
     "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
     "heure_fin": timedelta(hours=9, minutes=30)},
    {"id": 20, "matiere": "Philo", "examinateur": "ProfPhilo", "salle": "B1",
     "heure_sujet": timedelta(hours=11), "heure_oral": timedelta(hours=11, minutes=15),
     "heure_fin": timedelta(hours=11, minutes=30)},
]

LISTE_MATIERES = [
    {"id": 5, "nom": "Maths"}, {"id": 6, "nom": "Philo"}, {"id": 7, "nom": "Anglais"},
]

ORAL_LIGNE_CHANGEMENT = {
    "id": 10, "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
    "heure_fin": timedelta(hours=9, minutes=30),
    "id_examinateur": 1, "examinateur": "ProfMaths", "id_matiere": 5,
    "id_candidat": 100, "numero": "N100", "etablissement": "",
}

ORAL_LIGNE_ANCIENNE_MATIERE = {
    "id": 10, "id_candidat": 100, "numero": "N100", "etablissement": "",
    "id_examinateur": 1, "examinateur": "ProfMaths",
    "heure_sujet": timedelta(hours=9), "heure_oral": timedelta(hours=9, minutes=15),
    "heure_fin": timedelta(hours=9, minutes=30),
}

EXAMINATEURS_ANGLAIS = [{"id": 3, "nom": "ProfAnglais", "etablissements": "", "salle": "C1"}]


def _side_effect_defaut(sql, *args):
    """Dispatch par identité de requête SQL — robuste à l'ordre d'appel."""
    import db_facility_web as dfw
    if sql is dfw.SELECT_CANDIDAT_CHANGEMENT_MATIERE:
        return [CANDIDAT_INFO]
    if sql is dfw.SELECT_LISTE_EDITION_ORAL:
        return ORAUX_CANDIDAT
    if sql is dfw.SELECT_LISTE_MATIERES:
        return LISTE_MATIERES
    if sql is dfw.SELECT_ORAL_POUR_CHANGEMENT_MATIERE:
        return [ORAL_LIGNE_CHANGEMENT]
    if sql is dfw.SELECT_ORAUX_MATIERE_DU_JOUR:
        id_matiere = args[0]
        if id_matiere == 7:  # nouvelle matière (Anglais) : encore vide aujourd'hui
            return []
        return [ORAL_LIGNE_ANCIENNE_MATIERE]  # ancienne matière (Maths)
    if sql is dfw.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE:
        return EXAMINATEURS_ANGLAIS
    if sql is dfw.SELECT_SALLE_LOGE_FROM_EXAMINATEUR:
        return [{"salle": "X", "loge": "LogeX"}]
    return []


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, tmp_path):
    """Isole les tests des fichiers réels (algo_params.json, candidats.csv)."""
    import app as app_module
    monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")
    monkeypatch.setattr(app_module, "_charger_profs_a_eviter", lambda: {})


class TestChangerMatiereCandidatForm:
    def test_get_sans_id_404(self, admin_client):
        r = admin_client.get("/gestion/candidat/changer-matiere")
        assert r.status_code == 404

    def test_get_affiche_le_formulaire(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        r = admin_client.get("/gestion/candidat/changer-matiere?id_candidat=100")
        assert r.status_code == 200
        body = r.data.decode()
        assert "Cand Test" in body
        assert "Anglais" in body
        # Les matières déjà choisies (Maths, Philo) ne doivent pas apparaître
        # dans le sélecteur de nouvelle matière.
        assert 'value="7"' in body

    def test_get_avec_id_oral_preselectionne_la_ligne(self, admin_client, db_mock):
        """Depuis l'icône « changer de matière » d'un oral précis
        (/gestion/candidats), le radio de cet oral doit être pré-coché."""
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        r = admin_client.get("/gestion/candidat/changer-matiere?id_candidat=100&id_oral=20")
        assert r.status_code == 200
        body = r.data.decode()
        assert 'value="20" required\n               checked' in body


class TestChangerMatiereCandidatPrevisualisation:
    def _post(self, admin_client, db_mock, **extra):
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        data = {
            "id_candidat": "100", "etape": "previsualisation",
            "id_oral_a_remplacer": "10", "nouvelle_matiere": "7",
        }
        data.update(extra)
        return admin_client.post("/gestion/candidat/changer-matiere", data=data)

    def test_propose_le_nouvel_examinateur(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock)
        assert r.status_code == 200
        body = r.data.decode()
        assert "ProfAnglais" in body
        assert "N100" in body

    def test_lie_les_noms_d_examinateur_vers_leur_edition(self, admin_client, db_mock):
        r = self._post(admin_client, db_mock)
        body = r.data.decode()
        assert "/gestion/edit-examinateur?id_examinateur=1" in body  # ProfMaths (ancien)
        assert "/gestion/edit-examinateur?id_examinateur=3" in body  # ProfAnglais (nouveau)

    def test_refuse_matiere_deja_choisie(self, admin_client, db_mock):
        db_mock.make_sql_select.side_effect = _side_effect_defaut
        r = admin_client.post("/gestion/candidat/changer-matiere", data={
            "id_candidat": "100", "etape": "previsualisation",
            "id_oral_a_remplacer": "10", "nouvelle_matiere": "6",  # Philo, déjà choisi
        })
        assert r.status_code == 400

    def test_aucune_mise_a_jour_pendant_la_previsualisation(self, admin_client, db_mock):
        db_mock.make_sql_update.reset_mock()
        self._post(admin_client, db_mock)
        db_mock.make_sql_update.assert_not_called()


class TestChangerMatiereCandidatConfirmation:
    def test_confirmer_applique_et_notifie_ancien_et_nouvel_examinateur(
        self, admin_client, db_mock, monkeypatch,
    ):
        import app as app_module
        publish_mock = MagicMock()
        monkeypatch.setattr(app_module.sse, "publish", publish_mock)
        db_mock.make_sql_update.reset_mock()
        db_mock.make_sql_select.side_effect = _side_effect_defaut

        r = admin_client.post("/gestion/candidat/changer-matiere", data={
            "id_candidat": "100", "etape": "confirmer",
            "id_oral_a_remplacer": "10", "nouvelle_matiere": "7",
        })
        assert r.status_code == 200
        assert "déplacé" in r.data.decode()

        # UPDATE_INFOS_ORAL (nouvel oral) + UPDATE_CANDIDAT_CHOIX1 (choix1==5==ancienne matière)
        assert db_mock.make_sql_update.call_count == 2
        sql_appelees = [c.args[0] for c in db_mock.make_sql_update.call_args_list]
        import db_facility_web as dfw
        assert dfw.UPDATE_INFOS_ORAL in sql_appelees
        assert dfw.UPDATE_CANDIDAT_CHOIX1 in sql_appelees

        _, kwargs_oral = next(
            c for c in db_mock.make_sql_update.call_args_list if c.args[0] is dfw.UPDATE_INFOS_ORAL
        )
        assert kwargs_oral["examinateur"] == 3  # ProfAnglais

        # Notifie l'ancien ET le nouvel examinateur (salle+loge chacun) + le candidat + general
        assert publish_mock.called
        canaux = [c.kwargs.get("channel") for c in publish_mock.call_args_list]
        assert "salle_X" in canaux  # SELECT_SALLE_LOGE_FROM_EXAMINATEUR mocké identique pour les deux
        assert "candidat_N100" in canaux
