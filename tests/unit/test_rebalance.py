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
    creneaux_libres,
    duree_creneau_estimee,
    planifier_absence,
    planifier_changement_matiere,
    planifier_renfort,
    planifier_tiers_temps,
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


class TestPlacerPrivilegieCreneauAvantOralExistant:
    """En repli (heure d'origine impossible), un créneau qui se termine juste
    avant un oral déjà planifié de l'examinateur ciblé est préféré à un
    créneau simplement plus proche de l'heure d'origine mais isolé."""

    def test_privilegie_le_trou_avant_un_oral_existant(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [
            ExaminateurCible(id=2, nom="ProfB", etablissements=['']),
            ExaminateurCible(id=3, nom="ProfC", etablissements=['']),
        ]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={
                # ProfB : libre à 9h20 mais a déjà un oral à 10h -> 9h20 comble
                # le trou juste avant (25 min d'écart).
                2: [(_td(9, 15), _td(9, 30)), (_td(10, 15), _td(10, 30))],
                # ProfC : aussi libre à 9h20, mais sans oral existant à
                # proximité -> 9h20 lui serait pourtant plus proche de 9h.
                3: [(_td(9, 15), _td(9, 30))],
            },
            grille_horaires=[_td(9), _td(9, 20), _td(10)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=0,
            profs_a_eviter={},
        )
        assert not plan.non_replaces
        changement = plan.changements[0]
        assert changement.ancien_examinateur_id == 1
        assert changement.nouvel_examinateur_id == 2
        assert changement.nouvelle_heure_sujet == _td(9, 20)


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


class TestCreneauxLibres:
    """Suggestions de créneaux pour un examinateur déjà fixé — écran
    d'édition manuelle d'un oral (edit_oral.html)."""

    def test_filtre_les_creneaux_deja_occupes_par_l_examinateur(self):
        # Examinateur déjà occupé 9h15-9h30 -> le créneau 9h (9h15-9h30) est exclu.
        creneaux = creneaux_libres(
            duree_prep=_td(0, 15), duree_oral=_td(0, 15),
            occupations_examinateur=[(_td(9, 15), _td(9, 30))],
            grille_horaires=[_td(9), _td(9, 30), _td(10)],
            autre_heure_sujet=None, ecart_mini_minutes=0,
        )
        assert creneaux == [_td(9, 30), _td(10)]

    def test_filtre_selon_l_ecart_minimum_avec_l_autre_oral_du_candidat(self):
        creneaux = creneaux_libres(
            duree_prep=_td(0, 15), duree_oral=_td(0, 15),
            occupations_examinateur=[],
            grille_horaires=[_td(9), _td(9, 30), _td(11)],
            autre_heure_sujet=_td(9, 20), ecart_mini_minutes=40,
        )
        # 9h -> 20 min d'écart (< 40) exclu ; 9h30 -> 10 min exclu ; 11h -> 100 min ok.
        assert creneaux == [_td(11)]

    def test_filtre_les_creneaux_chevauchant_la_pause_meridienne(self):
        creneaux = creneaux_libres(
            duree_prep=_td(0, 15), duree_oral=_td(0, 15),
            occupations_examinateur=[],
            grille_horaires=[_td(11, 45), _td(13, 30)],
            autre_heure_sujet=None, ecart_mini_minutes=0,
            heure_pause_meridienne=_td(12), duree_pause_meridienne=_td(1),
        )
        # 11h45 -> oral 12h-12h15, chevauche la pause (12h-13h) -> exclu.
        assert creneaux == [_td(13, 30)]

    def test_deduplique_et_trie_la_grille(self):
        creneaux = creneaux_libres(
            duree_prep=_td(0, 15), duree_oral=_td(0, 15),
            occupations_examinateur=[],
            grille_horaires=[_td(10), _td(9), _td(9)],
            autre_heure_sujet=None, ecart_mini_minutes=0,
        )
        assert creneaux == [_td(9), _td(10)]


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

    def test_refuse_si_chevauche_pause(self):
        # Déplacer l'oral de 10h vers 9h (créneau libéré) ferait travailler
        # l'examinateur de 9h15 à 9h30 — en plein dans la pause [9h10, 9h40).
        oral_10h = _oral(3, 102, "N102", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(10))
        compaction = proposer_compaction(
            [oral_10h], creneau_libere=_td(9), autres_heures_sujet={102: None}, ecart_mini_minutes=40,
            heure_pause_meridienne=_td(9, 10), duree_pause_meridienne=timedelta(minutes=30),
        )
        assert compaction is None


class TestPauseMeridienneRebalance:
    """La pause méridienne configurée ne doit jamais être proposée comme
    nouveau créneau, à aucun palier (glouton, CP-SAT, extension de grille)."""

    def test_placer_saute_le_meme_horaire_si_chevauche_la_pause(self):
        # Même heure (9h) chevauche la pause [9h15, 9h45) -> repli sur 9h30,
        # qui se termine (10h00) juste après la fin de la pause (9h45).
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        plan = planifier_absence(
            oraux_a_reaffecter=[oral],
            examinateurs_disponibles=examinateurs,
            occupations_initiales={2: []},
            grille_horaires=[_td(9), _td(9, 30)],
            autres_heures_sujet={100: None},
            ecart_mini_minutes=0,
            profs_a_eviter={},
            heure_pause_meridienne=_td(9, 15),
            duree_pause_meridienne=timedelta(minutes=30),
        )
        assert not plan.non_replaces
        assert plan.changements[0].nouvelle_heure_sujet == _td(9, 30)

    def test_construire_grille_etendue_saute_la_pause(self):
        grille = construire_grille_etendue(
            [_td(9)], duree_creneau=timedelta(minutes=20), plafond_minutes=60,
            heure_pause_meridienne=_td(9, 30), duree_pause_meridienne=timedelta(minutes=25),
        )
        # Aucun horaire généré à l'intérieur de [9h30, 9h55).
        assert not any(_td(9, 30) <= h < _td(9, 55) for h in grille)
        assert _td(9, 55) in grille

    def test_resoudre_oraux_difficiles_evite_la_pause(self):
        oral = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA", heure_sujet=_td(9))
        examinateurs = [ExaminateurCible(id=2, nom="ProfB", etablissements=[''])]
        resultat = resoudre_oraux_difficiles(
            [oral], examinateurs, {}, [_td(9), _td(9, 30)],
            {100: None}, 0, {},
            heure_pause_meridienne=_td(9, 10), duree_pause_meridienne=timedelta(minutes=30),
        )
        assert not resultat.non_replaces
        assert resultat.changements[0].nouvelle_heure_sujet == _td(9, 30)

    def test_planifier_changement_matiere_evite_la_pause(self):
        oral_maths = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfMaths", heure_sujet=_td(9))
        examinateurs_philo = [ExaminateurCible(id=2, nom="ProfPhilo", etablissements=[''])]
        changement = planifier_changement_matiere(
            oral_maths, examinateurs_philo, {2: []}, [_td(9), _td(9, 30)], None, 0, {},
            heure_pause_meridienne=_td(9, 15), duree_pause_meridienne=timedelta(minutes=30),
        )
        assert changement is not None
        assert changement.nouvelle_heure_sujet == _td(9, 30)


class TestPlanifierTiersTemps:
    """Déclaration de tiers-temps d'un candidat en cours de journée : ses deux
    oraux voient leur préparation étendue d'1/3, et les oraux suivants chez
    les deux mêmes examinateurs sont cascadés du même délai."""

    def test_extension_preparation_candidat(self):
        # Maths (ProfA) : prep 20min -> +7min (arrondi de 20/3=6.66)
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        # Philo (ProfB) : prep 15min -> +5min (exact)
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 15), heure_fin=_td(11, 30))

        plan = planifier_tiers_temps(
            oral_a, [],
            oral_b, [],
            autres_heures_sujet_cascade={}, ecart_mini_minutes=40,
        )
        assert plan.conflit_bloquant is None
        assert len(plan.changements) == 2
        ch_a, ch_b = plan.changements
        assert ch_a.est_le_candidat is True
        assert ch_a.nouvelle_heure_sujet == _td(9)  # heure de sujet inchangée
        assert ch_a.nouvelle_heure_oral == _td(9, 27)
        assert ch_a.nouvelle_heure_fin == _td(9, 47)
        assert ch_b.est_le_candidat is True
        assert ch_b.nouvelle_heure_sujet == _td(11)
        assert ch_b.nouvelle_heure_oral == _td(11, 20)
        assert ch_b.nouvelle_heure_fin == _td(11, 35)

    def test_cascade_decale_les_oraux_suivants_du_meme_delai(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 15), heure_fin=_td(11, 30))
        # Candidat suivant chez ProfA, juste après oral_a (9h40)
        suivant = _oral(3, 101, "N101", id_examinateur=1, examinateur_nom="ProfA",
                         heure_sujet=_td(9, 40), heure_oral=_td(10), heure_fin=_td(10, 20))

        plan = planifier_tiers_temps(
            oral_a, [suivant],
            oral_b, [],
            autres_heures_sujet_cascade={101: _td(13)},  # loin, aucun souci d'écart
            ecart_mini_minutes=40,
        )
        cascade = next(c for c in plan.changements if not c.est_le_candidat)
        assert cascade.id_oral == 3
        # Décalé de +7 min (même délai que l'extension de oral_a), écart préservé
        assert cascade.nouvelle_heure_sujet == _td(9, 47)
        assert cascade.nouvelle_heure_oral == _td(10, 7)
        assert cascade.nouvelle_heure_fin == _td(10, 27)
        assert cascade.ecart_mini_rompu is False

    def test_cascade_ignore_les_oraux_avant_le_candidat(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 15), heure_fin=_td(11, 30))
        avant = _oral(9, 102, "N102", id_examinateur=1, examinateur_nom="ProfA",
                       heure_sujet=_td(8), heure_oral=_td(8, 20), heure_fin=_td(8, 40))

        plan = planifier_tiers_temps(
            oral_a, [avant],
            oral_b, [],
            autres_heures_sujet_cascade={}, ecart_mini_minutes=40,
        )
        assert not any(c.id_oral == 9 for c in plan.changements)

    def test_ecart_mini_rompu_signale_sans_bloquer(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 15), heure_fin=_td(11, 30))
        suivant = _oral(3, 101, "N101", id_examinateur=1, examinateur_nom="ProfA",
                         heure_sujet=_td(9, 40), heure_oral=_td(10), heure_fin=_td(10, 20))

        plan = planifier_tiers_temps(
            oral_a, [suivant],
            oral_b, [],
            # Autre oral du candidat 101 très proche de sa nouvelle heure (9h47 + 7min) -> écart rompu
            autres_heures_sujet_cascade={101: _td(10, 10)},
            ecart_mini_minutes=40,
        )
        assert plan.conflit_bloquant is None  # jamais bloquant pour un oral cascadé
        cascade = next(c for c in plan.changements if not c.est_le_candidat)
        assert cascade.ecart_mini_rompu is True

    def test_chevauche_pause_meridienne_signale(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 15), heure_fin=_td(11, 30))
        suivant = _oral(3, 101, "N101", id_examinateur=1, examinateur_nom="ProfA",
                         heure_sujet=_td(9, 40), heure_oral=_td(10), heure_fin=_td(10, 20))

        plan = planifier_tiers_temps(
            oral_a, [suivant],
            oral_b, [],
            autres_heures_sujet_cascade={101: None}, ecart_mini_minutes=0,
            heure_pause_meridienne=_td(10, 5), duree_pause_meridienne=timedelta(minutes=30),
        )
        cascade = next(c for c in plan.changements if not c.est_le_candidat)
        assert cascade.chevauche_pause is True

    def test_conflit_bloquant_si_chevauchement_entre_les_deux_oraux_du_candidat(self):
        # Écart minimum initial très faible (2 min) entre Maths et Philo : après
        # extension des deux préparations, les fenêtres se chevauchent.
        oral_a = _oral(1, 200, "N200", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 200, "N200", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(9, 42), heure_oral=_td(9, 57), heure_fin=_td(10, 12))

        plan = planifier_tiers_temps(
            oral_a, [],
            oral_b, [],
            autres_heures_sujet_cascade={}, ecart_mini_minutes=1,
        )
        assert plan.conflit_bloquant is not None
        assert plan.changements == []


class TestPlanifierTiersTempsRetrait:
    """Retrait d'un tiers-temps posé par erreur (activer=False) : symétrique
    de la déclaration — la préparation actuelle (déjà étendue) retrouve sa
    base, et la cascade décale les oraux suivants plus tôt du même délai."""

    def test_retrait_retrouve_les_horaires_dorigine(self):
        # Repris de test_extension_preparation_candidat, mais déjà étendu :
        # Maths 9h00 -> oral 9h27 -> fin 9h47 (base 20min + 7min)
        # Philo 11h00 -> oral 11h20 -> fin 11h35 (base 15min + 5min)
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 27), heure_fin=_td(9, 47))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 20), heure_fin=_td(11, 35))

        plan = planifier_tiers_temps(
            oral_a, [],
            oral_b, [],
            autres_heures_sujet_cascade={}, ecart_mini_minutes=40, activer=False,
        )
        assert plan.conflit_bloquant is None
        ch_a, ch_b = plan.changements
        assert ch_a.nouvelle_heure_sujet == _td(9)
        assert ch_a.nouvelle_heure_oral == _td(9, 20)
        assert ch_a.nouvelle_heure_fin == _td(9, 40)
        assert ch_b.nouvelle_heure_sujet == _td(11)
        assert ch_b.nouvelle_heure_oral == _td(11, 15)
        assert ch_b.nouvelle_heure_fin == _td(11, 30)

    def test_cascade_decale_les_oraux_suivants_plus_tot(self):
        oral_a = _oral(1, 100, "N100", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 27), heure_fin=_td(9, 47))
        oral_b = _oral(2, 100, "N100", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(11), heure_oral=_td(11, 20), heure_fin=_td(11, 35))
        suivant = _oral(3, 101, "N101", id_examinateur=1, examinateur_nom="ProfA",
                         heure_sujet=_td(9, 47), heure_oral=_td(10, 7), heure_fin=_td(10, 27))

        plan = planifier_tiers_temps(
            oral_a, [suivant],
            oral_b, [],
            autres_heures_sujet_cascade={101: _td(13)}, ecart_mini_minutes=40, activer=False,
        )
        cascade = next(c for c in plan.changements if not c.est_le_candidat)
        assert cascade.nouvelle_heure_sujet == _td(9, 40)
        assert cascade.nouvelle_heure_oral == _td(10)
        assert cascade.nouvelle_heure_fin == _td(10, 20)

    def test_retrait_ne_bloque_jamais_par_chevauchement_propre(self):
        # Même configuration serrée que le test de conflit à la déclaration,
        # mais en retrait : la préparation se réduit, ne peut jamais créer de
        # chevauchement là où il n'y en avait pas.
        oral_a = _oral(1, 200, "N200", id_examinateur=1, examinateur_nom="ProfA",
                        heure_sujet=_td(9), heure_oral=_td(9, 20), heure_fin=_td(9, 40))
        oral_b = _oral(2, 200, "N200", id_examinateur=2, examinateur_nom="ProfB",
                        heure_sujet=_td(9, 42), heure_oral=_td(9, 57), heure_fin=_td(10, 12))

        plan = planifier_tiers_temps(
            oral_a, [],
            oral_b, [],
            autres_heures_sujet_cascade={}, ecart_mini_minutes=1, activer=False,
        )
        assert plan.conflit_bloquant is None
        assert len(plan.changements) == 2
