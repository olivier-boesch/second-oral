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
    construire_grille_etendue,
    duree_creneau_estimee,
    planifier_absence,
    planifier_changement_matiere,
    planifier_renfort,
    proposer_compaction,
    resoudre_oraux_difficiles,
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


class TestDureeCreneauEtGrilleEtendue:
    def test_duree_deduite_du_plus_petit_ecart(self):
        assert duree_creneau_estimee([_td(9), _td(9, 30), _td(10, 30)], []) == _td(0, 30)

    def test_duree_repli_sur_duree_oral_si_grille_insuffisante(self):
        oral = _oral(1, 100, "N100", 1, "ProfA", heure_sujet=_td(9))
        assert duree_creneau_estimee([_td(9)], [oral]) == oral.heure_fin - oral.heure_sujet

    def test_grille_etendue_prolonge_apres_le_dernier_horaire(self):
        grille = construire_grille_etendue([_td(9), _td(9, 30)], _td(0, 30), plafond_minutes=60)
        assert grille == [_td(9), _td(9, 30), _td(10), _td(10, 30)]

    def test_grille_vide_reste_vide(self):
        assert construire_grille_etendue([], _td(0, 30)) == []


class TestResoudreOrauxDifficiles:
    def test_reussit_la_ou_le_glouton_bloquerait(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        # ProfB occupé au même horaire -> doit se rabattre sur 9h30 de la grille.
        occupations = {2: [(_td(9, 15), _td(9, 30))]}
        plan = resoudre_oraux_difficiles(
            [oral], examinateurs, occupations, [_td(9), _td(9, 30)],
            {100: None}, 40, {},
        )
        assert not plan.non_replaces
        changement = plan.changements[0]
        assert changement.nouvel_examinateur_id == 2
        assert changement.nouvelle_heure_sujet == _td(9, 30)
        assert changement.hors_grille is False

    def test_infaisable_meme_grille_reste_non_replace(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        # ProfB occupé sur tous les horaires de la grille -> aucune solution.
        occupations = {2: [(_td(9, 15), _td(9, 30)), (_td(9, 45), _td(10))]}
        plan = resoudre_oraux_difficiles(
            [oral], examinateurs, occupations, [_td(9), _td(9, 30)],
            {100: None}, 40, {},
        )
        assert plan.changements == []
        assert [o.id for o in plan.non_replaces] == [1]

    def test_grille_etendue_marque_hors_grille(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        occupations = {2: [(_td(9, 15), _td(9, 30)), (_td(9, 45), _td(10))]}
        grille_initiale = [_td(9), _td(9, 30)]
        grille_etendue = construire_grille_etendue(grille_initiale, _td(0, 30), plafond_minutes=60)
        plan = resoudre_oraux_difficiles(
            [oral], examinateurs, occupations, grille_etendue,
            {100: None}, 40, {}, grille_initiale=grille_initiale,
        )
        assert not plan.non_replaces
        changement = plan.changements[0]
        assert changement.hors_grille is True
        assert changement.nouvelle_heure_sujet not in grille_initiale

    def test_respecte_les_exclusions(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                      heure_sujet=_td(9), etablissement="LyceeX")
        examinateurs = [
            ExaminateurCible(id=2, nom="ProfB", etablissements=["LyceeX"]),
            ExaminateurCible(id=3, nom="ProfC", etablissements=['']),
        ]
        plan = resoudre_oraux_difficiles(
            [oral], examinateurs, {2: [], 3: []}, [_td(9)], {100: None}, 40, {},
        )
        assert len(plan.changements) == 1
        assert plan.changements[0].nouvel_examinateur_id == 3

    def test_liste_vide_ne_plante_pas(self):
        plan = resoudre_oraux_difficiles([], [], {}, [], {}, 40, {})
        assert plan.changements == []
        assert plan.non_replaces == []


class TestPlanifierChangementMatiere:
    """Un candidat change de matière : son oral de l'ancienne matière doit
    être replacé dans la nouvelle, même heure d'abord."""

    def test_meme_heure_privilegiee_quand_libre(self):
        oral_maths = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(9))
        examinateurs_philo = [ExaminateurCible(id=2, nom="ProfPhilo", etablissements=[''])]
        changement = planifier_changement_matiere(
            oral_maths, examinateurs_philo, {2: []}, [_td(9), _td(9, 30)], None, 40, {},
        )
        assert changement is not None
        assert changement.nouvel_examinateur_id == 2
        assert changement.nouvelle_heure_sujet == _td(9)

    def test_respecte_ecart_mini_contre_lautre_oral_fixe(self):
        oral_maths = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(9))
        examinateurs_philo = [ExaminateurCible(id=2, nom="ProfPhilo", etablissements=[''])]
        # ProfPhilo occupé à 9h (même heure) -> repli nécessaire ; l'autre
        # oral fixe du candidat est à 9h50, donc 9h30 (20 min d'écart) doit
        # être refusé et 10h30 (40 min) accepté.
        occupations = {2: [(_td(9, 15), _td(9, 30))]}
        changement = planifier_changement_matiere(
            oral_maths, examinateurs_philo, occupations, [_td(9), _td(9, 30), _td(10, 30)],
            _td(9, 50), 40, {},
        )
        assert changement is not None
        assert changement.nouvelle_heure_sujet == _td(10, 30)

    def test_respecte_les_exclusions(self):
        oral_maths = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfMaths",
                            heure_sujet=_td(9), etablissement="LyceeX")
        examinateurs_philo = [
            ExaminateurCible(id=2, nom="ProfPhilo", etablissements=["LyceeX"]),
            ExaminateurCible(id=3, nom="ProfPhilo2", etablissements=['']),
        ]
        changement = planifier_changement_matiere(
            oral_maths, examinateurs_philo, {2: [], 3: []}, [_td(9)], None, 40, {},
        )
        assert changement is not None
        assert changement.nouvel_examinateur_id == 3

    def test_aucune_option_retourne_none(self):
        oral_maths = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(9))
        examinateurs_philo = [ExaminateurCible(id=2, nom="ProfPhilo", etablissements=[''])]
        occupations = {2: [(_td(9, 15), _td(9, 30))]}
        changement = planifier_changement_matiere(
            oral_maths, examinateurs_philo, occupations, [_td(9)], None, 40, {},
        )
        assert changement is None


class TestProposerCompaction:
    """Suggestion optionnelle : compacter le planning de l'ancien examinateur
    en déplaçant son oral le plus tardif dans le créneau libéré."""

    def test_propose_le_plus_tardif(self):
        oral_9h30 = _oral(2, 101, "N101", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(9, 30))
        oral_10h = _oral(3, 102, "N102", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(10))
        compaction = proposer_compaction(
            [oral_9h30, oral_10h], creneau_libere=_td(9), autres_heures_sujet={101: None, 102: None},
            ecart_mini_minutes=40,
        )
        assert compaction is not None
        assert compaction.id_oral == oral_10h.id  # le plus tardif
        assert compaction.nouvelle_heure_sujet == _td(9)
        assert compaction.nouvel_examinateur_id == 1  # même examinateur

    def test_aucun_oral_plus_tardif_retourne_none(self):
        oral_8h = _oral(2, 101, "N101", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(8))
        compaction = proposer_compaction(
            [oral_8h], creneau_libere=_td(9), autres_heures_sujet={101: None}, ecart_mini_minutes=40,
        )
        assert compaction is None

    def test_refuse_si_ecart_mini_casse(self):
        oral_10h = _oral(3, 102, "N102", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(10))
        # L'autre oral fixe de ce candidat est à 9h20 -> le déplacer à 9h
        # (créneau libéré) ne laisserait que 20 min d'écart, insuffisant.
        compaction = proposer_compaction(
            [oral_10h], creneau_libere=_td(9), autres_heures_sujet={102: _td(9, 20)},
            ecart_mini_minutes=40,
        )
        assert compaction is None

    def test_liste_vide_retourne_none(self):
        assert proposer_compaction([], _td(9), {}, 40) is None
