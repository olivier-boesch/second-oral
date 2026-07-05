"""
Rééquilibrage des oraux d'une matière suite à un changement de disponibilité
d'un examinateur (absence, retard, renfort), en cours de journée.

Contexte : contrairement à `algo.py` (placement initial, à partir d'un jeu de
CSV et d'une grille de créneaux abstraite), ce module opère sur un planning
DÉJÀ publié en base — les horaires sont des heures réelles (`timedelta`), pas
des indices de créneau. La logique de non-chevauchement reprend donc celle
déjà utilisée par `_check_conflits_oral()` dans app.py (chevauchement candidat
sur tout le slot [heure_sujet, heure_fin], chevauchement examinateur sur
[heure_oral, heure_fin] seulement), et la « grille horaire » disponible pour
replacer un oral est simplement l'ensemble des heures de sujet déjà utilisées
aujourd'hui pour cette matière — pas besoin de connaître heure_debut ou les
durées de préparation/oral configurées dans preps.csv.

Un retard (arrivée tardive) est modélisé comme une absence sur la fenêtre
avant l'arrivée, suivie d'un renfort sur la fenêtre après l'arrivée — mêmes
fonctions, deux appels successifs avec la même heure pivot (cf. app.py).
"""
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class OralActuel:
    """Un oral déjà planifié pour la matière concernée."""
    id: int
    id_candidat: int
    numero: str
    etablissement: str
    id_examinateur: int
    examinateur_nom: str
    heure_sujet: timedelta
    heure_oral: timedelta
    heure_fin: timedelta


@dataclass(frozen=True)
class ExaminateurCible:
    """Un examinateur candidat pour recevoir un oral réaffecté."""
    id: int
    nom: str
    etablissements: list[str]


@dataclass(frozen=True)
class Changement:
    """Une réaffectation proposée pour un oral."""
    id_oral: int
    id_candidat: int
    numero: str
    ancien_examinateur_nom: str
    nouvel_examinateur_id: int
    nouvel_examinateur_nom: str
    ancienne_heure_sujet: timedelta
    nouvelle_heure_sujet: timedelta
    nouvelle_heure_oral: timedelta
    nouvelle_heure_fin: timedelta

    @property
    def meme_heure(self) -> bool:
        """Vrai si seul l'examinateur change (aucune disruption d'horaire pour le candidat)."""
        return self.ancienne_heure_sujet == self.nouvelle_heure_sujet


@dataclass
class PlanRebalancement:
    """Résultat d'une planification : changements proposés + oraux non replacés."""
    changements: list[Changement] = field(default_factory=list)
    non_replaces: list[OralActuel] = field(default_factory=list)

    def etendre(self, autre: "PlanRebalancement") -> None:
        self.changements.extend(autre.changements)
        self.non_replaces.extend(autre.non_replaces)


# ── Vérifications de contraintes (mêmes règles que _check_conflits_oral /
#    Examinateur.recherche_creneau dans algo.py, adaptées aux horaires réels) ─

def _intervalle_libre(
    occupations: list[tuple[timedelta, timedelta]], debut: timedelta, fin: timedelta,
) -> bool:
    return all(fin <= o_debut or debut >= o_fin for o_debut, o_fin in occupations)


def _examinateur_autorise(
    examinateur: ExaminateurCible, oral: OralActuel, profs_a_eviter: dict[str, list[str]],
) -> bool:
    if examinateur.etablissements not in ([], ['']) and oral.etablissement in examinateur.etablissements:
        return False
    profs = profs_a_eviter.get(oral.numero, [])
    if profs not in ([], ['']) and examinateur.nom in profs:
        return False
    return True


def _ecart_suffisant(
    nouvelle_heure_sujet: timedelta, autre_heure_sujet: timedelta | None, ecart_mini_minutes: float,
) -> bool:
    if autre_heure_sujet is None:
        return True
    gap_min = abs((nouvelle_heure_sujet - autre_heure_sujet).total_seconds()) / 60
    return gap_min >= ecart_mini_minutes


def _placer(
    oral: OralActuel,
    examinateurs: list[ExaminateurCible],
    occupations: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autre_heure_sujet: timedelta | None,
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
) -> Changement | None:
    """
    Cherche un (examinateur, heure_sujet) pour reloger `oral`.

    Priorité stricte : le créneau HORAIRE d'origine d'abord (disruption
    minimale — seul l'examinateur change, le candidat garde son heure), puis
    les autres heures déjà utilisées aujourd'hui pour cette matière, triées
    par proximité avec l'heure d'origine.
    """
    duree_prep = oral.heure_oral - oral.heure_sujet
    duree_oral = oral.heure_fin - oral.heure_oral

    autres_heures = sorted(
        (h for h in set(grille_horaires) if h != oral.heure_sujet),
        key=lambda h: abs((h - oral.heure_sujet).total_seconds()),
    )
    candidats_horaires = [oral.heure_sujet] + autres_heures

    for heure_sujet in candidats_horaires:
        heure_oral = heure_sujet + duree_prep
        heure_fin = heure_oral + duree_oral
        if heure_sujet != oral.heure_sujet and not _ecart_suffisant(
            heure_sujet, autre_heure_sujet, ecart_mini_minutes,
        ):
            continue
        for examinateur in examinateurs:
            if examinateur.id == oral.id_examinateur and heure_sujet == oral.heure_sujet:
                continue  # ne serait pas un déplacement
            if not _examinateur_autorise(examinateur, oral, profs_a_eviter):
                continue
            if not _intervalle_libre(occupations.get(examinateur.id, []), heure_oral, heure_fin):
                continue
            occupations.setdefault(examinateur.id, []).append((heure_oral, heure_fin))
            return Changement(
                id_oral=oral.id, id_candidat=oral.id_candidat, numero=oral.numero,
                ancien_examinateur_nom=oral.examinateur_nom,
                nouvel_examinateur_id=examinateur.id, nouvel_examinateur_nom=examinateur.nom,
                ancienne_heure_sujet=oral.heure_sujet, nouvelle_heure_sujet=heure_sujet,
                nouvelle_heure_oral=heure_oral, nouvelle_heure_fin=heure_fin,
            )
    return None


def planifier_absence(
    oraux_a_reaffecter: list[OralActuel],
    examinateurs_disponibles: list[ExaminateurCible],
    occupations_initiales: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autres_heures_sujet: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
) -> PlanRebalancement:
    """
    Réaffecte les oraux d'un examinateur indisponible vers les autres
    examinateurs de la même matière, en traitant d'abord les oraux les plus
    tôt dans la journée (ceux-ci ont le moins d'options de repli).

    :param oraux_a_reaffecter: oraux de l'examinateur absent, dans la fenêtre d'indisponibilité
    :param examinateurs_disponibles: les AUTRES examinateurs de la matière
    :param occupations_initiales: {id_examinateur: [(heure_oral, heure_fin), ...]} —
        état actuel de leur planning (hors oraux à réaffecter)
    :param grille_horaires: heures de sujet déjà utilisées aujourd'hui pour cette matière
    :param autres_heures_sujet: {id_candidat: heure_sujet de son AUTRE oral (fixe), ou None}
    :param ecart_mini_minutes: écart minimum requis entre les deux oraux d'un candidat
    :param profs_a_eviter: {numero_candidat: [noms de profs à éviter]}
    """
    occupations = {k: list(v) for k, v in occupations_initiales.items()}
    plan = PlanRebalancement()
    for oral in sorted(oraux_a_reaffecter, key=lambda o: o.heure_sujet):
        changement = _placer(
            oral, examinateurs_disponibles, occupations, grille_horaires,
            autres_heures_sujet.get(oral.id_candidat), ecart_mini_minutes, profs_a_eviter,
        )
        if changement is not None:
            plan.changements.append(changement)
        else:
            plan.non_replaces.append(oral)
    return plan


def planifier_renfort(
    oraux_deplacables: list[OralActuel],
    examinateur_renfort: ExaminateurCible,
    occupations_initiales: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autres_heures_sujet: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
    charge_par_examinateur: dict[int, int],
) -> PlanRebalancement:
    """
    Décharge les examinateurs les plus chargés de la matière vers un
    examinateur qui vient de devenir disponible (renfort, ou retour de
    retard), jusqu'à équilibrage de la charge.

    Contrairement à planifier_absence, aucun déplacement n'est obligatoire
    ici : c'est une optimisation de confort, pas une réparation d'un
    problème de faisabilité — un oral qui ne peut pas être déplacé reste
    simplement chez son examinateur actuel (jamais ajouté à `non_replaces`).

    :param oraux_deplacables: oraux des AUTRES examinateurs, dans la fenêtre
        où le renfort est disponible (candidats au déplacement)
    :param examinateur_renfort: l'examinateur qui devient disponible
    :param charge_par_examinateur: {id_examinateur: nb d'oraux restants dans
        la fenêtre}, incluant le renfort (à 0 au départ)
    """
    occupations = {k: list(v) for k, v in occupations_initiales.items()}
    charge = dict(charge_par_examinateur)
    plan = PlanRebalancement()

    if not charge:
        return plan
    moyenne_cible = sum(charge.values()) / len(charge)

    # Décharge d'abord les oraux les plus tardifs des examinateurs les plus
    # chargés : ce sont ceux qui contribuent le plus à l'écart de charge et
    # les plus simples à replacer (le moins de contraintes en aval).
    ordre = sorted(
        oraux_deplacables,
        key=lambda o: (-charge.get(o.id_examinateur, 0), -o.heure_sujet.total_seconds()),
    )

    for oral in ordre:
        if charge.get(oral.id_examinateur, 0) <= moyenne_cible:
            continue
        changement = _placer(
            oral, [examinateur_renfort], occupations, grille_horaires,
            autres_heures_sujet.get(oral.id_candidat), ecart_mini_minutes, profs_a_eviter,
        )
        if changement is not None:
            plan.changements.append(changement)
            charge[oral.id_examinateur] = charge.get(oral.id_examinateur, 0) - 1
            charge[examinateur_renfort.id] = charge.get(examinateur_renfort.id, 0) + 1

    return plan
