"""Tests unitaires pour webserver/rebalance.py — rééquilibrage des oraux."""
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webserver"))

from rebalance import (  # noqa: E402
    Changement,
    ExaminateurCible,
    OralActuel,
    planifier_absence,
    planifier_renfort,
)


def _td(h, m=0):
    return timedelta(hours=h, minutes=m)


def _oral(id_, id_candidat, numero, id_examinateur, examinateur_nom,
          heure_sujet, heure_oral=None, heure_fin=None, etablissement="") -> OralActuel:
    heure_oral = heure_oral if heure_oral is not None else heure_sujet + _td(0, 15)
    heure_fin = heure_fin if heure_fin is not None else heure_oral + _td(0, 15)
    return OralActuel(
        id=id_, id_candidat=id_candidat, numero=numero, etablissement=etablissement,
        id_examinateur=id_examinateur, examinateur_nom=examinateur_nom,
        heure_sujet=heure_sujet, heure_oral=heure_oral, heure_fin=heure_fin,
    )


class TestPlanifierAbsenceCasSimple:
    def test_meme_heure_privilegiee_quand_libre(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: []},
            grille_horaires=[_td(9), _td(9, 30)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
        )
        assert not plan.non_replaces
        assert len(plan.changements) == 1
        changement = plan.changements[0]
        assert changement.nouvel_examinateur_id == 2
        assert changement.meme_heure is True
        assert changement.nouvelle_heure_sujet == _td(9)

    def test_repli_sur_autre_heure_si_meme_creneau_occupe(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        # ProfB déjà occupé de 9h15 à 9h30 (période "oral" du créneau 9h)
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: [(_td(9, 15), _td(9, 30))]},
            grille_horaires=[_td(9), _td(9, 30)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
        )
        assert not plan.non_replaces
        changement = plan.changements[0]
        assert changement.meme_heure is False
        assert changement.nouvelle_heure_sujet == _td(9, 30)

    def test_non_replace_si_aucune_option(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: [(_td(9, 15), _td(9, 30))]},
            grille_horaires=[_td(9)],  # une seule heure possible, déjà occupée
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
        )
        assert plan.changements == []
        assert plan.non_replaces == [oral]


class TestPlanifierAbsenceExclusions:
    def test_etablissement_a_eviter_respecte(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                      heure_sujet=_td(9), etablissement="LyceeX")
        examinateurs = [
            ExaminateurCible(id=2, nom="ProfB", etablissements=["LyceeX"]),
            ExaminateurCible(id=3, nom="ProfC", etablissements=['']),
        ]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: [], 3: []},
            grille_horaires=[_td(9)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
        )
        assert len(plan.changements) == 1
        assert plan.changements[0].nouvel_examinateur_id == 3

    def test_prof_a_eviter_respecte(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [
            ExaminateurCible(id=2, nom="ProfB", etablissements=['']),
            ExaminateurCible(id=3, nom="ProfC", etablissements=['']),
        ]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: [], 3: []},
            grille_horaires=[_td(9)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={"N100": ["ProfB"]},
        )
        assert len(plan.changements) == 1
        assert plan.changements[0].nouvel_examinateur_id == 3


class TestPlanifierAbsenceEcartMini:
    def test_ecart_mini_respecte_lors_du_repli(self):
        # L'autre oral du candidat est à 9h30 -> un repli à 9h50 (20 min d'écart)
        # doit être refusé si l'écart mini est de 40 min, mais 10h10 (40 min) accepté.
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: [(_td(9, 15), _td(9, 30))]},  # bloque le même créneau
            grille_horaires=[_td(9), _td(9, 50), _td(10, 10)],
            autres_heures_sujet={100: _td(9, 30)},
            ecart_mini_minutes=40,
            profs_a_eviter={},
        )
        assert not plan.non_replaces
        assert plan.changements[0].nouvelle_heure_sujet == _td(10, 10)


class TestPlanifierRenfort:
    def test_decharge_examinateur_le_plus_charge(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        oral_b = _oral(2, 101, "N101", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9, 30))
        renfort = ExaminateurCible(id=9, nom="ProfRenfort", etablissements=[''])
        plan = planifier_renfort(
            oraux_deplacables=[oral_a, oral_b],
            examinateur_renfort=renfort,
            occupations_initiales={9: []},
            grille_horaires=[_td(9), _td(9, 30)],
            autres_heures_sujet={100: None, 101: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
            charge_par_examinateur={1: 2, 9: 0},
        )
        # Moyenne cible = (2+0)/2 = 1 -> ProfA doit descendre à 1, un seul oral déplacé
        assert len(plan.changements) == 1
        assert plan.changements[0].nouvel_examinateur_id == 9
        assert plan.non_replaces == []

    def test_aucun_deplacement_si_deja_equilibre(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        renfort = ExaminateurCible(id=9, nom="ProfRenfort", etablissements=[''])
        plan = planifier_renfort(
            oraux_deplacables=[oral_a],
            examinateur_renfort=renfort,
            occupations_initiales={9: []},
            grille_horaires=[_td(9)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
            charge_par_examinateur={1: 1, 9: 1},  # déjà équilibré
        )
        assert plan.changements == []

    def test_echec_de_placement_nest_jamais_bloquant(self):
        # Le renfort est déjà occupé au seul horaire disponible -> l'oral reste
        # chez son examinateur actuel, sans jamais apparaître dans non_replaces
        # (le renfort n'est jamais obligatoire).
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        renfort = ExaminateurCible(id=9, nom="ProfRenfort", etablissements=[''])
        plan = planifier_renfort(
            oraux_deplacables=[oral_a],
            examinateur_renfort=renfort,
            occupations_initiales={9: [(_td(9, 15), _td(9, 30))]},
            grille_horaires=[_td(9)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=40,
            profs_a_eviter={},
            charge_par_examinateur={1: 2, 9: 0},
        )
        assert plan.changements == []
        assert plan.non_replaces == []


class TestChangementMemeHeure:
    def test_meme_heure_true_si_heure_inchangee(self):
        c = Changement(
            id_oral=1, id_candidat=1, numero="N1", ancien_examinateur_nom="A",
            nouvel_examinateur_id=2, nouvel_examinateur_nom="B",
            ancienne_heure_sujet=_td(9), nouvelle_heure_sujet=_td(9),
            nouvelle_heure_oral=_td(9, 15), nouvelle_heure_fin=_td(9, 30),
        )
        assert c.meme_heure is True

    def test_meme_heure_false_si_heure_changee(self):
        c = Changement(
            id_oral=1, id_candidat=1, numero="N1", ancien_examinateur_nom="A",
            nouvel_examinateur_id=2, nouvel_examinateur_nom="B",
            ancienne_heure_sujet=_td(9), nouvelle_heure_sujet=_td(9, 30),
            nouvelle_heure_oral=_td(9, 45), nouvelle_heure_fin=_td(10),
        )
        assert c.meme_heure is False
