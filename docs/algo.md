# Algorithme de placement des oraux (`algo.py`)

## Vue d'ensemble

`algo.py` résout le problème d'appairage candidats ↔ examinateurs pour les oraux du second groupe (bac). Deux moteurs sont disponibles, sélectionnables via le paramètre `engine` (`/gestion/algo` ou variable d'environnement `ALGO_ENGINE`) :

- **`monte_carlo`** (historique, défaut) — recherche aléatoire massivement parallèle : on lance 1 000 runs indépendants en multiprocessing, chacun tente un placement différent (ordre des candidats aléatoire à chaque run), et on conserve le meilleur résultat (`algo.py`, `AlgoOne`).
- **`cpsat`** (`algo_cp.py`, `AlgoCP`) — modélise l'appairage comme un problème de contraintes/optimisation résolu par Google OR-Tools CP-SAT, en une seule résolution. L'écart minimum candidat est une contrainte **garantie** (jamais de run "non conforme"). Le placement exact varie volontairement d'un lancement à l'autre (ordre de parcours mélangé, graine du solveur, bruit de désambiguïsation dans l'objectif) tout en restant proche de l'optimum de tassement des créneaux — voir le commentaire en tête de `AlgoCP.resoudre()`.

```
                              ┌─ monte_carlo : 1000 runs parallèles ──► meilleur run ─┐
candidats.csv ──┐             │                  (Pool de CPUs)                       │
examinateurs.csv─┤──► ALGO_ENGINE ┤                                                    ├──► BDD + PDFs
preps.csv ──────┘             │                                                        │
                              └─ cpsat : une résolution CP-SAT (OR-Tools) ─────────────┘
```

> Un troisième moteur (algorithme génétique, `algo_ga.py`) a été expérimenté puis retiré :
> la qualité de placement obtenue restait trop en retrait des deux autres moteurs même
> après plusieurs passes d'amélioration (réparation locale étendue, mutation adaptative).

### Équité entre examinateurs d'une même matière

Les deux moteurs répartissent la charge le plus équitablement possible entre les
examinateurs d'une même matière (écart maximum d'1 oral entre le plus chargé et
le moins chargé, quand la répartition parfaite n'est pas un multiple exact) :

- **`monte_carlo`** : `AlgoOne.recherche_creneau()` choisit, parmi les
  examinateurs offrant un créneau valide, celui qui a le moins d'oraux déjà
  attribués (la proximité du créneau au matin n'intervient qu'en cas d'égalité).
- **`cpsat`** : un terme d'objectif pénalise l'écart entre la charge maximale et
  minimale par matière, avec un poids délibérément énorme par rapport au terme
  de tassement des créneaux — le solveur ne sacrifie jamais l'équité pour un
  meilleur tassement.

### Performance : cache CSV par worker (Monte-Carlo)

`multiprocessing.Pool()` ne crée que `cpu_count()` processus worker, qui traitent
chacun plusieurs runs séquentiellement (jusqu'à `ALGO_N_RUN`, 1000 par défaut) — sans
précaution particulière, chaque run relirait et re-parserait indépendamment les 3 CSV
depuis le disque alors que leur contenu ne change jamais pendant tout le batch.

`algo._initialiser_cache_worker()` (passé comme `initializer` à `Pool`) parse les 3 CSV
une seule fois à la création de chaque worker ; `AlgoOne.setup_from_files()` réutilise
ensuite ce cache s'il est disponible. Sans effet en dehors de ce contexte précis : le
moteur CP-SAT (une seule résolution, pas de `Pool`), les tests, et un usage direct en
script continuent de lire les fichiers normalement.

---

## Lancement

### Voie normale : interface web

L'algo se lance depuis l'interface d'administration : **`/gestion/algo`** → bouton *Lancer l'algorithme*.

`algo_bg.py` démarre alors `algo.py` dans un **sous-processus séparé** (via `subprocess.Popen`). La sortie standard d'`algo.py` est lue ligne par ligne et publiée sur le canal SSE `algo_output` (via Redis), ce qui permet de suivre la progression en temps réel dans le navigateur sans bloquer le serveur Flask.

Une fois le sous-processus terminé, un callback (`_absorb_credentials_file` dans `app.py`) :
1. Lit `data/credentials_new.json` (identifiants en clair écrits par algo.py)
2. Les intègre dans le store chiffré `data/credentials.enc` (AES-256-GCM)
3. Supprime immédiatement `credentials_new.json` — le fichier en clair ne persiste jamais plus de quelques secondes

```
Admin ──► POST /gestion/algo/run
            │
            ▼
        algo_bg.py ──► subprocess : algo.py
            │               │ stdout (ligne par ligne)
            │               ▼
            │           Redis pub/sub → SSE → navigateur (log en direct)
            │               │ fin (code retour)
            ▼               ▼
        _absorb_credentials_file()
            ├── lit data/credentials_new.json
            ├── chiffre → data/credentials.enc
            └── supprime credentials_new.json
```

L'app expose aussi `/gestion/algo/is-running` pour savoir si un run est en cours (un seul run simultané est autorisé).

### Voie alternative : `run_algo.sh`

Un script bash est disponible pour lancer l'algo depuis la ligne de commande sur l'hôte (usage hors-ligne, test, ou urgence) :

```bash
./run_algo.sh           # run normal
./run_algo.sh --dry-run # affiche les commandes sans les exécuter
```

`run_algo.sh` arrête le conteneur `app`, attend que MariaDB soit prête, lance `algo.py` dans Docker, puis redémarre l'app. À la différence de la voie web, le chiffrement des credentials n'est **pas** effectué automatiquement — il faut passer par `/gestion/credentials` ensuite si besoin.

---

## Paramètres configurables

Les paramètres sont modifiables depuis l'interface web (`/gestion/algo` → section Paramètres) et persistés dans `data/algo_params.json`. Ils sont aussi surchargeable via **variables d'environnement** :

| Variable                  | Défaut         | Description                                                             |
|---------------------------|----------------|---------------------------------------------------------------------------|
| `ALGO_ENGINE`             | `monte_carlo`  | Moteur de résolution : `monte_carlo` (historique) ou `cpsat`              |
| `ALGO_N_RUN`              | `1000`         | Nombre de runs parallèles (Monte-Carlo uniquement)                        |
| `ALGO_ECART_MINI`         | `80` (min)     | Écart minimum entre les deux oraux d'un candidat                         |
| `ALGO_HEURE_DEBUT`        | `08:10`        | Heure de début des premiers créneaux                                     |
| `ALGO_CRENEAUX`           | `13`           | Nombre de créneaux disponibles par examinateur                           |
| `ALGO_CP_TIMEOUT`         | `60` (s)       | Délai max du solveur CP-SAT (CP-SAT uniquement, ignoré si `ALGO_CP_OPTIMAL` est actif) |
| `ALGO_CP_OPTIMAL`         | `false`        | ⚠️ Supprime toute limite de temps au solveur CP-SAT — cf. avertissement ci-dessous |
| `ALGO_PETITES_MATIERES_FIN_JOURNEE` | `true` | Repousse les matières peu demandées en fin de journée (les 2 moteurs) |
| `ALGO_SEUIL_PETITE_MATIERE`        | `5`    | Nombre de candidats en-dessous duquel une matière est jugée "petite" (nombre absolu, pas un ratio) |
| `ALGO_MARGE_PETITE_MATIERE`        | `2`    | Créneaux de marge laissés en plus du strict nécessaire pour une petite matière |
| `ALGO_PAUSE_MERIDIENNE_DEBUT`      | *(vide)* | Heure à partir de laquelle aucun oral ne doit être en cours pour un examinateur ; vide = désactivée |
| `ALGO_PAUSE_MERIDIENNE_DUREE`      | `0` (min) | Durée de la pause méridienne ; ignorée si `ALGO_PAUSE_MERIDIENNE_DEBUT` est vide |
| `ALGO_CRENEAU_CIBLE_FIN_JOURNEE`   | *(vide)* | Dernier créneau souhaité pour le dernier oral de la journée ; vide = désactivée. Objectif souple (les 2 moteurs), jamais bloquant |
| `ALGO_POIDS_CRENEAU_FIN_JOURNEE`   | `200`  | Poids de la pénalité "créneau cible de fin de journée" dans l'objectif CP-SAT (ignoré par Monte-Carlo, cf. ci-dessous) |
| `ALGO_POIDS_EQUITE`       | `1 000 000`    | Poids de l'équité de charge entre examinateurs d'une même matière (CP-SAT uniquement) — doit rester très supérieur aux autres poids |
| `ALGO_BRUIT_TASSEMENT`    | `25`           | Amplitude du bruit aléatoire de désambiguïsation du tassement (CP-SAT uniquement) — doit rester >= 1 |

Chaque variable spécifique à un moteur est ignorée quand un autre moteur est sélectionné.

### ⚠️ Mode optimal CP-SAT (`ALGO_CP_OPTIMAL`)

**Supprime totalement la limite de temps du solveur** (`ALGO_CP_TIMEOUT` est alors ignoré) : CP-SAT
ne s'arrête que lorsqu'il a **prouvé mathématiquement** que la solution trouvée est optimale, quel
que soit le temps nécessaire.

> **Avertissement** : sur un jeu de données réel (dizaines/centaines de candidats), cela peut
> prendre plusieurs heures, voire ne jamais aboutir — la preuve d'optimalité est un problème
> combinatoire bien plus coûteux que trouver une bonne solution. Le lancement de l'algorithme reste
> bloqué (en cours) tout ce temps ; seul un arrêt manuel (`/gestion/algo/stop`) permet de
> l'interrompre, auquel cas **aucune solution n'est publiée** (le processus est simplement tué,
> contrairement à une expiration de `ALGO_CP_TIMEOUT` qui conserve la meilleure solution trouvée).
>
> Réservé à des essais volontaires (diagnostic, petit jeu de données) — jamais recommandé pour un
> lancement réel en production. C'est pourquoi l'interface (`/gestion/algo`) affiche un
> avertissement explicite et demande une confirmation avant d'activer la case correspondante, et
> que le défaut reste désactivé (`ALGO_CP_OPTIMAL=false` / `cp_optimal: false`).

Désactivé par défaut, réglable uniquement via `ALGO_CP_OPTIMAL` ou la case à cocher **Mode optimal**
sur `/gestion/algo` (section avancée, à côté du délai max CP-SAT).

### Petites matières repoussées en fin de journée

Les matières peu demandées (peu de candidats par rapport aux créneaux disponibles chez leurs
examinateurs) voient leurs premiers créneaux marqués `CreneauInterdit` — le même mécanisme déjà
utilisé pour décaler le début de journée d'un examinateur (`Heure mini` dans `examinateurs.csv`).
Comme les deux moteurs respectent déjà `CreneauInterdit` de façon identique, ce comportement est
implémenté une seule fois, dans `AlgoOne._reserver_petites_matieres()` (appelée depuis
`setup_from_files()`), et profite donc automatiquement à `AlgoOne` et `AlgoCP` sans duplication.

Une matière est jugée "petite" quand son nombre de candidats est strictement inférieur à
`ALGO_SEUIL_PETITE_MATIERE` (un nombre absolu d'oraux, pas un ratio candidats/capacité). Le nombre
de créneaux laissés ouverts par examinateur est calculé à partir du nombre réel de candidats de
cette matière (`+ ALGO_MARGE_PETITE_MATIERE` de flexibilité, pour ne pas sur-contraindre l'écart
minimum candidat) — deux petites matières de tailles différentes obtiennent donc naturellement des
fenêtres de fin de journée différentes.

Ce comportement est **opt-in au niveau de l'API** (`AlgoOne.__init__(optimiser_petites_matieres=False)`
par défaut) pour ne pas changer le comportement des appelants existants (tests, scripts) qui ne le
demandent pas explicitement — mais activé par défaut en production via `__main__` (donc par défaut
dans `/gestion/algo`). L'activation (case **Petites matières en fin de journée**) et le seuil
(**Seuil petite matière**, en nombre de candidats) sont réglables directement depuis
`/gestion/algo` → section Paramètres, en plus de `ALGO_PETITES_MATIERES_FIN_JOURNEE` /
`ALGO_SEUIL_PETITE_MATIERE`.

> **Piège connu** : si un candidat a choisi **ses deux matières parmi celles jugées "petites"**,
> leurs fenêtres de fin de journée (calculées indépendamment par matière) peuvent se chevaucher
> presque exactement, ne laissant pas assez de place pour respecter l'écart minimum candidat entre
> les deux oraux — le placement peut alors devenir impossible pour *tous* les runs (Monte-Carlo)
> ou `INFEASIBLE` (CP-SAT), même si les fichiers CSV sont par ailleurs valides. Si l'algorithme
> échoue avec « Aucun placement valide trouvé » ou une erreur CP-SAT `INFEASIBLE`, désactiver cette
> option (ou ajuster le seuil pour qu'un seul des deux choix du candidat concerné reste "petit")
> permet de contourner le problème.

### Pause méridienne

Contrairement aux petites matières (qui bloquent des *créneaux* avant résolution), la pause
méridienne agit **après** la résolution, dans `AlgoOne.calcul_horaires()` — comme la pause
périodique existante (`temps_pause`/`intervalle_pause`, insérée toutes les N oraux), mais
déclenchée une seule fois par examinateur, dès que l'heure configurée est atteinte plutôt qu'un
nombre d'oraux. L'assignation candidat/examinateur/créneau n'est donc pas modifiée : seule la
conversion créneau → horaire réel décale les oraux suivants. Cette logique étant dans la classe de
base, elle profite automatiquement aux deux moteurs (`AlgoOne` et `AlgoCP`).

Dès qu'un oral entamerait ou chevaucherait la pause (créneau `[heure_sujet, heure_fin]`), il est
repoussé pour démarrer juste après la fin de la pause — une seule fois par examinateur, ensuite
les créneaux s'enchaînent normalement.

Désactivée par défaut (`ALGO_PAUSE_MERIDIENNE_DEBUT` vide) ; réglable depuis `/gestion/algo` (heure
de début + durée en minutes) ou via `ALGO_PAUSE_MERIDIENNE_DEBUT`/`ALGO_PAUSE_MERIDIENNE_DUREE`.

La pause méridienne configurée est aussi respectée par la replanification en cours de journée
(`webserver/rebalance.py` — absences/renforts d'examinateur, changement de matière d'un candidat) :
`_placer` (glouton), `resoudre_oraux_difficiles` (CP-SAT, paliers 2/3) et `construire_grille_etendue`
(extension d'horaire) n'ont jamais le droit de proposer un créneau qui ferait travailler un
examinateur pendant la pause — celle-ci est lue depuis `/gestion/algo` à chaque calcul de plan
(`app.py::_pause_meridienne_params`), donc toujours à jour même sans relancer l'algorithme.

#### Interaction avec l'écart minimum candidat (CP-SAT)

Piège identifié puis corrigé : la contrainte d'écart minimum candidat de `AlgoCP.resoudre()`
raisonnait à l'origine en **nombre de créneaux** (`abs(t1 - t2) >= creneaux_minimum_entre_oraux`),
en supposant implicitement une durée uniforme par créneau. Or le décalage de pause méridienne
ci-dessus est appliqué **par examinateur**, après résolution : deux oraux d'un même candidat (chez
deux examinateurs différents, donc potentiellement décalés différemment par la pause) pouvaient
satisfaire la contrainte en nombre de créneaux tout en ayant un écart réel très inférieur au minimum
demandé — jusqu'à plusieurs dizaines de minutes de moins dans des cas réels, alors que la contrainte
est censée être **garantie**. Monte-Carlo n'est pas concerné de la même façon : `verif_ecart_horaire()`
(après `calcul_horaires()`) détecte ce genre d'écart réel insuffisant et alimente
`stats['candidats']`, qui sert justement à écarter les runs non conformes dans
`selectionner_meilleur_algo()` — mais CP-SAT ne fait qu'une seule résolution, sans repli possible.

Corrigé en remplaçant l'index de créneau par un **temps réel** dans la contrainte : `AlgoCP._minutes_creneau()`
précalcule, par examinateur, les minutes écoulées depuis `heure_debut` jusqu'à chaque créneau —
réplique fidèlement `calcul_horaires()` (pauses périodiques et pause méridienne incluses), à
l'exception du tiers-temps (dépend du candidat assigné, donc inconnu avant résolution — même
limite qu'aujourd'hui pour le calcul post-résolution). `t1`/`t2` et la contrainte d'écart utilisent
ces minutes plutôt que le simple `creneau`, donc l'écart est désormais garanti **en minutes
réelles**, y compris pour un candidat dont les deux oraux encadrent la pause (l'écart réel est alors
naturellement majoré par la durée de la pause — plus précis qu'une simple contrainte en créneaux,
qui l'aurait sous-estimé dans ce cas précis). Le créneau cible de fin de journée et le terme de
tassement restent en revanche exprimés en index de créneau (approximation déjà acceptée, cf.
sections dédiées).

### Créneau cible de fin de journée

Objectif **souple** (jamais bloquant) : contrairement aux petites matières ou à l'écart minimum
candidat, ce réglage ne peut jamais rendre le placement infaisable — il influence seulement lequel,
parmi les placements par ailleurs valides, est retenu. Désactivé par défaut ; réglable depuis
`/gestion/algo` → section Paramètres (case **Activer** + créneau cible + poids CP-SAT), ou via
`ALGO_CRENEAU_CIBLE_FIN_JOURNEE`/`ALGO_POIDS_CRENEAU_FIN_JOURNEE`.

Le réglage est un **nombre de créneaux**, pas une heure — les deux moteurs comparent donc
exactement la même grandeur, sans aucune conversion :

- **Monte-Carlo** (`selectionner_meilleur_algo`) : parmi les runs déjà conformes à l'écart minimum
  candidat, le run élu n'est plus celui au meilleur taux d'occupation examinateurs, mais celui dont
  le dernier créneau utilisé (`AlgoOne.dernier_creneau_journee()`) est le plus petit — le taux
  d'occupation ne sert plus qu'à départager une égalité. Sans effet sur le repli (aucun run
  conforme). Exact et disponible immédiatement après `resoudre()`, sans attendre `calcul_horaires()`.
- **CP-SAT** (`AlgoCP.resoudre`) : `AlgoCP._cutoff_creneau_fin_journee()` borne simplement la valeur
  fournie à `[0, max_creneau]`. Le dépassement est ensuite pénalisé **par examinateur**, pas par
  oral : pour chaque examinateur, on prend le maximum entre 0 et son propre dépassement le plus
  profond (`(dernier créneau utilisé par cet examinateur) - cutoff`, via `model.AddMaxEquality`,
  même construction que `charge_max`/`charge_min` pour l'équité) plutôt que de sommer le
  dépassement de chacun de ses oraux en retard — un examinateur ayant plusieurs oraux tardifs n'est
  donc pénalisé qu'une fois pour son pire cas, mais **chaque** examinateur est individuellement
  poussé à respecter la cible, ce qui empêche le solveur de laisser un seul examinateur absorber
  tout le dépassement pendant que ses collègues finissent nettement plus tôt. Poids
  `ALGO_POIDS_CRENEAU_FIN_JOURNEE` (défaut `200`, nettement sous `ALGO_POIDS_EQUITE` : l'équité de
  charge reste toujours prioritaire), sans jamais interdire ces créneaux (contrainte dure) : le
  solveur les utilise quand même si c'est nécessaire pour rester faisable, il paie juste une
  pénalité pour le faire.

Les trois poids CP-SAT (`ALGO_POIDS_EQUITE`, `ALGO_BRUIT_TASSEMENT`, `ALGO_POIDS_CRENEAU_FIN_JOURNEE`)
sont réglables depuis `/gestion/algo` → section Paramètres → **Paramètres avancés**, regroupés au
même endroit pour faciliter leur comparaison — le créneau cible lui-même (activation + valeur) reste
dans les paramètres principaux, à côté de la pause méridienne et des petites matières.

Une version antérieure de ce réglage était exprimée en heure et convertie en index de créneau via
une durée d'oral moyenne approchée côté CP-SAT — remplacé par un réglage direct en créneaux pour
éviter cette approximation et unifier les deux moteurs sur la même grandeur exacte.

---

## Structure des fichiers CSV d'entrée

Les CSV sont uploadés depuis `/gestion/algo` (format natif ou fichier ODS converti automatiquement en 3 CSV).

### `data/candidats.csv`

```
CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs
Jean Dupont (1234567890);Maths;Philo;0;Lycée X;
Marie Martin (0987654321);Philo;Lettres;1;Lycée Y;Nom Prof à éviter
```

- `CANDIDAT` : `"Nom Prénom (numéro)"` — le numéro est entre parenthèses
- `TT` : `1` si tiers-temps, `0` sinon
- `Profs` : noms des examinateurs à éviter (virgule-séparés, optionnel)

### `data/examinateurs.csv`

```
Nom;Disc.poste;Salle;Heure mini;Etab;Loge
Dupont Jean;Maths;A101;8;Lycée X;Loge B
```

- `Heure mini` : heure de début de disponibilité (entier, ex. `8` = 8h00)
- `Etab` : si renseigné, les candidats de cet établissement ne peuvent pas passer chez cet examinateur

### `data/preps.csv`

```
Matiere;Matière court;Temps preparation (min);Duree (min)
Mathématiques;Maths;20;20
Philosophie;Philo;30;20
```

---

## Algorithme de placement (`AlgoOne.resoudre`)

L'algorithme glouton place les candidats un par un, matière par matière (les matières les plus demandées en premier) :

1. Pour chaque matière, mélanger aléatoirement la liste des candidats (source de variabilité entre les 1 000 runs)
2. Pour chaque candidat, chercher le créneau le plus tôt possible chez n'importe quel examinateur disponible, sous contrainte :
   - le créneau doit être libre chez l'examinateur
   - l'écart avec l'autre oral du candidat (créneau de référence) doit être ≥ `ALGO_ECART_MINI`
   - l'examinateur ne doit pas être sur la liste des profs à éviter du candidat
   - l'examinateur ne doit pas appartenir au même établissement que le candidat
3. Si aucun créneau n'est trouvé → `PasDeCreneauDisponible` (le run échoue, on passe au suivant)

### Erreurs diagnosticables

```python
from algo import AlgoError, PasDeCreneauDisponible

try:
    alg.resoudre()
except PasDeCreneauDisponible as e:
    print(f"Candidat bloqué : {e.candidat.numero} — {e.n_examinateurs} examinateur(s)")
except AlgoError as e:
    print(f"Erreur algo : {e}")
```

Quand **tous** les runs échouent, le log (visible dans l'interface web et dans `data/log.txt`) affiche les causes dédupliquées :

```
CRITICAL Aucun placement valide trouvé sur l'ensemble des runs.
CRITICAL   Cause : Candidat 1234567890 (Dupont Jean) — aucun créneau disponible (1 examinateur(s))
```

---

## Sélection du meilleur run

Parmi les runs réussis, on retient celui qui **maximise le taux d'occupation des créneaux examinateurs** (colonne `profs` des statistiques). En cas d'égalité, l'écart minimum entre oraux des candidats sert de critère secondaire.

---

## Sorties

| Fichier / destination                              | Contenu                                    |
|----------------------------------------------------|--------------------------------------------|
| Base de données MariaDB                            | Candidats, examinateurs, oraux, horaires   |
| `webserver/generated/papillons_examinateurs.pdf`   | Papillons de connexion des examinateurs    |
| `webserver/generated/papillons_candidats.pdf`      | Papillons de connexion des candidats       |
| `webserver/generated/papillons_loges.pdf`          | Papillons de connexion des loges           |
| `data/credentials.enc`                             | Store chiffré des identifiants (AES-256-GCM) |
| `data/log.txt`                                     | Log détaillé de l'exécution               |
| `data/credentials_new.json`                        | Transitoire (quelques secondes), effacé automatiquement par le callback Flask |

`webserver/generated/` est volontairement hors de `webserver/static/` : ces PDF contiennent des
identifiants de connexion en clair et ne doivent jamais être servis en statique par nginx (ni par
le handler statique intégré de Flask) — seule la route `/download` (authentification requise) y
donne accès. Cf. [securite.md](securite.md).

---

## Contraintes et limites connues

- L'algorithme est **non-déterministe** : deux exécutions avec les mêmes données peuvent donner des placements différents (l'aléatoire est la source de diversité entre les runs).
- Le taux d'échec des runs augmente quand la capacité est juste. Avec beaucoup de candidats pour peu de créneaux, la totalité des 1 000 runs peut échouer.
- Le nombre de CPUs disponibles dans le conteneur Docker limite le parallélisme effectif.
- `data/credentials_new.json` est écrit en clair pendant quelques secondes (voir sorties ci-dessus). Ce point est connu et documenté dans [docs/secrets_backup.md](secrets_backup.md).

---

## Tests

Les tests unitaires de l'algorithme sont dans [tests/unit/test_algo.py](../tests/unit/test_algo.py) :

| Classe de test                  | Ce qui est vérifié                                              |
|---------------------------------|-----------------------------------------------------------------|
| `TestAlgoSimplePlacement`       | Placement correct (2 oraux/candidat, bonnes matières, pas de double créneau) |
| `TestAlgoInsufficientCapacity`  | `PasDeCreneauDisponible` levée quand capacité insuffisante, message avec contexte |
| `TestAlgoHorairesCoherence`     | Pas d'overlap chez un examinateur, horaires complets après `calcul_horaires()` |
| `TestCandidatSeparationMinimum` | Écart entre les deux oraux d'un candidat ≥ `ALGO_ECART_MINI`  |
| `TestAlgoTiming`                | Temps de résolution < 10s pour 20, 50 et 100 candidats (run unique) |

```bash
python3 -m pytest tests/unit/test_algo.py -v
```
