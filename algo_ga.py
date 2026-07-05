#!/usr/bin/python3
"""
Troisième algorithme de placement des oraux : algorithme génétique.

Alternative à AlgoOne.resoudre() (glouton Monte-Carlo) et à AlgoCP.resoudre()
(CP-SAT) : fait évoluer une population de placements par sélection/croisement/
mutation, avec réparation locale et pénalités pour les contraintes non
garanties structurellement. Intérêt principal par rapport aux deux autres
moteurs : la fonction de fitness peut absorber facilement des critères
d'optimisation supplémentaires (préférences, équité de charge...) sans
reformuler tout un modèle de contraintes, au prix d'une garantie plus faible
que CP-SAT sur le respect strict de l'écart minimum candidat.
"""
import random
import time as _time
from dataclasses import dataclass

from algo import (
    AlgoOne,
    AlgoError,
    Candidat,
    CreneauInterdit,
    Examinateur,
    Matiere,
    PasDeCreneauDisponible,
    _env_float,
    _env_int,
    log,
)

ALGO_GA_POPULATION    = _env_int("ALGO_GA_POPULATION", 150)
ALGO_GA_GENERATIONS   = _env_int("ALGO_GA_GENERATIONS", 300)
ALGO_GA_TIMEOUT       = _env_int("ALGO_GA_TIMEOUT", 60)
ALGO_GA_MUTATION_RATE = _env_float("ALGO_GA_MUTATION_RATE", 0.15)


class AucuneSolutionGA(AlgoError):
    """L'algorithme génétique n'a pas produit de solution utilisable."""

    def __init__(self, message: str):
        super().__init__(f"Algorithme génétique : {message}")


@dataclass
class _Evaluation:
    """Résultat de l'évaluation du fitness d'un individu."""
    fitness: float
    occupation_pct: float
    violations_exclusion: int
    violations_ecart: int
    desequilibre_charge: int


# Un individu est un dict {id(matiere): permutation}, où `permutation` est une
# permutation des indices de matiere_data[id(matiere)]['slots'] : seuls les
# len(candidats) premiers gènes sont réellement affectés (le reste correspond
# à des créneaux non utilisés). L'unicité garantie par une permutation évite
# structurellement qu'un même créneau soit attribué à deux candidats.
_Individu = dict[int, list[int]]


class AlgoGA(AlgoOne):
    """Variante de AlgoOne qui résout l'appairage par algorithme génétique.

    Réutilise tel quel setup_from_files / calcul_horaires / statistiques /
    save / sauvegarder_oraux de AlgoOne — seule resoudre() change de logique.
    """

    _LOG_INTERVAL_S = 2.0
    _STAGNATION_LIMIT = 30
    _TOURNAMENT_SIZE = 3
    _PENALITE_EXCLUSION = 1000
    _PENALITE_ECART = 50
    _PENALITE_DESEQUILIBRE = 20
    # Mutation adaptative : le taux décroît linéairement de ALGO_GA_MUTATION_RATE
    # (exploration en début d'évolution) vers _MUTATION_TAUX_MIN (exploitation en
    # fin d'évolution), et chaque mutation déclenchée applique plusieurs swaps
    # (proportionnels au nombre de candidats de la matière) plutôt qu'un seul —
    # un swap unique est une perturbation négligeable sur un grand chromosome.
    _MUTATION_TAUX_MIN = 0.02
    _MUTATION_INTENSITE = 0.05
    # Nombre de tentatives bornées pour chaque réparation locale (exclusion,
    # écart minimum, équilibre de charge) — cf. _reparer_complet.
    _REPARATION_MAX_ESSAIS = 5

    def _construire_matiere_data(self) -> dict[int, dict]:
        """Précalcule, par matière, les créneaux disponibles et les examinateurs
        valides par candidat (établissement à éviter / prof à éviter).

        Matiere définit __eq__ sans __hash__ (donc non hashable) : on indexe
        le dict par id() de l'objet plutôt que par la Matiere elle-même
        (même contrainte que dans algo_cp.py).
        """
        matiere_data: dict[int, dict] = {}
        for matiere in self.liste_matieres:
            slots = [
                (examinateur, creneau)
                for examinateur in matiere.examinateurs
                for creneau in range(len(examinateur.oraux))
                if not isinstance(examinateur.oraux[creneau], CreneauInterdit)
            ]
            candidats = matiere.candidats
            if len(candidats) > len(slots):
                raise AucuneSolutionGA(
                    f"matière '{matiere.nom}' : {len(candidats)} candidat(s) pour "
                    f"seulement {len(slots)} créneau(x) disponible(s) au total"
                )
            examinateurs_valides: dict[Candidat, set] = {}
            for candidat in candidats:
                valides = {
                    e for e in matiere.examinateurs
                    if not (e.etablissements != [''] and candidat.etablissement in e.etablissements)
                    and not (candidat.profs_a_eviter != [''] and e.nom in candidat.profs_a_eviter)
                }
                if not valides:
                    raise PasDeCreneauDisponible(candidat, len(matiere.examinateurs))
                examinateurs_valides[candidat] = valides
            matiere_data[id(matiere)] = {
                'matiere': matiere,
                'slots': slots,
                'candidats': candidats,
                'examinateurs_valides': examinateurs_valides,
            }
        return matiere_data

    def _individu_aleatoire(self, matiere_data: dict) -> _Individu:
        individu: _Individu = {}
        for mid, data in matiere_data.items():
            perm = list(range(len(data['slots'])))
            random.shuffle(perm)
            individu[mid] = perm
        return individu

    @staticmethod
    def _ox(p1: list[int], p2: list[int]) -> list[int]:
        """Order Crossover : préserve un segment de p1, complète avec l'ordre
        relatif de p2 — produit toujours une permutation valide."""
        n = len(p1)
        if n < 2:
            return list(p1)
        a, b = sorted(random.sample(range(n), 2))
        enfant: list = [None] * n
        enfant[a:b] = p1[a:b]
        utilises = set(enfant[a:b])
        valeurs_restantes = [g for g in p2 if g not in utilises]
        positions_libres = [i for i in range(n) if not (a <= i < b)]
        for pos, val in zip(positions_libres, valeurs_restantes):
            enfant[pos] = val
        return enfant

    def _croisement(self, parent1: _Individu, parent2: _Individu, matiere_data: dict) -> _Individu:
        return {mid: self._ox(parent1[mid], parent2[mid]) for mid in matiere_data}

    def _mutation(self, individu: _Individu, matiere_data: dict, taux: float) -> None:
        """Mutation adaptative : `taux` (déjà décroissant au fil des
        générations, cf. resoudre()) déclenche ou non une mutation pour
        chaque matière ; quand elle se déclenche, plusieurs swaps sont
        appliqués (proportionnels au nombre de candidats), pas un seul —
        un swap unique est une perturbation négligeable sur un grand
        chromosome et ralentit inutilement l'exploration.

        Au moins une des deux positions échangées est toujours prise parmi
        les gènes réellement affectés (le préfixe « utile » de la
        permutation) : muter deux positions inutilisées entre elles n'a
        aucun effet sur le fitness.
        """
        for mid, data in matiere_data.items():
            if random.random() >= taux:
                continue
            perm = individu[mid]
            n = len(perm)
            n_candidats = len(data['candidats'])
            if n < 2 or n_candidats == 0:
                continue
            n_swaps = max(1, round(n_candidats * self._MUTATION_INTENSITE))
            for _ in range(n_swaps):
                i = random.randrange(n_candidats)
                j = random.randrange(n)
                if j == i:
                    continue
                perm[i], perm[j] = perm[j], perm[i]

    def _reparer(self, individu: _Individu, matiere_data: dict, max_essais: int | None = None) -> None:
        """Tente de corriger les violations d'exclusion (établissement/prof à
        éviter) par échange avec un autre créneau compatible de la même
        matière — conserve la propriété de permutation (donc l'unicité des
        créneaux) puisqu'il s'agit d'un simple swap.

        Le partenaire d'échange est cherché sur toute la longueur de la
        permutation (créneaux affectés à un candidat ET créneaux encore
        inutilisés), pas seulement parmi les candidats déjà placés : un
        créneau inutilisé n'a personne à y reloger, donc aucune vérification
        réciproque n'est nécessaire pour lui — c'est souvent là que se trouve
        la place manquante (ex. un examinateur encore peu chargé).
        """
        max_essais = max_essais if max_essais is not None else self._REPARATION_MAX_ESSAIS
        for mid, data in matiere_data.items():
            candidats = data['candidats']
            slots = data['slots']
            valides = data['examinateurs_valides']
            perm = individu[mid]
            n = len(candidats)
            total = len(perm)
            for i in range(n):
                examinateur_i, _creneau_i = slots[perm[i]]
                if examinateur_i in valides[candidats[i]]:
                    continue
                for _ in range(max_essais):
                    j = random.randrange(total)
                    if j == i:
                        continue
                    examinateur_j, _creneau_j = slots[perm[j]]
                    if examinateur_j not in valides[candidats[i]]:
                        continue
                    if j < n and examinateur_i not in valides[candidats[j]]:
                        continue
                    perm[i], perm[j] = perm[j], perm[i]
                    break

    def _reparer_ecart(self, individu: _Individu, matiere_data: dict, max_essais: int | None = None) -> None:
        """Tente de corriger les écarts insuffisants entre les deux oraux
        d'un même candidat, en échangeant l'un des deux oraux avec celui
        d'un autre candidat de la même matière — accepté seulement si
        l'échange augmente effectivement l'écart et ne casse pas les
        exclusions établissement/prof à éviter d'aucun des deux candidats.

        Sans cette réparation, l'écart minimum (une des pénalités les plus
        lourdes du fitness, cf. _PENALITE_ECART) ne s'améliore qu'au hasard
        du croisement/de la mutation — beaucoup plus lent à converger.
        """
        max_essais = max_essais if max_essais is not None else self._REPARATION_MAX_ESSAIS

        # Position/créneau courant de chaque candidat dans chacune de ses
        # deux matières (choix1 et choix2).
        infos_candidat: dict[Candidat, list[tuple[int, int, int]]] = {}
        for mid, data in matiere_data.items():
            slots = data['slots']
            perm = individu[mid]
            for i, candidat in enumerate(data['candidats']):
                creneau = slots[perm[i]][1]
                infos_candidat.setdefault(candidat, []).append((mid, i, creneau))

        for candidat, infos in infos_candidat.items():
            if len(infos) != 2:
                continue
            (mid_a, i_a, creneau_a), (mid_b, i_b, creneau_b) = infos
            if abs(creneau_a - creneau_b) >= self.creneaux_minimum_entre_oraux:
                continue
            # Essaie de déplacer l'un des deux oraux (ordre aléatoire), en
            # s'arrêtant au premier qui améliore effectivement l'écart.
            candidats_essai = [(mid_a, i_a, creneau_b), (mid_b, i_b, creneau_a)]
            random.shuffle(candidats_essai)
            for mid, i, creneau_fixe in candidats_essai:
                data = matiere_data[mid]
                slots = data['slots']
                perm = individu[mid]
                valides = data['examinateurs_valides']
                candidats_matiere = data['candidats']
                n = len(candidats_matiere)
                total = len(perm)
                ecart_actuel = abs(slots[perm[i]][1] - creneau_fixe)
                examinateur_i, _ = slots[perm[i]]
                for _ in range(max_essais):
                    j = random.randrange(total)
                    if j == i:
                        continue
                    examinateur_j, creneau_j = slots[perm[j]]
                    if abs(creneau_j - creneau_fixe) <= ecart_actuel:
                        continue
                    if examinateur_j not in valides[candidat]:
                        continue
                    if j < n and examinateur_i not in valides[candidats_matiere[j]]:
                        continue
                    perm[i], perm[j] = perm[j], perm[i]
                    break
                else:
                    continue
                break

    def _reparer_desequilibre(self, individu: _Individu, matiere_data: dict, max_essais: int | None = None) -> None:
        """Tente de réduire l'écart de charge entre l'examinateur le plus
        chargé et le moins chargé d'une matière, en échangeant un candidat de
        l'examinateur le plus chargé avec un candidat d'un autre examinateur —
        accepté seulement si les exclusions établissement/prof à éviter des
        deux candidats restent respectées après l'échange.

        Une seule amélioration par matière et par appel : cette réparation
        est exécutée à chaque génération, un rééquilibrage progressif suffit.
        """
        max_essais = max_essais if max_essais is not None else self._REPARATION_MAX_ESSAIS
        for mid, data in matiere_data.items():
            examinateurs = data['matiere'].examinateurs
            if len(examinateurs) < 2:
                continue
            slots = data['slots']
            perm = individu[mid]
            candidats = data['candidats']
            valides = data['examinateurs_valides']
            n = len(candidats)
            if n == 0:
                continue

            # Initialisé à 0 pour TOUS les examinateurs de la matière — un
            # examinateur sans aucun candidat actuellement assigné (donc
            # absent de la boucle ci-dessous) doit être visible comme
            # candidat à un rééquilibrage, pas ignoré.
            charge: dict[int, int] = {id(e): 0 for e in examinateurs}
            for i in range(n):
                id_ex = id(slots[perm[i]][0])
                charge[id_ex] = charge.get(id_ex, 0) + 1
            id_max = max(charge, key=lambda k: charge[k])
            id_min = min(charge, key=lambda k: charge[k])
            if charge[id_max] - charge[id_min] <= 1:
                continue  # déjà aussi équilibré que possible

            total = len(perm)
            indices_max = [i for i in range(n) if id(slots[perm[i]][0]) == id_max]
            random.shuffle(indices_max)
            for i in indices_max:
                examinateur_i, _ = slots[perm[i]]
                candidat_i = candidats[i]
                for _ in range(max_essais):
                    j = random.randrange(total)
                    if j == i:
                        continue
                    examinateur_j, _ = slots[perm[j]]
                    if id(examinateur_j) == id_max:
                        continue
                    if examinateur_j not in valides[candidat_i]:
                        continue
                    if j < n and examinateur_i not in valides[candidats[j]]:
                        continue
                    perm[i], perm[j] = perm[j], perm[i]
                    break
                else:
                    continue
                break

    def _reparer_complet(self, individu: _Individu, matiere_data: dict) -> None:
        """Enchaîne les trois réparations locales (mémétique) : exclusion,
        écart minimum, équilibre de charge — dans cet ordre, du critère le
        plus impératif (règle métier absolue) au plus « confort »."""
        self._reparer(individu, matiere_data)
        self._reparer_ecart(individu, matiere_data)
        self._reparer_desequilibre(individu, matiere_data)

    def _evaluer(self, individu: _Individu, matiere_data: dict) -> _Evaluation:
        violations_exclusion = 0
        creneaux_par_examinateur: dict[int, set] = {}
        creneaux_candidat: dict[Candidat, list[int]] = {}

        for mid, data in matiere_data.items():
            slots = data['slots']
            candidats = data['candidats']
            valides = data['examinateurs_valides']
            perm = individu[mid]
            for i, candidat in enumerate(candidats):
                examinateur, creneau = slots[perm[i]]
                creneaux_par_examinateur.setdefault(id(examinateur), set()).add(creneau)
                if examinateur not in valides[candidat]:
                    violations_exclusion += 1
                creneaux_candidat.setdefault(candidat, []).append(creneau)

        violations_ecart = 0
        for creneaux in creneaux_candidat.values():
            if len(creneaux) == 2:
                deficit = self.creneaux_minimum_entre_oraux - abs(creneaux[0] - creneaux[1])
                if deficit > 0:
                    violations_ecart += deficit

        # Équité de charge entre examinateurs d'une même matière : pénalise
        # l'écart entre l'examinateur le plus chargé et le moins chargé.
        # Poids choisi pour dominer nettement les variations d'occupation
        # (0-100%) sans pour autant l'emporter sur une vraie violation
        # d'écart minimum (cf. _PENALITE_ECART) — l'équité est une
        # préférence de qualité forte, pas une règle absolue comme
        # l'exclusion établissement/prof à éviter.
        desequilibre_charge = 0
        for data in matiere_data.values():
            examinateurs = data['matiere'].examinateurs
            if len(examinateurs) < 2:
                continue
            charges = [len(creneaux_par_examinateur.get(id(e), ())) for e in examinateurs]
            desequilibre_charge += max(charges) - min(charges)

        # Occupation : même logique que AlgoOne.statistiques() (trous parmi
        # les créneaux réellement disponibles avant le dernier utilisé), mais
        # calculée directement sur le chromosome pour rester rapide — cette
        # fonction est appelée à chaque individu de chaque génération.
        total_occupes = 0
        total_trous = 0
        for examinateur in self.liste_examinateurs:
            occupes = creneaux_par_examinateur.get(id(examinateur))
            if not occupes:
                continue
            n_interdits = sum(1 for o in examinateur.oraux if isinstance(o, CreneauInterdit))
            dernier = max(occupes)
            total_occupes += len(occupes)
            total_trous += (dernier - n_interdits + 1) - len(occupes)

        occupation_pct = (
            total_occupes / (total_occupes + total_trous) * 100
            if (total_occupes + total_trous) > 0 else 0.0
        )
        fitness = (
            occupation_pct
            - self._PENALITE_EXCLUSION * violations_exclusion
            - self._PENALITE_ECART * violations_ecart
            - self._PENALITE_DESEQUILIBRE * desequilibre_charge
        )
        return _Evaluation(
            fitness, occupation_pct, violations_exclusion, violations_ecart, desequilibre_charge,
        )

    def _tournoi(self, evalues: list[tuple[_Evaluation, _Individu]]) -> _Individu:
        contestants = random.sample(evalues, min(self._TOURNAMENT_SIZE, len(evalues)))
        return max(contestants, key=lambda t: t[0].fitness)[1]

    def resoudre(self) -> None:
        """Résout l'appairage des oraux par algorithme génétique.

        Comme AlgoCP.resoudre() (cf. algo_cp.py), le résultat varie
        volontairement d'un run à l'autre : population initiale aléatoire,
        croisement/mutation stochastiques.
        """
        log.debug(f"Run {self.numero_run} : Démarrage de l'appairage (GA)")
        matiere_data = self._construire_matiere_data()

        debut = _time.time()
        population = [self._individu_aleatoire(matiere_data) for _ in range(ALGO_GA_POPULATION)]
        for individu in population:
            self._reparer_complet(individu, matiere_data)

        n_elite = max(1, ALGO_GA_POPULATION // 20)
        meilleur_individu: _Individu | None = None
        meilleure_eval: _Evaluation | None = None
        generations_sans_amelioration = 0
        derniere_log_info_t = 0.0
        generation = 0

        while generation < ALGO_GA_GENERATIONS and (_time.time() - debut) < ALGO_GA_TIMEOUT:
            evalues = sorted(
                ((self._evaluer(ind, matiere_data), ind) for ind in population),
                key=lambda t: t[0].fitness, reverse=True,
            )
            eval_gen, individu_gen = evalues[0]

            if meilleure_eval is None or eval_gen.fitness > meilleure_eval.fitness:
                meilleure_eval, meilleur_individu = eval_gen, individu_gen
                generations_sans_amelioration = 0
                t = _time.time() - debut
                message = (
                    f"Run {self.numero_run} : GA — génération {generation} : "
                    f"fitness={meilleure_eval.fitness:.1f} "
                    f"(occupation={meilleure_eval.occupation_pct:.1f}%, "
                    f"violations_exclusion={meilleure_eval.violations_exclusion}, "
                    f"violations_ecart={meilleure_eval.violations_ecart}), t={t:.1f}s"
                )
                log.debug(message)
                if generation == 0 or t - derniere_log_info_t >= self._LOG_INTERVAL_S:
                    log.info(message)
                    derniere_log_info_t = t
            else:
                generations_sans_amelioration += 1

            if generations_sans_amelioration >= self._STAGNATION_LIMIT:
                log.debug(
                    f"Run {self.numero_run} : GA — arrêt anticipé "
                    f"(stagnation depuis {self._STAGNATION_LIMIT} générations)"
                )
                break

            # Mutation adaptative : décroît linéairement de ALGO_GA_MUTATION_RATE
            # à _MUTATION_TAUX_MIN, selon la progression la plus avancée entre
            # générations écoulées et temps écoulé (les deux peuvent terminer
            # l'évolution, cf. condition de la boucle ci-dessus).
            progression = max(
                generation / ALGO_GA_GENERATIONS,
                (_time.time() - debut) / ALGO_GA_TIMEOUT,
            )
            progression = min(1.0, progression)
            taux_mutation = ALGO_GA_MUTATION_RATE - (
                (ALGO_GA_MUTATION_RATE - self._MUTATION_TAUX_MIN) * progression
            )

            nouvelle_population = [ind for _, ind in evalues[:n_elite]]
            while len(nouvelle_population) < ALGO_GA_POPULATION:
                parent1 = self._tournoi(evalues)
                parent2 = self._tournoi(evalues)
                enfant = self._croisement(parent1, parent2, matiere_data)
                self._mutation(enfant, matiere_data, taux_mutation)
                self._reparer_complet(enfant, matiere_data)
                nouvelle_population.append(enfant)
            population = nouvelle_population
            generation += 1

        assert meilleure_eval is not None and meilleur_individu is not None
        log.info(
            f"Run {self.numero_run} : GA — terminé après {generation} génération(s) "
            f"(fitness={meilleure_eval.fitness:.1f}, "
            f"occupation={meilleure_eval.occupation_pct:.1f}%, "
            f"violations_exclusion={meilleure_eval.violations_exclusion})"
        )

        # Les exclusions établissement/prof à éviter sont une règle métier
        # absolue (conflit d'intérêt), pas une simple préférence de qualité —
        # contrairement à l'écart minimum candidat (déjà toléré en best-effort
        # côté Monte-Carlo, cf. aucun_run_conforme), on ne publie jamais un
        # planning qui les enfreint encore.
        if meilleure_eval.violations_exclusion > 0:
            raise AucuneSolutionGA(
                f"{meilleure_eval.violations_exclusion} violation(s) résiduelle(s) des "
                f"exclusions établissement/prof à éviter après {generation} génération(s) "
                f"— augmenter ALGO_GA_GENERATIONS, ALGO_GA_TIMEOUT ou ALGO_GA_POPULATION."
            )

        for mid, data in matiere_data.items():
            perm = meilleur_individu[mid]
            for i, candidat in enumerate(data['candidats']):
                examinateur, creneau = data['slots'][perm[i]]
                self.creer_oral(candidat, examinateur, data['matiere'], creneau)

        log.debug(f"Run {self.numero_run} : {len(self.liste_oraux)} oraux créés (GA).")
        log.debug(f"Run {self.numero_run} : Fin de l'appairage (GA)")
        assert len(self.liste_oraux) == 2 * len(self.liste_candidats)


def algo_ga_run(parameters):
    """
    Exécute l'algorithme génétique d'assignation des oraux.

    Miroir de algo.algo_run() / algo_cp.algo_cp_run() : même signature
    d'entrée/sortie, pour rester compatible avec algo.selectionner_meilleur_algo().

    :param parameters: Paramètres de configuration pour l'algorithme
    """
    numero_run = parameters.get('numero_run', 0)
    log.info(f"Run {numero_run} : lancement (GA)")
    alg = AlgoGA(**parameters)
    alg.setup_from_files()
    try:
        alg.resoudre()
    except AlgoError as e:
        log.info(f"Run {numero_run} : fin (échec — {e})")
        return None, str(e)
    alg.calcul_horaires()
    alg.verif_ecart_horaire()
    stats = alg.statistiques()
    log.info(f"Run {numero_run} : fin ({stats})")
    return alg, stats
