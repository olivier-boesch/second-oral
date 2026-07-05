# Algorithme de placement des oraux (`algo.py`)

## Vue d'ensemble

`algo.py` résout le problème d'appairage candidats ↔ examinateurs pour les oraux du second groupe (bac). Trois moteurs sont disponibles, sélectionnables via le paramètre `engine` (`/gestion/algo` ou variable d'environnement `ALGO_ENGINE`) :

- **`monte_carlo`** (historique, défaut) — recherche aléatoire massivement parallèle : on lance 1 000 runs indépendants en multiprocessing, chacun tente un placement différent (ordre des candidats aléatoire à chaque run), et on conserve le meilleur résultat (`algo.py`, `AlgoOne`).
- **`cpsat`** (`algo_cp.py`, `AlgoCP`) — modélise l'appairage comme un problème de contraintes/optimisation résolu par Google OR-Tools CP-SAT, en une seule résolution. L'écart minimum candidat est une contrainte **garantie** (jamais de run "non conforme"). Le placement exact varie volontairement d'un lancement à l'autre (ordre de parcours mélangé, graine du solveur, bruit de désambiguïsation dans l'objectif) tout en restant proche de l'optimum de tassement des créneaux — voir le commentaire en tête de `AlgoCP.resoudre()`.
- **`genetic`** (`algo_ga.py`, `AlgoGA`) — algorithme génétique : fait évoluer une population de placements (encodage par permutation, un chromosome par matière) via sélection par tournoi, croisement OX, mutation et réparation locale. L'écart minimum candidat est une pénalité forte dans le fitness (best-effort, comme en Monte-Carlo) ; en revanche, les exclusions établissement/prof à éviter sont vérifiées strictement en fin d'évolution — l'algorithme échoue explicitement (`AucuneSolutionGA`) plutôt que de publier un planning qui les enfreindrait. Intérêt principal : la fonction de fitness peut absorber facilement de futurs critères d'optimisation (préférences, équité de charge...) sans reformuler un modèle de contraintes.

```
                              ┌─ monte_carlo : 1000 runs parallèles ──► meilleur run ─┐
candidats.csv ──┐             │                  (Pool de CPUs)                       │
examinateurs.csv─┤──► ALGO_ENGINE ┼─ cpsat : une résolution CP-SAT (OR-Tools) ────────┼──► BDD + PDFs
preps.csv ──────┘             │                                                        │
                              └─ genetic : population → générations (algo_ga.py) ─────┘
```

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
| `ALGO_ENGINE`             | `monte_carlo`  | Moteur de résolution : `monte_carlo` (historique), `cpsat` ou `genetic`   |
| `ALGO_N_RUN`              | `1000`         | Nombre de runs parallèles (Monte-Carlo uniquement)                        |
| `ALGO_ECART_MINI`         | `80` (min)     | Écart minimum entre les deux oraux d'un candidat                         |
| `ALGO_HEURE_DEBUT`        | `08:10`        | Heure de début des premiers créneaux                                     |
| `ALGO_CRENEAUX`           | `13`           | Nombre de créneaux disponibles par examinateur                           |
| `ALGO_CP_TIMEOUT`         | `60` (s)       | Délai max du solveur CP-SAT (CP-SAT uniquement)                          |
| `ALGO_GA_POPULATION`      | `150`          | Taille de la population (génétique uniquement)                           |
| `ALGO_GA_GENERATIONS`     | `300`          | Nombre maximum de générations (génétique uniquement)                     |
| `ALGO_GA_TIMEOUT`         | `60` (s)       | Délai max de l'évolution (génétique uniquement)                          |
| `ALGO_GA_MUTATION_RATE`   | `0.15`         | Probabilité de mutation par matière et par individu (génétique uniquement)|
| `ALGO_PETITES_MATIERES_FIN_JOURNEE` | `true` | Repousse les matières peu demandées en fin de journée (les 3 moteurs) |
| `ALGO_SEUIL_PETITE_MATIERE`        | `0.5`  | Ratio candidats/capacité en-dessous duquel une matière est jugée "petite"|
| `ALGO_MARGE_PETITE_MATIERE`        | `2`    | Créneaux de marge laissés en plus du strict nécessaire pour une petite matière |

Chaque variable spécifique à un moteur est ignorée quand un autre moteur est sélectionné.

### Petites matières repoussées en fin de journée

Les matières peu demandées (peu de candidats par rapport aux créneaux disponibles chez leurs
examinateurs) voient leurs premiers créneaux marqués `CreneauInterdit` — le même mécanisme déjà
utilisé pour décaler le début de journée d'un examinateur (`Heure mini` dans `examinateurs.csv`).
Comme les trois moteurs respectent déjà `CreneauInterdit` de façon identique, ce comportement est
implémenté une seule fois, dans `AlgoOne._reserver_petites_matieres()` (appelée depuis
`setup_from_files()`), et profite donc automatiquement à `AlgoOne`, `AlgoCP` et `AlgoGA` sans
duplication.

Une matière est jugée "petite" quand `candidats / (examinateurs × créneaux disponibles) <
ALGO_SEUIL_PETITE_MATIERE`. Le nombre de créneaux laissés ouverts par examinateur est calculé à
partir du nombre réel de candidats de cette matière (`+ ALGO_MARGE_PETITE_MATIERE` de flexibilité,
pour ne pas sur-contraindre l'écart minimum candidat) — deux petites matières de tailles
différentes obtiennent donc naturellement des fenêtres de fin de journée différentes.

Ce comportement est **opt-in au niveau de l'API** (`AlgoOne.__init__(optimiser_petites_matieres=False)`
par défaut) pour ne pas changer le comportement des appelants existants (tests, scripts) qui ne le
demandent pas explicitement — mais activé par défaut en production via `__main__` (donc par défaut
dans `/gestion/algo` et `ALGO_ENGINE`), piloté par `ALGO_PETITES_MATIERES_FIN_JOURNEE`.

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
| `webserver/static/docs/papillons_examinateurs.pdf` | Papillons de connexion des examinateurs    |
| `webserver/static/docs/papillons_candidats.pdf`    | Papillons de connexion des candidats       |
| `webserver/static/docs/papillons_loges.pdf`        | Papillons de connexion des loges           |
| `data/credentials.enc`                             | Store chiffré des identifiants (AES-256-GCM) |
| `data/log.txt`                                     | Log détaillé de l'exécution               |
| `data/credentials_new.json`                        | Transitoire (quelques secondes), effacé automatiquement par le callback Flask |

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
