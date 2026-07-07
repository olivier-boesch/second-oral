#!/usr/bin/python3
"""
Second algorithme de placement des oraux : résolution par contraintes (CP-SAT).

Alternative à AlgoOne.resoudre() (glouton randomisé à redémarrages
Monte-Carlo) : modélise l'appairage candidat/examinateur/créneau comme un
problème de satisfaction/optimisation de contraintes résolu par OR-Tools
CP-SAT, en une seule résolution plutôt qu'un grand nombre de tirages
aléatoires. L'écart minimum entre les deux oraux d'un candidat devient une
contrainte garantie (et non un critère de sélection a posteriori).
"""
import random
from os import cpu_count

from ortools.sat.python import cp_model

from algo import (
    AlgoOne,
    AlgoError,
    Candidat,
    CreneauInterdit,
    Examinateur,
    Matiere,
    PasDeCreneauDisponible,
    _env_bool,
    _env_int,
    log,
)

ALGO_CP_TIMEOUT = _env_int("ALGO_CP_TIMEOUT", 60)
# Mode optimal : AUCUNE limite de temps, le solveur tourne jusqu'à preuve
# d'optimalité — peut prendre des heures sur un jeu de données réel.
# Désactivé par défaut ; ALGO_CP_TIMEOUT est ignoré quand actif.
ALGO_CP_OPTIMAL = _env_bool("ALGO_CP_OPTIMAL", False)


class _ProgressLogger(cp_model.CpSolverSolutionCallback):
    """Journalise les solutions améliorantes trouvées pendant la résolution.

    CP-SAT ne renvoie sa solution qu'à la toute fin de Solve() ; sans ce
    callback, rien n'apparaît dans les logs pendant tout le temps de
    résolution (jusqu'à ALGO_CP_TIMEOUT secondes), ce qui donne l'impression
    d'un blocage.

    Deux niveaux de verbosité :
    - DEBUG (ALGO_DEBUG=1) : chaque solution améliorante, sans limite —
      utile pour diagnostiquer la convergence du solveur.
    - INFO (par défaut) : la première solution, puis au maximum une ligne
      toutes les _LOG_INTERVAL_S secondes — un problème avec beaucoup de
      candidats peut trouver des dizaines de solutions par seconde en début
      de recherche, ce qui noierait le log par défaut sans ce throttling.
    """

    _LOG_INTERVAL_S = 2.0

    def __init__(self, numero_run: int):
        super().__init__()
        self._numero_run = numero_run
        self._n_solutions = 0
        self._last_info_log_t = 0.0

    def on_solution_callback(self) -> None:
        self._n_solutions += 1
        t = self.WallTime()
        message = (
            f"Run {self._numero_run} : CP-SAT — solution n°{self._n_solutions} "
            f"trouvée (objectif={self.ObjectiveValue():.0f}, "
            f"borne={self.BestObjectiveBound():.0f}, t={t:.1f}s)"
        )
        log.debug(message)
        if self._n_solutions == 1 or t - self._last_info_log_t >= self._LOG_INTERVAL_S:
            log.info(message)
            self._last_info_log_t = t


class AucuneSolutionCP(AlgoError):
    """Le solveur CP-SAT n'a trouvé aucune solution respectant les contraintes."""

    def __init__(self, status_name: str, n_candidats: int, n_examinateurs: int, n_creneaux: int):
        self.status_name = status_name
        super().__init__(
            f"CP-SAT : aucune solution trouvée (statut={status_name}) — "
            f"{n_candidats} candidat(s), {n_examinateurs} examinateur(s), "
            f"{n_creneaux} créneau(x) par examinateur"
        )


class AlgoCP(AlgoOne):
    """Variante de AlgoOne qui résout l'appairage via CP-SAT (Google OR-Tools).

    Réutilise tel quel setup_from_files / calcul_horaires / statistiques /
    save de AlgoOne — seule resoudre() change de logique.
    """

    def _creneaux_autorises(self, examinateur: Examinateur, candidat: Candidat) -> list[int]:
        """Créneaux où `examinateur` peut recevoir `candidat`.

        Reprend exactement les règles de Examinateur.recherche_creneau :
        établissement à éviter, prof à éviter, créneaux interdits (début de
        journée décalé pour cet examinateur).
        """
        if examinateur.etablissements != [''] and candidat.etablissement in examinateur.etablissements:
            return []
        if candidat.profs_a_eviter != [''] and examinateur.nom in candidat.profs_a_eviter:
            return []
        return [
            t for t, oral in enumerate(examinateur.oraux)
            if not isinstance(oral, CreneauInterdit)
        ]

    def resoudre(self) -> None:
        """Résout l'appairage des oraux via un modèle CP-SAT.

        Contrairement à un CP-SAT « déterministe » classique, ce modèle est
        délibérément randomisé à chaque appel (ordre de parcours candidats/
        examinateurs, graine du solveur, bruit de désambiguïsation dans
        l'objectif — cf. plus bas) : par choix, on veut un résultat différent
        à chaque exécution plutôt qu'un unique optimum reproductible, tout en
        restant proche de l'optimal (comme le faisait déjà l'esprit du
        glouton Monte-Carlo, mais sans les 1000 tirages).
        """
        log.debug(f"Run {self.numero_run} : Démarrage de l'appairage (CP-SAT)")
        model = cp_model.CpModel()

        # Ordre de parcours mélangé (candidats + examinateurs par matière) :
        # contribue à la diversité des solutions d'un run à l'autre.
        candidats_ordre = list(self.liste_candidats)
        random.shuffle(candidats_ordre)
        # Matiere définit __eq__ sans __hash__ (donc non hashable) : on
        # indexe par id() de l'objet plutôt que par la Matiere elle-même.
        examinateurs_par_matiere: dict[int, list[Examinateur]] = {}
        for matiere in self.liste_matieres:
            examinateurs = list(matiere.examinateurs)
            random.shuffle(examinateurs)
            examinateurs_par_matiere[id(matiere)] = examinateurs

        # x[(candidat, choix_attr, examinateur, creneau)] -> BoolVar
        x: dict[tuple, cp_model.IntVar] = {}
        for candidat in candidats_ordre:
            for choix_attr in ('choix1', 'choix2'):
                matiere: Matiere = getattr(candidat, choix_attr)
                choix_vars = []
                for examinateur in examinateurs_par_matiere[id(matiere)]:
                    for creneau in self._creneaux_autorises(examinateur, candidat):
                        var = model.NewBoolVar(
                            f"x_{candidat.numero}_{choix_attr}_{examinateur.nom}_{creneau}"
                        )
                        x[(candidat, choix_attr, examinateur, creneau)] = var
                        choix_vars.append(var)
                if not choix_vars:
                    raise PasDeCreneauDisponible(candidat, len(matiere.examinateurs))
                model.AddExactlyOne(choix_vars)

        # un examinateur ne peut recevoir qu'un seul candidat par créneau
        par_examinateur_creneau: dict[tuple, list] = {}
        vars_par_examinateur: dict[Examinateur, list] = {}
        for (_candidat, _choix_attr, examinateur, creneau), var in x.items():
            par_examinateur_creneau.setdefault((examinateur, creneau), []).append(var)
            vars_par_examinateur.setdefault(examinateur, []).append(var)
        for vars_ in par_examinateur_creneau.values():
            model.AddAtMostOne(vars_)

        # écart minimum garanti entre les deux oraux d'un même candidat
        max_creneau = self.max_creneaux_journee - 1
        for candidat in self.liste_candidats:
            t1 = model.NewIntVar(0, max_creneau, f"t1_{candidat.numero}")
            t2 = model.NewIntVar(0, max_creneau, f"t2_{candidat.numero}")
            model.Add(t1 == sum(
                creneau * var
                for (c, choix_attr, _e, creneau), var in x.items()
                if c is candidat and choix_attr == 'choix1'
            ))
            model.Add(t2 == sum(
                creneau * var
                for (c, choix_attr, _e, creneau), var in x.items()
                if c is candidat and choix_attr == 'choix2'
            ))
            diff = model.NewIntVar(-max_creneau, max_creneau, f"diff_{candidat.numero}")
            model.Add(diff == t1 - t2)
            abs_diff = model.NewIntVar(0, max_creneau, f"absdiff_{candidat.numero}")
            model.AddAbsEquality(abs_diff, diff)
            model.Add(abs_diff >= self.creneaux_minimum_entre_oraux)

        # Équité de charge entre examinateurs d'une même matière : pour chaque
        # matière ayant plusieurs examinateurs, on pénalise l'écart entre
        # l'examinateur le plus chargé et le moins chargé (nombre d'oraux
        # reçus). Poids délibérément énorme par rapport à la contribution
        # maximale possible du terme de tassement ci-dessous (POIDS_EQUITE >>
        # max_creneau * BRUIT_ECHELLE * nombre de variables) : le solveur
        # sacrifie toujours un meilleur tassement pour une meilleure
        # répartition de charge, jamais l'inverse.
        POIDS_EQUITE = 1_000_000
        ecarts_charge = []
        for matiere in self.liste_matieres:
            examinateurs = examinateurs_par_matiere[id(matiere)]
            charges = [
                vars_par_examinateur[e] for e in examinateurs if e in vars_par_examinateur
            ]
            if len(charges) < 2:
                continue
            n_candidats_matiere = len(matiere.candidats)
            charge_vars = []
            for i, vars_examinateur in enumerate(charges):
                charge = model.NewIntVar(0, n_candidats_matiere, f"charge_{matiere.nom}_{i}")
                model.Add(charge == sum(vars_examinateur))
                charge_vars.append(charge)
            charge_max = model.NewIntVar(0, n_candidats_matiere, f"chargemax_{matiere.nom}")
            charge_min = model.NewIntVar(0, n_candidats_matiere, f"chargemin_{matiere.nom}")
            model.AddMaxEquality(charge_max, charge_vars)
            model.AddMinEquality(charge_min, charge_vars)
            ecarts_charge.append(charge_max - charge_min)

        # Objectif : tasser les oraux tôt dans la journée (réduit les trous
        # avant le dernier créneau utilisé par examinateur -> meilleur taux
        # d'occupation, cf. AlgoOne.statistiques()), MAIS avec un bruit
        # aléatoire de désambiguïsation par variable (0..BRUIT_ECHELLE-1,
        # toujours strictement inférieur au poids d'un seul créneau) : sans
        # lui, le solveur choisirait systématiquement la même solution parmi
        # toutes celles à égalité de score (fréquent ici — de nombreux
        # examinateurs d'une même matière sont interchangeables). Le terme
        # `creneau * BRUIT_ECHELLE` reste dominant sur le tassement, donc la
        # solution reste proche de l'optimum du tassement ; seul le choix
        # entre solutions quasi équivalentes change à chaque run.
        BRUIT_ECHELLE = 25
        objectif_tassement = sum(
            (creneau * BRUIT_ECHELLE + random.randint(0, BRUIT_ECHELLE - 1)) * var
            for (_c, _m, _e, creneau), var in x.items()
        )
        model.Minimize(POIDS_EQUITE * sum(ecarts_charge) + objectif_tassement)

        solver = cp_model.CpSolver()
        if ALGO_CP_OPTIMAL:
            log.warning(
                f"Run {self.numero_run} : CP-SAT — mode OPTIMAL activé (ALGO_CP_OPTIMAL) : "
                "AUCUNE limite de temps, le solveur tourne jusqu'à preuve d'optimalité. "
                "Cela peut prendre plusieurs heures, voire ne jamais aboutir, sur un jeu "
                "de données réel — utilisez /gestion/algo/stop pour interrompre si besoin."
            )
        else:
            solver.parameters.max_time_in_seconds = float(ALGO_CP_TIMEOUT)
        solver.parameters.num_search_workers = max(1, cpu_count() or 1)
        solver.parameters.random_seed = random.randint(1, 2 ** 31 - 1)
        # NB : on n'active volontairement PAS solver.parameters.log_search_progress
        # — ce journal natif du solveur (présolve, heuristiques internes...) est
        # écrit directement sur stdout par la couche C++ d'OR-Tools, sans passer
        # par le logger Python (donc sans respecter ALGO_DEBUG) : plusieurs
        # centaines de lignes très techniques et non traduites même sur un
        # petit jeu de données. _ProgressLogger ci-dessus fournit déjà un suivi
        # de progression exploitable (solution, objectif, borne, temps).
        status = solver.Solve(model, _ProgressLogger(self.numero_run))

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise AucuneSolutionCP(
                solver.StatusName(status),
                len(self.liste_candidats), len(self.liste_examinateurs),
                self.max_creneaux_journee,
            )

        statut_libelle = {
            cp_model.OPTIMAL: "OPTIMAL — solution prouvée optimale",
            cp_model.FEASIBLE: (
                "FEASIBLE — résolution interrompue avant preuve d'optimalité ; "
                "meilleure solution trouvée conservée"
                if ALGO_CP_OPTIMAL else
                f"FEASIBLE — délai de {ALGO_CP_TIMEOUT}s (ALGO_CP_TIMEOUT) atteint "
                f"avant preuve d'optimalité ; meilleure solution trouvée conservée"
            ),
        }.get(status, solver.StatusName(status))
        log.info(f"Run {self.numero_run} : CP-SAT — statut final : {statut_libelle}")

        for (candidat, choix_attr, examinateur, creneau), var in x.items():
            if solver.Value(var):
                matiere = getattr(candidat, choix_attr)
                self.creer_oral(candidat, examinateur, matiere, creneau)

        log.debug(f"Run {self.numero_run} : {len(self.liste_oraux)} oraux créés (CP-SAT).")
        log.debug(f"Run {self.numero_run} : Fin de l'appairage (CP-SAT)")
        assert len(self.liste_oraux) == 2 * len(self.liste_candidats)


def algo_cp_run(parameters):
    """
    Exécute l'algorithme CP-SAT d'assignation des oraux.

    Miroir de algo.algo_run() : même signature d'entrée/sortie, pour rester
    compatible avec algo.selectionner_meilleur_algo().

    :param parameters: Paramètres de configuration pour l'algorithme
    """
    numero_run = parameters.get('numero_run', 0)
    log.info(f"Run {numero_run} : lancement (CP-SAT)")
    alg = AlgoCP(**parameters)
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
