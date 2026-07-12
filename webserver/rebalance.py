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
    ancien_examinateur_id: int = 0
    """Id de l'examinateur remplacé — 0 si non renseigné (uniquement pour
    lier son nom vers sa fiche d'édition côté template, sans incidence sur
    la logique de replanification)."""

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


def _chevauche_pause(
    heure_oral: timedelta, heure_fin: timedelta,
    heure_pause_meridienne: timedelta | None, duree_pause_meridienne: timedelta,
) -> bool:
    """Vrai si l'examinateur serait occupé (`heure_oral` -> `heure_fin`, cf.
    convention de ce module) pendant la pause méridienne configurée — aucune
    pause configurée (`heure_pause_meridienne` à None) ne bloque jamais rien."""
    if heure_pause_meridienne is None or duree_pause_meridienne <= timedelta(0):
        return False
    pause_fin = heure_pause_meridienne + duree_pause_meridienne
    return heure_fin > heure_pause_meridienne and heure_oral < pause_fin


def creneaux_libres(
    duree_prep: timedelta,
    duree_oral: timedelta,
    occupations_examinateur: list[tuple[timedelta, timedelta]],
    grille_horaires: list[timedelta],
    autre_heure_sujet: timedelta | None,
    ecart_mini_minutes: float,
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> list[timedelta]:
    """Liste les heures de sujet compatibles pour UN examinateur déjà fixé
    (contrairement à `_placer`, qui choisit un couple examinateur/horaire) —
    utilisé par l'écran d'édition manuelle d'un oral pour proposer des
    créneaux une fois l'examinateur sélectionné dans le formulaire.

    Les horaires candidats viennent de `grille_horaires` (heures de sujet
    déjà utilisées aujourd'hui pour cette matière, toutes matières
    confondues) — mêmes contraintes que `_placer` : écart minimum candidat,
    pause méridienne, disponibilité de l'examinateur.
    """
    creneaux = []
    for heure_sujet in sorted(set(grille_horaires)):
        heure_oral = heure_sujet + duree_prep
        heure_fin = heure_oral + duree_oral
        if not _ecart_suffisant(heure_sujet, autre_heure_sujet, ecart_mini_minutes):
            continue
        if _chevauche_pause(heure_oral, heure_fin, heure_pause_meridienne, duree_pause_meridienne):
            continue
        if not _intervalle_libre(occupations_examinateur, heure_oral, heure_fin):
            continue
        creneaux.append(heure_sujet)
    return creneaux


def _placer(
    oral: OralActuel,
    examinateurs: list[ExaminateurCible],
    occupations: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autre_heure_sujet: timedelta | None,
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> Changement | None:
    """
    Cherche un (examinateur, heure_sujet) pour reloger `oral`.

    Priorité 1 : le créneau HORAIRE d'origine (disruption minimale — seul
    l'examinateur change, le candidat garde son heure).

    Priorité 2 (si l'heure d'origine est impossible) : parmi les autres
    heures déjà utilisées aujourd'hui, on privilégie un créneau qui se
    termine juste avant un oral déjà planifié de l'examinateur ciblé — comble
    un trou dans son planning plutôt que d'isoler le nouvel oral loin de ses
    autres oraux. À défaut d'un tel créneau pour un examinateur donné, on
    retombe sur la proximité avec l'heure d'origine de `oral`.

    Un créneau qui ferait travailler l'examinateur pendant la pause
    méridienne configurée (`heure_pause_meridienne`/`duree_pause_meridienne`)
    n'est jamais proposé, à aucune des deux priorités.
    """
    duree_prep = oral.heure_oral - oral.heure_sujet
    duree_oral = oral.heure_fin - oral.heure_oral

    heure_sujet = oral.heure_sujet
    heure_oral = heure_sujet + duree_prep
    heure_fin = heure_oral + duree_oral
    if not _chevauche_pause(heure_oral, heure_fin, heure_pause_meridienne, duree_pause_meridienne):
        for examinateur in examinateurs:
            if examinateur.id == oral.id_examinateur:
                continue  # ne serait pas un déplacement
            if not _examinateur_autorise(examinateur, oral, profs_a_eviter):
                continue
            if not _intervalle_libre(occupations.get(examinateur.id, []), heure_oral, heure_fin):
                continue
            occupations.setdefault(examinateur.id, []).append((heure_oral, heure_fin))
            return Changement(
                id_oral=oral.id, id_candidat=oral.id_candidat, numero=oral.numero,
                ancien_examinateur_id=oral.id_examinateur, ancien_examinateur_nom=oral.examinateur_nom,
                nouvel_examinateur_id=examinateur.id, nouvel_examinateur_nom=examinateur.nom,
                ancienne_heure_sujet=oral.heure_sujet, nouvelle_heure_sujet=heure_sujet,
                nouvelle_heure_oral=heure_oral, nouvelle_heure_fin=heure_fin,
            )

    autres_heures = sorted(
        (h for h in set(grille_horaires) if h != oral.heure_sujet),
        key=lambda h: abs((h - oral.heure_sujet).total_seconds()),
    )

    candidats: list[tuple[int, float, timedelta, timedelta, timedelta, ExaminateurCible]] = []
    for heure_sujet in autres_heures:
        if not _ecart_suffisant(heure_sujet, autre_heure_sujet, ecart_mini_minutes):
            continue
        heure_oral = heure_sujet + duree_prep
        heure_fin = heure_oral + duree_oral
        if _chevauche_pause(heure_oral, heure_fin, heure_pause_meridienne, duree_pause_meridienne):
            continue
        for examinateur in examinateurs:
            if not _examinateur_autorise(examinateur, oral, profs_a_eviter):
                continue
            if not _intervalle_libre(occupations.get(examinateur.id, []), heure_oral, heure_fin):
                continue
            trous_avant = [
                o_debut - heure_fin
                for o_debut, _o_fin in occupations.get(examinateur.id, [])
                if o_debut >= heure_fin
            ]
            if trous_avant:
                rang, cle = 0, min(trous_avant).total_seconds()
            else:
                rang, cle = 1, abs((heure_sujet - oral.heure_sujet).total_seconds())
            candidats.append((rang, cle, heure_sujet, heure_oral, heure_fin, examinateur))

    if not candidats:
        return None
    candidats.sort(key=lambda c: (c[0], c[1]))
    _, _, heure_sujet, heure_oral, heure_fin, examinateur = candidats[0]
    occupations.setdefault(examinateur.id, []).append((heure_oral, heure_fin))
    return Changement(
        id_oral=oral.id, id_candidat=oral.id_candidat, numero=oral.numero,
        ancien_examinateur_id=oral.id_examinateur, ancien_examinateur_nom=oral.examinateur_nom,
        nouvel_examinateur_id=examinateur.id, nouvel_examinateur_nom=examinateur.nom,
        ancienne_heure_sujet=oral.heure_sujet, nouvelle_heure_sujet=heure_sujet,
        nouvelle_heure_oral=heure_oral, nouvelle_heure_fin=heure_fin,
    )


def planifier_absence(
    oraux_a_reaffecter: list[OralActuel],
    examinateurs_disponibles: list[ExaminateurCible],
    occupations_initiales: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires: list[timedelta],
    autres_heures_sujet: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
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
    :param heure_pause_meridienne: heure de début de la pause méridienne configurée
        (None = désactivée), jamais proposée comme nouveau créneau
    :param duree_pause_meridienne: durée de la pause méridienne
    """
    occupations = {k: list(v) for k, v in occupations_initiales.items()}
    plan = PlanRebalancement()
    for oral in sorted(oraux_a_reaffecter, key=lambda o: o.heure_sujet):
        changement = _placer(
            oral, examinateurs_disponibles, occupations, grille_horaires,
            autres_heures_sujet.get(oral.id_candidat), ecart_mini_minutes, profs_a_eviter,
            heure_pause_meridienne, duree_pause_meridienne,
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
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
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
    :param heure_pause_meridienne: heure de début de la pause méridienne configurée
        (None = désactivée), jamais proposée comme nouveau créneau
    :param duree_pause_meridienne: durée de la pause méridienne
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
            heure_pause_meridienne, duree_pause_meridienne,
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
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> list[timedelta]:
    """Prolonge la grille horaire au-delà du dernier horaire déjà utilisé
    aujourd'hui, par pas de `duree_creneau`, jusqu'à `plafond_minutes` de plus
    — pour proposer de véritables nouveaux créneaux (palier « extension »),
    plutôt que de se limiter aux horaires déjà utilisés pour cette matière.

    Si une pause méridienne est configurée, aucun nouveau créneau n'est
    généré à l'intérieur (estimation prudente sur la base de `duree_creneau`,
    faute de connaître à l'avance la préparation/durée exacte du futur oral)
    — l'extension saute directement à la fin de la pause et `plafond_minutes`
    est prolongé d'autant, pour conserver la même amplitude réelle
    d'extension malgré le saut."""
    if not grille_horaires:
        return list(grille_horaires)
    grille = set(grille_horaires)
    dernier = max(grille_horaires)
    limite = dernier + timedelta(minutes=plafond_minutes)
    t = dernier + duree_creneau
    while t <= limite:
        if (
            heure_pause_meridienne is not None
            and duree_pause_meridienne > timedelta(0)
            and t + duree_creneau > heure_pause_meridienne
            and t < heure_pause_meridienne + duree_pause_meridienne
        ):
            saut = heure_pause_meridienne + duree_pause_meridienne - t
            t += saut
            limite += saut
            continue
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
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
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
    :param heure_pause_meridienne: heure de début de la pause méridienne
        configurée (None = désactivée) — aucune variable n'est créée pour un
        (oral, examinateur, heure_sujet) qui ferait travailler l'examinateur
        pendant la pause, quelle que soit l'origine de `grille_horaires`.
    :param duree_pause_meridienne: durée de la pause méridienne
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
                if _chevauche_pause(
                    heure_oral, heure_fin, heure_pause_meridienne, duree_pause_meridienne,
                ):
                    continue
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
            ancien_examinateur_id=oral.id_examinateur, ancien_examinateur_nom=oral.examinateur_nom,
            nouvel_examinateur_id=examinateur.id, nouvel_examinateur_nom=examinateur.nom,
            ancienne_heure_sujet=oral.heure_sujet, nouvelle_heure_sujet=heure_sujet,
            nouvelle_heure_oral=heure_oral, nouvelle_heure_fin=heure_fin,
            hors_grille=heure_sujet not in grille_reference,
        ))
        resolus.add(id_oral)

    plan.non_replaces = [o for o in oraux if o.id not in resolus]
    return plan


# ── Changement de matière d'un candidat en cours de journée ──────────────────
# Un candidat change de matière le jour J (après le placement initial) : un
# seul de ses deux oraux doit être remplacé par un nouveau dans la matière
# choisie — son autre oral reste fixe et sert de référence pour l'écart
# minimum, exactement comme pour une absence d'examinateur. On réutilise donc
# `_placer` (même horaire d'abord, repli sur la grille sinon) et, en cas
# d'échec, `resoudre_oraux_difficiles` (paliers 2/3) exactement comme pour la
# disponibilité d'un examinateur — cf. app.py.

def planifier_changement_matiere(
    oral_a_remplacer: OralActuel,
    examinateurs_nouvelle_matiere: list[ExaminateurCible],
    occupations_nouvelle_matiere: dict[int, list[tuple[timedelta, timedelta]]],
    grille_horaires_nouvelle_matiere: list[timedelta],
    autre_heure_sujet: timedelta | None,
    ecart_mini_minutes: float,
    profs_a_eviter: dict[str, list[str]],
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> Changement | None:
    """
    Cherche un nouvel (examinateur, horaire) dans la nouvelle matière pour un
    candidat qui en change — même heure d'abord (disruption minimale), repli
    sur la grille horaire déjà utilisée aujourd'hui pour cette matière sinon.

    :param oral_a_remplacer: l'oral actuel de l'ANCIENNE matière (son
        `id_examinateur`/`examinateur_nom` ne sont utilisés que pour ignorer
        un « swap » vers soi-même — sans effet ici puisque la nouvelle
        matière a des examinateurs différents)
    :param heure_pause_meridienne: heure de début de la pause méridienne
        configurée (None = désactivée), jamais proposée comme nouveau créneau
    :param duree_pause_meridienne: durée de la pause méridienne
    """
    return _placer(
        oral_a_remplacer, examinateurs_nouvelle_matiere, occupations_nouvelle_matiere,
        grille_horaires_nouvelle_matiere, autre_heure_sujet, ecart_mini_minutes, profs_a_eviter,
        heure_pause_meridienne, duree_pause_meridienne,
    )


def proposer_compaction(
    oraux_examinateur_libere: list[OralActuel],
    creneau_libere: timedelta,
    autres_heures_sujet: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> Changement | None:
    """
    Suggestion optionnelle (jamais appliquée automatiquement) : une fois le
    créneau `creneau_libere` vacant chez un examinateur (suite au changement
    de matière d'un candidat), propose de déplacer son oral le plus tardif
    dans ce créneau — compacte son planning et libère du temps en fin de
    journée pour cet examinateur.

    Aucune vérification d'exclusion n'est nécessaire ici (même examinateur
    qu'avant pour le candidat déplacé, donc établissement/prof à éviter
    restent valides par construction) — seul l'écart minimum avec son autre
    oral est revérifié au nouveau créneau, plus précoce, ainsi que la pause
    méridienne configurée.

    :param oraux_examinateur_libere: les oraux ACTUELS de cet examinateur
        pour cette matière (hors l'oral qui vient d'être libéré)
    :param heure_pause_meridienne: heure de début de la pause méridienne
        configurée (None = désactivée)
    :param duree_pause_meridienne: durée de la pause méridienne
    """
    candidats_deplacables = [o for o in oraux_examinateur_libere if o.heure_sujet > creneau_libere]
    if not candidats_deplacables:
        return None
    plus_tardif = max(candidats_deplacables, key=lambda o: o.heure_sujet)

    if not _ecart_suffisant(
        creneau_libere, autres_heures_sujet.get(plus_tardif.id_candidat), ecart_mini_minutes,
    ):
        return None

    duree_prep = plus_tardif.heure_oral - plus_tardif.heure_sujet
    duree_oral = plus_tardif.heure_fin - plus_tardif.heure_oral
    nouvelle_heure_oral = creneau_libere + duree_prep
    nouvelle_heure_fin = nouvelle_heure_oral + duree_oral
    if _chevauche_pause(
        nouvelle_heure_oral, nouvelle_heure_fin, heure_pause_meridienne, duree_pause_meridienne,
    ):
        return None
    return Changement(
        id_oral=plus_tardif.id, id_candidat=plus_tardif.id_candidat, numero=plus_tardif.numero,
        ancien_examinateur_id=plus_tardif.id_examinateur, ancien_examinateur_nom=plus_tardif.examinateur_nom,
        nouvel_examinateur_id=plus_tardif.id_examinateur, nouvel_examinateur_nom=plus_tardif.examinateur_nom,
        ancienne_heure_sujet=plus_tardif.heure_sujet, nouvelle_heure_sujet=creneau_libere,
        nouvelle_heure_oral=nouvelle_heure_oral, nouvelle_heure_fin=nouvelle_heure_fin,
    )


# ── Déclaration (ou retrait) de tiers-temps d'un candidat en cours de journée
# Un candidat déclare un tiers-temps le jour J (après le placement initial) :
# ses deux oraux voient leur temps de préparation étendu d'1/3 (même règle que
# AlgoOne.calcul_horaires pour un candidat tiers-temps à la construction du
# planning) — seule l'heure d'oral (et donc de fin) recule, l'heure de sujet
# (début de préparation) reste inchangée. Comme l'examinateur reste occupé
# plus longtemps, tous les oraux suivants chez ce même examinateur ce jour-là
# doivent être décalés du même delta pour ne jamais se chevaucher.
#
# Le retrait d'un tiers-temps posé par erreur inverse exactement ce calcul :
# la préparation actuelle (déjà étendue) représente 4/3 de la préparation de
# base, donc on lui retire 1/4 pour retrouver cette base — et la cascade
# décale les oraux suivants plus TÔT du même delta (négatif), sans jamais
# créer de nouveau chevauchement (un décalage uniforme d'une file d'oraux
# préserve tous les écarts déjà en place, quel que soit le sens).

def _arrondir_minute(td: timedelta) -> timedelta:
    """Arrondit un timedelta à la minute la plus proche — même granularité que
    AlgoOne.ajouter_temps (algo.py), pour rester cohérent avec les horaires
    déjà publiés (toujours en minutes entières) et éviter qu'un arrondi à la
    seconde ne soit silencieusement tronqué par _td_to_time_str (HH:MM)."""
    return timedelta(minutes=round(td.total_seconds() / 60))


@dataclass(frozen=True)
class ChangementTiersTemps:
    """Un oral dont l'horaire est décalé suite à la déclaration de tiers-temps
    d'un candidat — soit l'un des deux oraux du candidat lui-même (préparation
    étendue), soit un oral cascadé chez le même examinateur (décalé du même
    delta pour préserver l'écart déjà en place, sans jamais le réduire)."""
    id_oral: int
    id_candidat: int
    numero: str
    id_examinateur: int
    examinateur_nom: str
    ancienne_heure_sujet: timedelta
    ancienne_heure_oral: timedelta
    ancienne_heure_fin: timedelta
    nouvelle_heure_sujet: timedelta
    nouvelle_heure_oral: timedelta
    nouvelle_heure_fin: timedelta
    est_le_candidat: bool
    """Vrai si c'est un des deux oraux du candidat qui déclare le tiers-temps
    (préparation étendue), faux si c'est un oral cascadé (horaire décalé)."""
    ecart_mini_rompu: bool = False
    """Uniquement pertinent pour un oral cascadé : le décalage de son heure de
    sujet romprait l'écart minimum avec son AUTRE oral (dans une autre
    matière, non affecté) — à signaler, jamais bloquant automatiquement."""
    chevauche_pause: bool = False
    """Le nouvel horaire (heure_oral -> heure_fin) chevauche la pause
    méridienne configurée — à signaler à l'examinateur concerné."""


@dataclass
class PlanTiersTemps:
    """Résultat de planifier_tiers_temps() : la liste des oraux affectés
    (candidat + cascade), et un éventuel conflit bloquant empêchant toute
    application automatique."""
    changements: list[ChangementTiersTemps] = field(default_factory=list)
    conflit_bloquant: str | None = None
    """Non None si les deux oraux du candidat lui-même se chevauperaient une
    fois leur préparation étendue (très rare : suppose un écart minimum déjà
    proche de zéro, et ne peut se produire qu'à la déclaration — un retrait
    ne fait que réduire les fenêtres, jamais les faire chevaucher) — aucun
    changement ne doit alors être appliqué, une intervention manuelle
    (édition d'oral) est nécessaire."""


def planifier_tiers_temps(
    oral_a: OralActuel,
    oraux_examinateur_a: list[OralActuel],
    oral_b: OralActuel,
    oraux_examinateur_b: list[OralActuel],
    autres_heures_sujet_cascade: dict[int, timedelta | None],
    ecart_mini_minutes: float,
    activer: bool = True,
    heure_pause_meridienne: timedelta | None = None,
    duree_pause_meridienne: timedelta = timedelta(0),
) -> PlanTiersTemps:
    """
    Calcule le décalage à appliquer pour déclarer (ou retirer) le tiers-temps
    d'un candidat.

    :param oral_a: un des deux oraux actuels du candidat (matière A) — le
        temps de préparation actuel se déduit de `oral_a.heure_oral - oral_a.heure_sujet`
    :param oraux_examinateur_a: les AUTRES oraux du jour de l'examinateur de
        la matière A (hors `oral_a`)
    :param oral_b, oraux_examinateur_b: idem pour la matière B
    :param autres_heures_sujet_cascade: {id_candidat: heure_sujet de son AUTRE
        oral (fixe), ou None} pour les candidats dont l'oral est cascadé —
        sert à vérifier que l'écart minimum reste respecté après décalage
    :param activer: True pour déclarer le tiers-temps (préparation actuelle
        considérée NON étendue, on lui ajoute 1/3), False pour le retirer
        (préparation actuelle considérée étendue à 4/3, on lui retire 1/4
        pour retrouver la base — cf. commentaire de section ci-dessus)
    :param heure_pause_meridienne, duree_pause_meridienne: pause méridienne
        configurée (None = désactivée)
    """
    plan = PlanTiersTemps()
    changements_propres: list[ChangementTiersTemps] = []
    changements_cascade: list[ChangementTiersTemps] = []

    def _traiter(oral: OralActuel, oraux_examinateur: list[OralActuel]) -> None:
        gap_actuel = oral.heure_oral - oral.heure_sujet
        delta = _arrondir_minute(gap_actuel / 3) if activer else -_arrondir_minute(gap_actuel / 4)
        nouvelle_heure_oral = oral.heure_oral + delta
        nouvelle_heure_fin = oral.heure_fin + delta
        changements_propres.append(ChangementTiersTemps(
            id_oral=oral.id, id_candidat=oral.id_candidat, numero=oral.numero,
            id_examinateur=oral.id_examinateur, examinateur_nom=oral.examinateur_nom,
            ancienne_heure_sujet=oral.heure_sujet, ancienne_heure_oral=oral.heure_oral,
            ancienne_heure_fin=oral.heure_fin,
            nouvelle_heure_sujet=oral.heure_sujet, nouvelle_heure_oral=nouvelle_heure_oral,
            nouvelle_heure_fin=nouvelle_heure_fin, est_le_candidat=True,
        ))
        for autre in oraux_examinateur:
            if autre.heure_sujet <= oral.heure_sujet:
                continue  # avant le candidat tiers-temps ce jour-là : non concerné
            nouvelle_hs = autre.heure_sujet + delta
            nouvelle_ho = autre.heure_oral + delta
            nouvelle_hf = autre.heure_fin + delta
            ecart_rompu = not _ecart_suffisant(
                nouvelle_hs, autres_heures_sujet_cascade.get(autre.id_candidat), ecart_mini_minutes,
            )
            chevauche_pause = _chevauche_pause(
                nouvelle_ho, nouvelle_hf, heure_pause_meridienne, duree_pause_meridienne,
            )
            changements_cascade.append(ChangementTiersTemps(
                id_oral=autre.id, id_candidat=autre.id_candidat, numero=autre.numero,
                id_examinateur=autre.id_examinateur, examinateur_nom=autre.examinateur_nom,
                ancienne_heure_sujet=autre.heure_sujet, ancienne_heure_oral=autre.heure_oral,
                ancienne_heure_fin=autre.heure_fin,
                nouvelle_heure_sujet=nouvelle_hs, nouvelle_heure_oral=nouvelle_ho,
                nouvelle_heure_fin=nouvelle_hf, est_le_candidat=False,
                ecart_mini_rompu=ecart_rompu, chevauche_pause=chevauche_pause,
            ))

    _traiter(oral_a, oraux_examinateur_a)
    _traiter(oral_b, oraux_examinateur_b)

    ch_a, ch_b = changements_propres
    if (
        ch_a.nouvelle_heure_sujet < ch_b.nouvelle_heure_fin
        and ch_b.nouvelle_heure_sujet < ch_a.nouvelle_heure_fin
    ):
        plan.conflit_bloquant = (
            "Les deux oraux du candidat se chevaucheraient une fois la préparation "
            "étendue pour le tiers-temps — écart minimum déjà trop faible entre les "
            "deux matières. Résolution manuelle nécessaire (édition d'oral)."
        )
        return plan

    plan.changements = changements_propres + changements_cascade
    return plan
