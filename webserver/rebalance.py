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
    hors_grille: bool = False
    """Vrai si l'heure proposée n'a jamais été utilisée aujourd'hui pour cette
    matière — càd un créneau réellement nouveau (palier « extension d'horaire »
    de resoudre_oraux_difficiles), pas juste un horaire déjà existant réutilisé."""

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


# ── Résolution exacte (CP-SAT) pour les oraux que le glouton n'a pas su
#    replacer — cf. discussion produit : le glouton (_placer) est un premier
#    essai non exhaustif ; un solveur exact peut réussir là où il échoue,
#    d'abord sur la même grille horaire, puis (si toujours infaisable) sur une
#    grille étendue au-delà des heures déjà utilisées aujourd'hui. ──────────

def duree_creneau_estimee(grille_horaires: list[timedelta], oraux: list[OralActuel]) -> timedelta:
    """Estime la durée d'un créneau pour cette matière, à partir de l'écart
    entre les heures de sujet déjà utilisées (ou, à défaut, de la durée totale
    du premier oral fourni) — sert à générer de nouveaux créneaux plausibles
    au-delà de la grille actuelle."""
    valeurs = sorted(set(grille_horaires))
    ecarts = [b - a for a, b in zip(valeurs, valeurs[1:]) if b > a]
    if ecarts:
        return min(ecarts)
    if oraux:
        return oraux[0].heure_fin - oraux[0].heure_sujet
    return timedelta(minutes=20)


def construire_grille_etendue(
    grille_horaires: list[timedelta], duree_creneau: timedelta, plafond_minutes: int = 120,
) -> list[timedelta]:
    """Prolonge la grille horaire au-delà du dernier horaire déjà utilisé
    aujourd'hui, par pas de `duree_creneau`, jusqu'à `plafond_minutes` de plus
    — pour proposer de véritables nouveaux créneaux (palier « extension »),
    plutôt que de se limiter aux horaires déjà utilisés pour cette matière."""
    if not grille_horaires:
        return list(grille_horaires)
    grille = set(grille_horaires)
    dernier = max(grille_horaires)
    limite = dernier + timedelta(minutes=plafond_minutes)
    t = dernier + duree_creneau
    while t <= limite:
        grille.add(t)
        t += duree_creneau
    return sorted(grille)


def resoudre_oraux_difficiles(
    oraux: list[OralActuel],
    examinateurs: list[ExaminateurCible],
    occupations_initiales: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autres_heures_sujet: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
    grille_initiale: list[timedelta] | None = None,
) -> PlanRebalancement:
    """
    Résout par CP-SAT (Google OR-Tools) les oraux qu'un placement glouton
    (`_placer`, via `planifier_absence`) n'a pas réussi à replacer.

    Contrairement au glouton — qui s'arrête au premier échec — cette
    résolution est exhaustive sur le sous-problème donné : si une solution
    existe compte tenu de `grille_horaires` et `examinateurs`, elle sera
    trouvée ; sinon `non_replaces` contient les oraux réellement infaisables
    dans ce cadre (signal fiable pour décider d'élargir la grille — palier
    suivant — ou de renoncer à l'automatisation).

    :param grille_horaires: heures candidates pour le repositionnement —
        peut être la grille du jour telle quelle (palier « même grille ») ou
        une grille étendue via `construire_grille_etendue` (palier
        « extension d'horaire »).
    :param grille_initiale: grille du jour AVANT extension éventuelle, pour
        marquer `Changement.hors_grille` — si None, `grille_horaires` sert
        aussi de référence (donc `hors_grille` toujours faux).
    """
    from ortools.sat.python import cp_model

    if not oraux:
        return PlanRebalancement()

    grille_reference = set(grille_initiale if grille_initiale is not None else grille_horaires)
    oral_par_id = {o.id: o for o in oraux}
    model = cp_model.CpModel()

    x: dict[tuple[int, int, timedelta], "cp_model.IntVar"] = {}
    intervalles_par_examinateur: dict[int, list] = {}

    # Intervalles fixes : oraux déjà en place chez les autres examinateurs,
    # qui ne bougent pas pendant cette résolution.
    for id_examinateur, occ in occupations_initiales.items():
        for debut, fin in occ:
            iv = model.NewIntervalVar(  # type: ignore[attr-defined]
                int(debut.total_seconds()), int((fin - debut).total_seconds()),
                int(fin.total_seconds()), f"fixe_{id_examinateur}_{int(debut.total_seconds())}",
            )
            intervalles_par_examinateur.setdefault(id_examinateur, []).append(iv)

    for oral in oraux:
        duree_prep = oral.heure_oral - oral.heure_sujet
        duree_oral = oral.heure_fin - oral.heure_oral
        vars_oral = []
        for examinateur in examinateurs:
            if not _examinateur_autorise(examinateur, oral, profs_a_eviter):
                continue
            for heure_sujet in grille_horaires:
                if heure_sujet != oral.heure_sujet and not _ecart_suffisant(
                    heure_sujet, autres_heures_sujet.get(oral.id_candidat), ecart_mini_minutes,
                ):
                    continue
                heure_oral = heure_sujet + duree_prep
                heure_fin = heure_oral + duree_oral
                var = model.NewBoolVar(  # type: ignore[attr-defined]
                    f"x_{oral.id}_{examinateur.id}_{int(heure_sujet.total_seconds())}"
                )
                iv = model.NewOptionalIntervalVar(  # type: ignore[attr-defined]
                    int(heure_oral.total_seconds()), int(duree_oral.total_seconds()),
                    int(heure_fin.total_seconds()), var,
                    f"opt_{oral.id}_{examinateur.id}_{int(heure_sujet.total_seconds())}",
                )
                intervalles_par_examinateur.setdefault(examinateur.id, []).append(iv)
                x[(oral.id, examinateur.id, heure_sujet)] = var
                vars_oral.append(var)
        if vars_oral:
            model.AddExactlyOne(vars_oral)  # type: ignore[attr-defined]
        # sinon : aucune option même en théorie pour cet oral — il restera
        # dans non_replaces (absent de x, jamais sélectionné).

    for intervalles in intervalles_par_examinateur.values():
        model.AddNoOverlap(intervalles)  # type: ignore[attr-defined]

    if not x:
        return PlanRebalancement(non_replaces=list(oraux))

    # Objectif : minimiser la disruption (rester le plus proche possible de
    # l'heure d'origine), même logique de priorité que le glouton _placer.
    model.Minimize(sum(  # type: ignore[attr-defined]
        int(abs((heure_sujet - oral_par_id[id_oral].heure_sujet).total_seconds())) * var
        for (id_oral, _id_examinateur, heure_sujet), var in x.items()
    ))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    plan = PlanRebalancement()
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan.non_replaces = list(oraux)
        return plan

    examinateur_par_id = {e.id: e for e in examinateurs}
    resolus: set[int] = set()
    for (id_oral, id_examinateur, heure_sujet), var in x.items():
        if not solver.Value(var):
            continue
        oral = oral_par_id[id_oral]
        examinateur = examinateur_par_id[id_examinateur]
        duree_prep = oral.heure_oral - oral.heure_sujet
        duree_oral = oral.heure_fin - oral.heure_oral
        heure_oral = heure_sujet + duree_prep
        heure_fin = heure_oral + duree_oral
        plan.changements.append(Changement(
            id_oral=oral.id, id_candidat=oral.id_candidat, numero=oral.numero,
            ancien_examinateur_nom=oral.examinateur_nom,
            nouvel_examinateur_id=examinateur.id, nouvel_examinateur_nom=examinateur.nom,
            ancienne_heure_sujet=oral.heure_sujet, nouvelle_heure_sujet=heure_sujet,
            nouvelle_heure_oral=heure_oral, nouvelle_heure_fin=heure_fin,
            hors_grille=heure_sujet not in grille_reference,
        ))
        resolus.add(id_oral)

    plan.non_replaces = [o for o in oraux if o.id not in resolus]
    return plan
