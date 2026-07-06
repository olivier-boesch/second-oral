# CHANGELOG

## [Unreleased]

### Changed

- `cp_timeout` (délai max du solveur CP-SAT, `/gestion/algo`) : plafond relevé de 600s à 1200s (20 min), backend et champ du formulaire.

### Added

**Gestion en cours de journée (suite 4) — déclaration de tiers-temps d'un candidat**
- Nouveau bouton **⏱️ Déclarer** sur `/gestion/liste-candidats` : un candidat déclare un tiers-temps le jour J, après le placement initial
- Étend la préparation de ses deux oraux d'1/3 (même règle que `AlgoOne.calcul_horaires` pour un tiers-temps connu à la construction du planning — heure de sujet inchangée, heure d'oral/fin décalées) ; nouvelle fonction `rebalance.planifier_tiers_temps()`
- Cascade automatique : tous les oraux suivants chez les deux mêmes examinateurs ce jour-là sont décalés du même délai, pour ne jamais les chevaucher
- Écart minimum et pause méridienne revérifiés pour chaque oral cascadé (signalés, jamais bloquants) ; un chevauchement entre les deux oraux du candidat lui-même (écart minimum déjà très faible) bloque la proposition et demande une résolution manuelle
- Nouvelles requêtes `SELECT_CANDIDAT_TIERS_TEMPS`, `SELECT_ORAUX_CANDIDAT_TIERS_TEMPS`, `UPDATE_CANDIDAT_TIERS_TEMPS` dans `db_facility_web.py` ; `SELECT_ORAUX_EXAMINATEUR` complétée avec `id_candidat` (nécessaire à la cascade)
- Portée volontairement limitée à un seul candidat à la fois ; ne gère pas le retrait d'un tiers-temps déjà déclaré
- **Unification avec `/gestion/edit-candidat`** : cette fiche avait déjà une case « Tiers temps », qui se contentait de poser le flag en base sans jamais adapter les horaires — un risque réel de désynchronisation puisque les fiches candidats n'existent qu'après le placement initial (donc toujours en contexte « jour J »). Cocher cette case déclenche désormais la même adaptation (extension + cascade), via les fonctions partagées `_calculer_plan_tiers_temps()`/`_appliquer_oraux_tiers_temps()` ; confirmation JS avant activation, et blocage (aucune mise à jour, ni du flag ni des oraux) en cas de conflit bloquant

**Tests (suite 15)**
- `tests/unit/test_rebalance.py::TestPlanifierTiersTemps` : extension de préparation, cascade avec préservation de l'écart existant, oraux avant le candidat ignorés, écart minimum rompu signalé sans bloquer, chevauchement de la pause méridienne signalé, conflit bloquant si les deux oraux du candidat se chevauchent
- `tests/integration/test_declarer_tiers_temps_candidat.py` : câblage de la route (formulaire, déjà tiers-temps, prévisualisation, confirmation avec mise à jour des 3 oraux affectés et double notification candidat/cascadé)
- `tests/integration/test_edit_candidat.py` : édition simple sans tiers-temps, activation avec cascade, activation sans oral publié (flag seul), désactivation sans cascade, conflit bloquant sans aucune mise à jour

**`examinateurs.csv` — minutes dans `Heure mini`**
- La colonne `Heure mini` accepte désormais une heure:minute (`9:30`), en plus du format heure entière historique (`9`) — nouvelle fonction `algo.parser_heure_mini()`
- Validation mise à jour dans `csv_validator.py` (heure 0-23, minutes 0-59 si présentes) ; la validation stricte de la case ODS (`vHeure`, intervalle numérique 0-23) a été retirée car elle aurait bloqué la saisie de minutes dans le tableur — la validation faisant foi reste celle de `csv_validator.py` à l'upload
- Aucun changement de comportement pour les fichiers existants (heure entière toujours acceptée)

**Tests (suite 13)**
- `tests/unit/test_algo.py::TestParserHeureMini` : heure entière, heure:minute, zéro initial, espaces, effet réel sur le nombre de créneaux interdits (précision à la minute, pas seulement à l'heure)
- `tests/unit/test_csv_validator.py` : heure:minute valide, minutes hors bornes, heure hors bornes avec minutes

**Algorithme de placement — mode optimal CP-SAT**
- Nouvelle option `cp_optimal` (`/gestion/algo`) ou `ALGO_CP_OPTIMAL` : supprime toute limite de temps au solveur CP-SAT, qui tourne alors jusqu'à preuve mathématique d'optimalité plutôt que de s'arrêter à `cp_timeout`
- ⚠️ Avertissement explicite dans l'UI (texte rouge + confirmation JS à l'activation) et dans `docs/algo.md` : peut prendre plusieurs heures, voire ne jamais aboutir, sur un jeu de données réel — un arrêt manuel (`/gestion/algo/stop`) ne publie aucune solution (contrairement à l'expiration normale de `cp_timeout`, qui conserve la meilleure solution trouvée)
- Désactivé par défaut ; ignoré si le moteur Monte-Carlo est sélectionné

**Tests (suite 10)**
- `tests/unit/test_algo_cp.py::TestAlgoCPModeOptimal` : désactivé par défaut, délai appliqué normalement quand désactivé, aucune limite (`max_time_in_seconds` reste à `inf`) quand activé
- `tests/integration/test_flask_routes.py` : sauvegarde/valeur par défaut de `cp_optimal` via `/gestion/algo/params`

**UX — hub "Jour J"**
- Nouvelle page `/gestion/jour-j` (icône ⚡ en tête de la barre latérale admin) : centralise le pilotage en direct pendant les épreuves — état ambiant (algorithme en cours, statut de la pause méridienne : à venir/en cours/terminée) et accès rapide (liste déroulante) aux formulaires de disponibilité examinateur et de changement de matière candidat, sans repasser par les listes complètes
- Objectif : réduire le nombre de clics/pages pour réagir à un imprévu (retard d'examinateur, changement de matière) en cours de journée

**Tests (suite 11)**
- `tests/integration/test_flask_routes.py::TestJourJ` : contenu de la page (listes déroulantes, lien retour), statut algorithme (en cours/au repos), statut pause méridienne (non configurée/à venir/en cours), redirection si non authentifié

**Gestion en cours de journée (suite 3) — suggestion de renfort inédit**
- Après l'ajout d'un nouvel examinateur avec une matière (`/gestion/add-examinateur`), un bandeau apparaît sur `/gestion/credentials` proposant de rééquilibrer dès maintenant vers lui les oraux déjà en cours pour cette matière
- Le lien mène au formulaire existant de disponibilité (`/gestion/examinateur/disponibilite?renfort=1`), avec le champ « Disponible de nouveau à partir de » pré-rempli sur l'heure courante (arrondie aux 5 minutes suivantes) — modifiable avant de prévisualiser
- Aucune nouvelle logique de rééquilibrage : réutilise entièrement `rebalance.planifier_renfort()` (déjà non bloquant, déjà conscient de la pause méridienne et de l'équité de charge) — seule la « colle » UI est ajoutée
- `DbInterface.make_sql_update()` / `db_update()` retournent désormais `cursor.lastrowid`, nécessaire pour connaître l'id du nouvel examinateur juste après l'INSERT

**Tests (suite 9)**
- `tests/integration/test_add_examinateur_renfort_inedit.py` : suggestion présente/absente selon qu'une matière a été renseignée, bandeau affiché uniquement pour un id d'examinateur réellement existant, pré-remplissage de l'heure uniquement avec `?renfort=1`

**Algorithme de placement — pause méridienne**
- Nouveaux paramètres réglables depuis `/gestion/algo` (`pause_meridienne_debut`, `pause_meridienne_duree`) ou via `ALGO_PAUSE_MERIDIENNE_DEBUT`/`ALGO_PAUSE_MERIDIENNE_DUREE` : aucun oral ne se déroule plus pour un examinateur pendant la pause configurée
- Appliqué dans `AlgoOne.calcul_horaires()` (conversion créneau → horaire réel, après résolution) : un oral qui empièterait sur la pause est repoussé pour démarrer juste après sa fin, une seule fois par examinateur — les créneaux suivants s'enchaînent ensuite normalement
- Aucune modification nécessaire dans `algo_cp.py` : la logique étant dans la classe de base `AlgoOne`, les deux moteurs (Monte-Carlo et CP-SAT) en bénéficient automatiquement
- Désactivée par défaut (heure de début vide) pour ne changer aucun comportement existant
- Également respectée par la replanification en cours de journée (`webserver/rebalance.py` — absences/renforts d'examinateur, changement de matière d'un candidat) : `_placer` (glouton), `resoudre_oraux_difficiles` (CP-SAT, paliers 2/3), `construire_grille_etendue` (extension d'horaire) et `proposer_compaction` ne proposent jamais un créneau qui ferait travailler un examinateur pendant la pause — lue à chaque calcul de plan (`app.py::_pause_meridienne_params()`), donc toujours à jour sans relancer l'algorithme

**Tests (suite 8)**
- `tests/unit/test_algo.py::TestPauseMeridienne` : désactivée par défaut, aucun oral ne chevauche la pause, oral repoussé juste après la fin de la pause, un seul rattrapage par examinateur (les créneaux suivants restent régulièrement espacés)
- `tests/unit/test_rebalance.py::TestPauseMeridienneRebalance` (+ un cas dans `TestProposerCompaction`) : glouton, CP-SAT et extension de grille évitent tous la pause configurée
- `tests/integration/test_disponibilite_examinateur.py::TestDisponibiliteExaminateurPauseMeridienne` : la pause lue depuis `/gestion/algo` est bien transmise jusqu'à la résolution poussée

**Algorithme de placement**
- Second moteur de résolution, sélectionnable depuis `/gestion/algo` (paramètre `engine`) ou via `ALGO_ENGINE` : CP-SAT (Google OR-Tools, `algo_cp.py`, `AlgoCP`), en alternative au glouton Monte-Carlo historique (`AlgoOne`)
- CP-SAT modélise l'appairage candidat/examinateur/créneau comme un problème de contraintes et le résout en une seule fois (au lieu de 1000 tirages aléatoires) ; l'écart minimum entre les deux oraux d'un candidat devient une contrainte garantie plutôt qu'un critère de sélection a posteriori (plus de run "non conforme")
- Le placement produit reste volontairement différent à chaque lancement (ordre de parcours mélangé, graine de solveur, bruit de désambiguïsation dans l'objectif) tout en restant proche de l'optimal de tassement des créneaux
- Nouveau paramètre `cp_timeout` (`ALGO_CP_TIMEOUT`, défaut 60s) : délai maximum accordé au solveur CP-SAT
- Nouvelle dépendance `ortools`

**Tests**
- `tests/unit/test_algo_cp.py` : placement, écart minimum garanti, exclusions établissement/prof à éviter, créneaux interdits, cas infaisables (`AucuneSolutionCP`), variabilité entre runs

**Algorithme de placement (suite)**
- Troisième moteur de résolution, sélectionnable depuis `/gestion/algo` (paramètre `engine`) ou via `ALGO_ENGINE` : algorithme génétique (`algo_ga.py`, `AlgoGA`), en complément des moteurs Monte-Carlo et CP-SAT
- Encodage par permutation (un chromosome par matière), sélection par tournoi, croisement OX, mutation, élitisme et réparation locale des violations d'exclusion établissement/prof à éviter
- Les exclusions établissement/prof à éviter sont vérifiées strictement en fin d'évolution (règle métier absolue) : l'algorithme échoue explicitement (`AucuneSolutionGA`) plutôt que de publier un planning qui les enfreindrait ; l'écart minimum candidat reste best-effort (pénalité de fitness), comme pour le Monte-Carlo
- Nouveaux paramètres `ga_population`, `ga_generations`, `ga_timeout`, `ga_mutation_rate` (`ALGO_GA_POPULATION`, `ALGO_GA_GENERATIONS`, `ALGO_GA_TIMEOUT`, `ALGO_GA_MUTATION_RATE`)
- Aucune nouvelle dépendance (implémentation sans bibliothèque tierce)

**Tests (suite)**
- `tests/unit/test_algo_ga.py` : placement, exclusions établissement/prof à éviter, cas infaisables (`PasDeCreneauDisponible`, `AucuneSolutionGA`), variabilité entre runs

**Algorithme de placement (suite 2) — petites matières en fin de journée**
- Les matières peu demandées (peu de candidats par rapport à la capacité de leurs examinateurs) voient désormais leurs premiers créneaux réservés (`CreneauInterdit`), les repoussant vers la fin de journée
- Implémenté une seule fois dans `AlgoOne._reserver_petites_matieres()` (appelée depuis `setup_from_files()`, partagée par héritage) : profite automatiquement aux trois moteurs (Monte-Carlo, CP-SAT, génétique) sans duplication
- Nouveaux paramètres constructeur `optimiser_petites_matieres` (opt-in, `False` par défaut au niveau de l'API pour ne pas affecter les appelants existants), `seuil_petite_matiere`, `marge_flexibilite_petite_matiere` — activés par défaut en production via `__main__` (`ALGO_PETITES_MATIERES_FIN_JOURNEE`, `ALGO_SEUIL_PETITE_MATIERE`, `ALGO_MARGE_PETITE_MATIERE`)

**Tests (suite 2)**
- `tests/unit/test_algo.py::TestPetitesMatieresFinJournee` : désactivé par défaut, réservation correcte des créneaux d'une petite matière, non-impact sur les grosses matières, placement effectif en fin de journée

**Gestion en cours de journée — absence / retard / renfort d'un examinateur**
- Nouveau bouton **🕒 Disponibilité** sur `/gestion/liste-examinateurs` : rééquilibre les oraux restants d'une matière suite à un changement de disponibilité d'un examinateur (absence, retard, renfort), à partir de deux heures réglables ("indisponible à partir de" / "disponible de nouveau à partir de")
- Un retard se modélise comme une absence sur la fenêtre avant l'arrivée suivie d'un renfort sur la fenêtre après l'arrivée — même mécanisme, pas de troisième cas particulier (`webserver/rebalance.py`)
- Priorité systématique au même horaire (seul l'examinateur change, aucune disruption pour le candidat) avant tout recalcul d'heure ; écran de prévisualisation (vert = même heure, jaune = heure modifiée) avant application
- Les exclusions établissement/prof à éviter et l'écart minimum candidat (contre l'heure fixe de son autre oral) sont respectés lors du recalcul ; les oraux non replaçables automatiquement sont signalés pour une édition manuelle
- Réutilise l'infrastructure SSE existante (`edit_oral`) : chaque changement appliqué déclenche exactement la même notification ciblée candidat/salle/loge qu'une édition manuelle — extraction d'une fonction partagée `_appliquer_changement_oral()`
- Nouvelle requête `SELECT_ORAUX_MATIERE_DU_JOUR` / `SELECT_EXAMINATEUR_MATIERE` dans `db_facility_web.py` ; aucune nouvelle dépendance

**Tests (suite 3)**
- `tests/unit/test_rebalance.py` : placement au même horaire en priorité, repli sur un autre horaire avec écart minimum respecté, exclusions établissement/prof à éviter, rééquilibrage de charge vers un renfort
- `tests/integration/test_disponibilite_examinateur.py` : câblage de la route (formulaire, prévisualisation, confirmation + notification SSE)

**Algorithme de placement (suite 3) — équité entre examinateurs**
- Les trois moteurs de placement (Monte-Carlo, CP-SAT, génétique) répartissent désormais la charge le plus équitablement possible entre les examinateurs d'une même matière (écart maximum d'1 oral), et non plus seulement au mieux du tassement
- Monte-Carlo (`AlgoOne.recherche_creneau`) : priorité à l'examinateur le moins chargé, la proximité du créneau au matin ne servant plus qu'à départager une égalité
- CP-SAT (`algo_cp.py`) : terme d'objectif pénalisant l'écart de charge par matière, avec un poids dominant très largement le terme de tassement existant
- Génétique (`algo_ga.py`) : pénalité de déséquilibre de charge dans le fitness (`_PENALITE_DESEQUILIBRE`), dominant les variations d'occupation sans l'emporter sur une vraie violation d'écart minimum

**Tests (suite 4)**
- Nouveaux tests d'équité de charge dans `test_algo.py`, `test_algo_cp.py`, `test_algo_ga.py` (écart maximum d'1 oral entre examinateurs d'une même matière, y compris avec un nombre de candidats non divisible par le nombre d'examinateurs)

**Gestion en cours de journée (suite) — résolution poussée pour les oraux non replaçables**
- Sur l'écran de prévisualisation de `/gestion/examinateur/disponibilite`, deux nouveaux boutons apparaissent quand des oraux n'ont pas pu être replacés automatiquement (glouton) : **🔧 Résolution poussée (mêmes horaires)** et **🔧🕐 Résolution poussée + extension d'horaire**
- Le glouton existant (`planifier_absence`) est un premier essai non exhaustif ; `rebalance.resoudre_oraux_difficiles()` relance une résolution *exacte* par CP-SAT (Google OR-Tools) sur les seuls oraux restants — exploration exhaustive de toutes les combinaisons (examinateur × horaire), donc capable de réussir là où le glouton échoue
- Si la même grille horaire reste infaisable, un second palier (`rebalance.construire_grille_etendue()`) génère de nouveaux créneaux après le dernier horaire utilisé aujourd'hui pour la matière (pas égal à la durée d'un créneau déduite de la grille existante, jusqu'à 2h de plus par défaut), puis relance le solveur — les changements obtenus sur ces nouveaux horaires sont marqués `hors_grille` et signalés distinctement (🟧🕐) dans la prévisualisation
- Toujours aucune application en base tant que la confirmation n'est pas explicitement donnée ; les changements de tous les paliers se cumulent dans le même plan avant confirmation

**Tests (suite 5)**
- `tests/unit/test_rebalance.py` : `resoudre_oraux_difficiles` (succès là où le glouton bloquerait, infaisabilité correctement détectée, marquage `hors_grille`, exclusions respectées), `construire_grille_etendue`, `duree_creneau_estimee`
- `tests/integration/test_disponibilite_examinateur.py` : escalade des 3 paliers sur un cas réellement bloqué pour le glouton, résolu uniquement par l'extension d'horaire

**Algorithme de placement (suite 4) — convergence du moteur génétique**
- Réparation locale étendue à chaque génération (algorithme mémétique) : en plus des violations d'exclusion (déjà présent), `algo_ga.py` corrige désormais aussi localement les écarts minimum insuffisants (`_reparer_ecart`) et le déséquilibre de charge entre examinateurs (`_reparer_desequilibre`) — ces critères ne s'amélioraient auparavant qu'au hasard du croisement/de la mutation, beaucoup plus lent à converger
- Les trois réparations cherchent leur partenaire d'échange sur toute la permutation (créneaux affectés ET inutilisés), pas seulement parmi les candidats déjà placés : un créneau inutilisé ne nécessite aucune vérification réciproque, et c'est souvent là que se trouve la place manquante
- Mutation adaptative : le taux décroît linéairement de `ALGO_GA_MUTATION_RATE` (exploration) vers un minimum interne (exploitation), et chaque mutation déclenchée applique plusieurs swaps proportionnels au nombre de candidats plutôt qu'un seul (négligeable sur un grand chromosome)
- Sur un jeu de test de taille moyenne (60 candidats, 12 examinateurs), l'écart minimum candidat converge à 0 violation dès la génération 10 (contre 45 auparavant), et l'évolution s'arrête après 64 générations au lieu de 98

**Tests (suite 6)**
- `tests/unit/test_algo_ga.py` : réduction effective des violations d'écart minimum et de déséquilibre de charge par les nouvelles réparations locales, comportement du taux de mutation adaptatif (0 = aucun changement, 1 = déclenchement systématique)

**Gestion en cours de journée (suite 2) — changement de matière d'un candidat**
- Nouveau bouton **🔄 Changer** sur `/gestion/liste-candidats` : remplace un des deux oraux d'un candidat (choix1 ou choix2) par un oral dans une nouvelle matière, en cours de journée après le placement initial
- Réutilise à l'identique l'infrastructure de la disponibilité examinateur : placement glouton (palier 1), puis les deux mêmes paliers de résolution poussée par CP-SAT (mêmes horaires, puis extension d'horaire) si le glouton échoue à trouver un examinateur/créneau disponible dans la nouvelle matière
- Écran de prévisualisation avec le même code couleur (🟩/🟨/🟧🕐) ; à la confirmation, le choix (`choix1`/`choix2`) du candidat est mis à jour en base et les notifications SSE ciblent à la fois le nouvel examinateur et l'ancien (dont la salle/loge doit savoir que ce candidat ne viendra plus)
- Suggestion optionnelle (case à cocher) : compacter le planning de l'ancien examinateur en déplaçant son oral le plus tardif vers le créneau qui vient de se libérer
- Portée volontairement limitée à un seul candidat à la fois
- Nouvelles requêtes `SELECT_CANDIDAT_CHANGEMENT_MATIERE`, `SELECT_ORAL_POUR_CHANGEMENT_MATIERE`, `UPDATE_CANDIDAT_CHOIX1`, `UPDATE_CANDIDAT_CHOIX2` dans `db_facility_web.py` ; nouvelles fonctions `rebalance.planifier_changement_matiere()` et `rebalance.proposer_compaction()`
- En repli (heure d'origine impossible), le placement glouton (`rebalance._placer`, partagé par la disponibilité examinateur, le renfort et le changement de matière) privilégie désormais un créneau qui se termine juste avant un oral déjà planifié de l'examinateur ciblé — comble un trou dans son planning plutôt que d'isoler le nouvel oral loin de ses autres oraux ; à défaut, retombe sur la proximité avec l'heure d'origine comme avant

**Tests (suite 7)**
- `tests/unit/test_rebalance.py` : `planifier_changement_matiere` (priorité même heure, repli avec écart minimum respecté, exclusions respectées, aucune option disponible), `proposer_compaction` (proposition de l'oral le plus tardif, absence d'oral déplaçable, écart minimum non respecté, liste vide), préférence pour un créneau comblant un trou avant un oral existant de l'examinateur ciblé
- `tests/integration/test_changer_matiere_candidat.py` : câblage de la route (formulaire, refus d'une matière déjà choisie, aucune écriture en base pendant la prévisualisation, confirmation avec double notification ancien/nouvel examinateur)

### Fixed

- `/gestion/examinateur/disponibilite` : les oraux replacés uniquement grâce à la résolution poussée (palier 2 « mêmes horaires » ou palier 3 « extension d'horaire ») n'étaient jamais écrits en base à la confirmation — celle-ci recalculait le plan à partir de zéro (glouton seul), perdant silencieusement le résultat des paliers précédents. Le niveau de résolution atteint est désormais reporté d'une requête à l'autre (champ caché `niveau_resolution`) et rejoué avant application, pour que « Confirmer et notifier » persiste exactement ce qui a été prévisualisé.
- `/gestion/examinateur/disponibilite` (renfort) : un examinateur déclaré disponible seulement à partir d'une heure H pouvait se voir proposer un oral à une heure **antérieure** à H (repli de `_placer` sur « une autre heure déjà utilisée aujourd'hui », sans tenir compte de l'heure de disponibilité du renfort). La grille transmise à `planifier_renfort()` est désormais restreinte aux heures `>= H` ; un oral qui ne peut plus être replacé dans cette fenêtre reste simplement chez son examinateur actuel (comportement déjà non bloquant de `planifier_renfort`).
- `/gestion/edit-examinateur` : le tableau des oraux de l'examinateur n'affichait plus le numéro du candidat, l'établissement, ni aucune heure — la route utilisait par erreur `SELECT_ORAUX_EXAMINATEUR_CONFLITS` (requête dédiée à la détection de conflits, avec bien moins de champs) au lieu de `SELECT_ORAUX_EXAMINATEUR`. Corrigé en utilisant la bonne requête, complétée avec `heure_oral`/`heure_fin` (elle n'exposait auparavant que `heure_sujet`) ; le tableau affiche maintenant trois colonnes distinctes : Heure sujet, Heure début, Heure fin.
- `heure_filter` (filtre Jinja2 `|heure`) : plantait en production (`AttributeError: 'str' object has no attribute 'total_seconds'`) dès qu'une colonne TIME était rendue directement depuis une ligne SQL brute (ex. `/gestion/edit-examinateur`) — mysql-connector-python peut renvoyer un `timedelta` ou une chaîne `HH:MM:SS` selon le driver/contexte. Le filtre normalise désormais la valeur via `_to_td()` (même logique que le reste du code) avant formatage, quel que soit le type reçu.
- `AlgoOne.calcul_horaires()` : un examinateur dont `Heure mini` (créneaux interdits en tête, cf. début de journée décalé) était postérieure à l'heure de début générale voyait son premier oral décalé d'un créneau supplémentaire par rapport à l'heure déclarée (ex. `Heure mini = 8:00` avec une journée à 7h20 → premier oral à 8h20 au lieu de 8h00). La condition qui avance l'horloge au premier passage de la boucle testait l'index absolu (`i_oral != 0`) au lieu de l'index de départ réel après les créneaux interdits (`i_oral != i`), provoquant une avance en double. N'affectait pas les examinateurs sans créneau interdit (`Heure mini` = heure de début de journée).
- `/gestion/algo/stop` : un arrêt manuel (bouton Stop, ou rechargement de `/gestion/algo` pendant un calcul) pouvait laisser le solveur CP-SAT tourner indéfiniment — `Solve()` est un appel natif bloquant qui ne vérifie ni signaux OS ni même son propre `max_time_in_seconds` de façon fiable sous charge (cf. issues OR-Tools #4882, #2310, #2058). `stop_algo()` envoie toujours SIGTERM en premier, mais escalade désormais vers SIGKILL après un délai de grâce de 5s si le groupe de processus est toujours vivant — SIGKILL ne peut ni être bloqué ni ignoré. Comportement inchangé par ailleurs : un arrêt manuel ne publie toujours aucune solution (contrairement à l'expiration normale de `cp_timeout`).

**Tests (suite 12)**
- `tests/unit/test_algo_bg.py::TestStopAlgo::test_escalade_vers_sigkill_si_sigterm_ignore` : un process qui ignore SIGTERM est bien arrêté via l'escalade SIGKILL après le délai de grâce

**Tests (suite 14)**
- `tests/unit/test_algo.py::TestPremierOralApresCreneauxInterdits` : le premier oral d'un examinateur respecte pile son `Heure mini` déclarée quand la journée commence plus tôt (créneaux interdits en tête) ; non-régression quand `Heure mini` == heure de début de journée (aucun créneau interdit)

### Removed

- **Moteur génétique** (`algo_ga.py`, `AlgoGA`) : retiré après évaluation — qualité de placement trop en retrait des moteurs Monte-Carlo et CP-SAT, y compris après plusieurs tentatives d'amélioration de la convergence (réparation locale mémétique, mutation adaptative). Suppression complète : `algo_ga.py`, `tests/unit/test_algo_ga.py`, la branche `ALGO_ENGINE=genetic` dans `algo.py`, les paramètres `ga_population`/`ga_generations`/`ga_timeout`/`ga_mutation_rate` (backend et UI `/gestion/algo`), et l'option « Génétique » du sélecteur de moteur. `monte_carlo` et `cpsat` restent les deux seuls moteurs disponibles.

## [2026.2] — 2026-07-06

### Added

**Interface d'administration**
- Sidebar de navigation admin fixe (`admin_nav.html`) sur toutes les pages `/gestion/*` : 7 icônes SVG (oraux, candidats, examinateurs, documents, algo, identifiants, monitoring), tooltip au survol, état actif par page, theming automatique
- Icône "Recharger les pages" dans la sidebar (action POST, remplace le lien textuel sur `index_gestion`)
- Page liste des candidats (`/gestion/liste-candidats`) : tableau trié par nom, indicateur tiers-temps, accès direct à l'édition
- Page édition candidat (`/gestion/edit-candidat`) : modification du nom complet, du numéro et du statut tiers-temps sans recalcul des oraux
- Sélecteur multiple (multi-select) pour les établissements des examinateurs dans `/gestion/edit-examinateur`
- Liens d'accès admin vers la liste des candidats depuis la page d'accueil (`index.html`)
- Option **Affichage détaillé (debug)** dans les paramètres avancés de l'algo (`ALGO_DEBUG`) : affiche dans la console le détail interne de chaque run (chargement des données, appairage, calcul des horaires), en plus du lancement/fin de run affichés par défaut
- Page `/gestion/algo` : avertissement et confirmation avant de quitter la page pendant un run, et arrêt effectif du run (route `POST /gestion/algo/stop`, `algo_bg.stop_algo()`) si l'utilisateur quitte quand même — envoie SIGTERM au groupe de processus (algo.py + workers `multiprocessing.Pool`) pour ne pas laisser de processus orphelins
- Renouvellement des identifiants candidats/examinateurs : bouton "↺ Renouveler" par ligne directement depuis `/gestion/liste-candidats` et `/gestion/liste-examinateurs`, avec message et lien de téléchargement du fichier de lot regénéré, sans quitter la liste (`link_back`)

**Infrastructure**
- Route `GET /health` (vérification DB + Redis) exemptée du rate limiter — compatible orchestrateurs
- Healthcheck Docker pour le service `app` (via Python urllib, sans dépendance curl)
- Sentry : intégration optionnelle (`SENTRY_DSN` dans `.env`, `traces_sample_rate=0.05`, prod uniquement) — configuré via `setup_new_site.py --sentry-dsn` ou question interactive

**Tests**
- Tests unitaires pour `algo.py` : placement, capacité insuffisante, cohérence des horaires, écart minimum candidat, timing (`tests/unit/test_algo.py`), option debug
- Tests d'intégration : édition/suppression d'examinateurs (validation du nombre de requêtes, confirmation de la suppression), renouvellement des identifiants
- Script de test de charge SSE (`tests/load/test_sse_rate_limit.py`)
- Configuration pre-commit (`pytest` avant chaque commit)

**Système de thème**
- Palette CSS dérivée algorithmiquement depuis une couleur d'accent unique (`ACCENT_COLOR`) — route Flask `/theme.css` surchargeant les variables de `main.css`
- 12 tokens de palette (web + PDFs ReportLab) partagés via `theme.py`

**Documentation**
- `docs/algo.md` — fonctionnement de l'algorithme de placement
- `docs/architecture.md` — philosophie générale et décisions techniques (Flask, SSE, gevent, Redis, algo séparé)
- `docs/securite.md` — niveaux d'accès, mesures de sécurité, RGPD
- `docs/setup.md` — configuration complète d'un nouveau site
- `docs/structure.md` — arborescence et dépendances
- `docs/secrets_backup.md` — procédure de sauvegarde des secrets
- `docs/capacity.md` — capacité et rate limiting SSE

### Changed

**Sécurité**
- `algo.py` : credentials temporaires écrits dans `/dev/shm` (RAM, jamais sur disque) avec fallback sur `data/` si indisponible
- `app.py` : `_CREDENTIALS_TMP_FILE` pointe vers `/dev/shm/second_oral_creds_new.json` (cohérent avec `algo.py`)
- `app.py` : validation du nom de fichier `new_papillon` (regex anti path-traversal) harmonisée entre `/gestion/liste-examinateurs` et `/gestion/liste-candidats`

**Algorithme**
- `db_facility_save.py`/`algo.py` : remplissage de la DB en fin de run ~7x plus rapide (mesuré : 19.8s → 2.9s sur données réelles) — le goulot d'étranglement n'était pas les `INSERT` (`executemany()` adopté, gain marginal) mais `hash_password()` (scrypt, ~150ms/appel par conception, coût de sécurité délibéré) appelé séquentiellement pour chaque candidat/examinateur/loge dans `to_dict()` ; `hashlib.scrypt` relâche le GIL pendant le calcul, donc un `ThreadPoolExecutor` parallélise ces appels indépendants sans aucun compromis de sécurité
- `algo.py` : logs internes de chaque run passés de `INFO` à `DEBUG` (seuls le lancement, la fin et les échecs de chaque run restent en `INFO` par défaut) — console beaucoup moins verbeuse ; l'option **Affichage détaillé (debug)** réactive le détail complet
- `algo.py` : verrou inter-processus (`multiprocessing.Lock`) autour des handlers de logging — les runs parallèles (Pool) partagent la même sortie/le même fichier et pouvaient entrelacer leurs écritures au milieu d'un caractère UTF-8 multi-octets, corrompant le flux lu côté serveur web
- `algo.py` : le verrou ci-dessus protégeait aussi le fichier de log local `fh` (jamais lu par le serveur web), toujours au niveau `DEBUG` quel que soit `ALGO_DEBUG` — il était donc acquis à chaque `log.debug()` des boucles per-candidat, plusieurs centaines de milliers de fois sur un batch `N_run=1000`, re-sérialisant une grande partie du calcul parallèle. Le verrou ne protège plus que `ch` (seul handler réellement piped vers le serveur web), `fh` redevient un `FileHandler` classique — un remplacement par `QueueHandler`/`QueueListener` avait été tenté puis abandonné : le pickling de chaque `LogRecord` et la consommation mono-thread du listener s'est révélé encore plus lent (voire bloquant) sur ce volume de logs
- `algo_bg.py` : subprocess lancé avec `encoding="utf-8", errors="replace"` — un octet mal décodé ne fait plus planter le streaming SSE de la sortie de l'algo

**Génération PDF**
- Remplacement de `pypdftk` par `pypdf` pour la concaténation PDF — supprime la dépendance Java du Dockerfile
- Mise en page des papillons de connexion refactorisée : marges, positionnement des blocs identifiant/mot de passe

**Vérification des conflits**
- `_check_conflits_oral` : utilise `heure_oral` (et non `heure_sujet`) pour la plage examinateur — conflits détectés plus précisément sur la durée réelle de l'oral

**Édition d'un oral**
- `/gestion/edit-oral` : seul `heure_sujet` est modifiable dans le formulaire ; `heure_oral` et `heure_fin` sont recalculés côté serveur pour préserver les durées d'origine (préparation et oral)

**Liste générale**
- `liste.html` hors grand écran (`dont_scroll`) : affiche les trois horaires (sujet, oral, fin) au lieu du seul horaire de sujet
- `index_gestion.html` (`/gestion`) : affiche également les trois horaires (sujet, oral, fin)

**Timers de loge**
- Polling partagé pour les vues en lecture seule (examinateurs, candidats) : une seule boucle de polling par page, quel que soit le nombre de candidats affichés
- Route `/loge/timer-state` exemptée du rate limiter (appels automatiques fréquents)
- Accès étendu aux timers en lecture pour tous les utilisateurs authentifiés (admin, examinateur, candidat)

**UX**
- Rate limiting SSE : `30/min` → `300/min` (validé empiriquement — supporte ~120 connexions simultanées)
- Tables larges : défilement horizontal sur mobile (wrapper `.table-scroll`)
- Signature numérique : données préservées lors du redimensionnement du canvas
- Tableau des oraux (`index_gestion`) : couleurs alternées de fond pour une meilleure lisibilité
- Pages archive et verify-logs : liens de navigation textuels supprimés (remplacés par la sidebar)

**README**
- Réécrit comme page d'entrée courte avec liens vers les docs — contenu détaillé extrait dans `docs/`

**Divers**
- Suppression du bouton "Recharger les pages" de `index_gestion.html` (déjà présent dans la sidebar admin)

### Fixed
- `algo.py` : l'écart minimum entre les deux oraux d'un candidat pouvait être violé sans être détecté — le contrôle de placement (`recherche_creneau`/`verif_ecart_creneaux`) compare des indices de créneau en supposant 20 min/créneau pour toutes les matières, alors que `calcul_horaires()` avance parfois d'un pas différent selon la matière (ex. `Lettres`/`SES` : préparation 30 min non multiple de l'oral 20 min → pas réel de 30 min) ; et même quand la violation était détectée (`verif_ecart_horaire`, `stats['candidats']`), la sélection du « meilleur » run ne se basait que sur le taux d'occupation examinateurs (`stats['profs']`), sans jamais en tenir compte. La sélection (`selectionner_meilleur_algo`) ignore désormais tout run non conforme à l'écart minimum candidat tant qu'un run conforme existe dans le batch ; à défaut, publie le meilleur run disponible avec un `CRITICAL` explicite
- `algo.py` : `calcul_horaires()` pouvait chevaucher de quelques minutes l'oral d'un candidat tiers-temps et celui du candidat suivant dans la même salle — le délai supplémentaire (`temps_preparation / 3`) était arrondi à la minute près pour le candidat tiers-temps lui-même mais à la dizaine de minutes pour compenser le créneau suivant (ex. préparation 40 min → délai réel 13 min contre seulement 10 min compensés), sous-compensant systématiquement de quelques minutes. Les deux arrondis sont désormais identiques
- `algo.py` : les statistiques du meilleur algo (remplissage examinateurs, écart mini candidats) n'apparaissaient plus dans le log console — le passage de `statistiques()` en `log.debug` (option `ALGO_DEBUG`) masquait aussi ce résumé final ; désormais loggé explicitement en `INFO`
- Token CSRF dans la sidebar : conflit entre la variable `csrf_token` (string) passée au contexte de `gestion_algo` et la fonction Flask-WTF — résolu par test `is callable`
- `algo.py` : erreur "Pas de créneau trouvé" remplacée par `PasDeCreneauDisponible` avec contexte (numéro candidat, nombre d'examinateurs) — causes loggées en `CRITICAL` quand tous les runs échouent
- `reports.py` : ajout du répertoire `webserver/` au `sys.path` (erreur d'import à froid)
- `db_facility_web.py` : requête `SELECT_INFOS_CANDIDAT_BY_ID` correctement nommée et utilisée
- `db_facility_web.py` : `SELECT_EXAMINATEUR_INFOS` comptait `COUNT(*)` sur une jointure `LEFT OUTER JOIN Oral`, ce qui affichait 1 oral (au lieu de 0) pour un examinateur sans oral assigné — remplacé par `COUNT(Oral.id)`

### Removed
- Dépendance Java (suppression de `pypdftk`, remplacé par `pypdf`)
- Fichiers et scripts dépréciés : ancien script 2FA, générateur de clés de login, CSV de données de test, assets statiques obsolètes

---

## [2026.1] — 2026-01-01

### Added
- Placement automatique des oraux : algorithme glouton avec 1 000 runs parallèles (multiprocessing), sélection du meilleur résultat
- Interface web complète : consultation candidats, salles, loges ; émargement dématérialisé (signature canvas)
- Authentification TOTP admin, mot de passe par salle (examinateurs), papillon numérique (candidats), mot de passe loge
- Streaming temps réel via SSE (Redis pub/sub) : suivi de l'algo, état des timers, compteurs de présence
- Upload CSV / ODS avec validation (disciplines, créneaux, contraintes établissements)
- Génération PDF : papillons de connexion, listes oraux, listes candidats, listes loges, fiches salles
- Store chiffré des identifiants `credentials.enc` (AES-256-GCM, clé dérivée HKDF-SHA256) — les identifiants persistent entre les relances de l'algo
- Renouvellement des identifiants sans relancer l'algo (`/gestion/credentials`)
- Rate limiting (Flask-Limiter + Redis), protection CSRF, CSP strict, headers de sécurité (Talisman)
- Requêtes SQL paramétrées (prévention injection SQL)
- Hachage des mots de passe scrypt avec pepper et salt par identifiant
- Docker Compose : MariaDB 11, Redis 7, gunicorn/gevent, nginx interne + hôte, Let's Encrypt
- CI GitHub Actions : tests unitaires et d'intégration, mypy, flake8

### Known limitations
- `algo.py` sans tests (corrigé en 2026.2)
- Credentials temporaires écrits sur disque (corrigé en 2026.2)
- Pas de backup des secrets documenté (corrigé en 2026.2)
- Pas de healthcheck pour le conteneur app (corrigé en 2026.2)

---

[2026.2]: https://github.com/olivier-boesch/second-oral/compare/v2026.1...v2026.2
[2026.1]: https://github.com/olivier-boesch/second-oral/releases/tag/v2026.1
