# Guide administrateur — Workflow annuel 2ndOral

Ce document décrit le processus complet de préparation et de lancement des oraux de second groupe, du formatage des fichiers CSV jusqu'à la distribution des papillons le jour J.

---

## Vue d'ensemble du workflow

```
1. Préparer les données (via ODS modèle ou CSV individuels)
        ↓
2. Uploader le fichier ODS (ou les CSV) via /gestion/algo
        ↓
3. Lancer algo.py (placement automatique)
        ↓
4. Vérifier le résultat et corriger si besoin
        ↓
5. Imprimer et distribuer les papillons
        ↓
6. Jour des épreuves : émargements en temps réel
```

---

## 1. Les trois fichiers de données

Les données sont fournies soit via un **fichier ODS unique** (méthode recommandée), soit via **trois fichiers CSV séparés** (méthode avancée). Dans les deux cas, le serveur produit trois CSV dans `data/` avec les colonnes décrites ci-dessous.

> **Méthode recommandée — fichier ODS :**
> depuis `/gestion/algo`, cliquez **Télécharger le modèle ODS**. Le fichier contient 4 feuilles :
> - `candidats` : à remplir — listes déroulantes pour les disciplines, TT et établissement
> - `examinateurs` : à remplir — listes déroulantes pour la discipline, l'heure de début et l'établissement
> - `preps` : pré-remplie avec les 16 disciplines habituelles (à modifier si besoin)
> - `lycees` : référentiel des 249 lycées académie Aix-Marseille (lecture seule — la colonne `Etab` est une formule dynamique `Ville — Nom (UAI)`)
>
> Les colonnes `Etab` de `candidats` et `examinateurs` proposent une liste déroulante issue de la feuille `lycees`. Remplissez les feuilles sous LibreOffice Calc ou Excel, puis uploadez le fichier — le serveur le découpe automatiquement en 3 CSV.

### Format des CSV (généré depuis l'ODS ou fourni directement)

Tous les fichiers utilisent le **point-virgule `;` comme séparateur** et l'encodage **UTF-8**.

---

### 1.1 `preps.csv` — Matières et durées

Ce fichier définit les disciplines disponibles et leurs contraintes de temps. Il est **stable d'une année sur l'autre** et doit être mis à jour uniquement si les disciplines ou leurs durées changent.

**Format :**

```
Matiere;Matière court;Temps preparation (min);Duree (min)
```

| Colonne | Description | Exemple |
|---|---|---|
| `Matiere` | Nom complet de la discipline (utilisé dans la base) | `Sciences Économiques et Sociales` |
| `Matière court` | Abréviation utilisée dans les CSV candidats et examinateurs | `SES` |
| `Temps preparation (min)` | Durée de la phase de préparation en minutes | `30` |
| `Duree (min)` | Durée de l'oral (passage devant l'examinateur) en minutes | `20` |

**Contenu actuel de référence :**

```csv
Matiere;Matière court;Temps preparation (min);Duree (min)
Lettres;Lettres;30;20
Arts;Arts;30;30
Histoire-Géographie, Géopolitique et Sciences Politiques;HGGSP;20;20
Humanités, Littérature et Philosophie;HLP;20;20
Numérique Sciences Informatiques;NSI;20;20
Physique-Chimie;PC;20;20
Sciences de la Vie et de la Terre;SVT;20;20
Sciences Économiques et Sociales;SES;30;20
Anglais;Anglais;20;20
Mathématiques;Maths;20;20
Management;Mana;40;20
Droit Economie;Droit Eco;20;20
Langues, Littératures et Cultures Etrangères;LLCE;20;20
Anglais Monde contemporain;AMC;20;20
Philosophie;Philo;20;20
Sciences Industrielles de L'ingénieur;SII;20;20
```

> **Important :** les noms abrégés (`Matière court`) doivent correspondre exactement à ceux utilisés dans les colonnes `CHOIX DISCIPLINE` du fichier candidats et dans la colonne `Disc.poste` du fichier examinateurs. La correspondance est insensible à la casse mais l'orthographe doit être identique.

---

### 1.2 `candidats.csv` — Liste des candidats

Ce fichier liste tous les candidats inscrits aux oraux, avec leurs deux choix de disciplines et leurs contraintes éventuelles.

**Format :**

```
CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs
```

| Colonne | Description | Valeurs | Exemple |
|---|---|---|---|
| `CANDIDAT` | Nom, prénom et INE entre parenthèses | `NOM Prénom (INE)` | `Dupont Marie (1234567890A)` |
| `CHOIX DISCIPLINE 1` | Première discipline choisie | Nom court ou complet de `preps.csv` | `Maths` |
| `CHOIX DISCIPLINE 2` | Deuxième discipline choisie | Idem | `PC` |
| `TT` | Tiers-temps | `0` = non, `1` = oui | `0` |
| `Etab` | Établissement d'origine (abréviation libre) | Texte libre | `St Ex` |
| `Profs` | Examinateurs à éviter (séparés par virgule) | Noms tels que dans `examinateurs.csv` | `Dupont,Martin` |

**Exemple :**

```csv
CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs
Dupont Marie (1234567890A);Maths;PC;0;St Ex;
Martin Jean (0987654321B);SES;Anglais;1;Lumière;Durand
Leroy Sophie (1122334455C);Mana;Droit Eco;0;Cézanne;Dupont,Bernard
```

**Règles importantes :**
- L'INE doit être entre parenthèses, collé au nom : `Dupont Marie (1234567890A)`
- Les deux disciplines doivent exister dans `preps.csv` (nom complet ou abréviation)
- La colonne `Profs` peut être vide (laisser le champ vide, pas d'espace)
- Un candidat avec tiers-temps (`TT=1`) bénéficie automatiquement d'un temps de préparation majoré d'un tiers
- La colonne `Etab` empêche l'algorithme d'assigner un examinateur du même établissement que le candidat

---

### 1.3 `examinateurs.csv` — Liste des examinateurs

Ce fichier liste tous les examinateurs disponibles, leur salle et leurs contraintes.

**Format :**

```
Nom;Disc.poste;Salle;Heure mini;Etab;Loge
```

| Colonne | Description | Valeurs | Exemple |
|---|---|---|---|
| `Nom` | Nom complet de l'examinateur | Texte libre | `Dupont Marie` |
| `Disc.poste` | Discipline enseignée | Nom court ou complet de `preps.csv` | `Maths` |
| `Salle` | Numéro/nom de la salle | Texte libre (ex. `A101`) | `B201` |
| `Heure mini` | Heure de début au plus tôt (format 24h, entier) | `8` à `17` | `9` |
| `Etab` | Établissement(s) d'origine, séparés par virgule | Texte libre | `St Ex,Lumière` |
| `Loge` | Salle de loge de rattachement | Texte libre (ex. `B103`) | `B103` |

**Exemple :**

```csv
Nom;Disc.poste;Salle;Heure mini;Etab;Loge
Dupont Marie;Maths;A101;8;Lumière;A103
Martin Jean;PC;A102;8;St Ex;A103
Bernard Sophie;Anglais;B201;9;Cézanne;B203
Durand Paul;Mana;B202;8;St Ex,Diderot;B203
```

**Règles importantes :**
- `Disc.poste` doit correspondre à une discipline de `preps.csv`
- `Heure mini` définit l'heure à partir de laquelle l'examinateur peut recevoir des candidats. Un examinateur avec `Heure mini = 9` n'aura pas d'oral avant 9h00.
- `Etab` : l'algorithme évite d'assigner un candidat dont l'établissement (`Etab` dans candidats.csv) correspond à l'établissement de l'examinateur. Utiliser les **mêmes abréviations** dans les deux fichiers.
- `Loge` : regroupe les examinateurs par salle de loge pour l'organisation des surveillants. Toutes les salles d'une même loge doivent avoir le même identifiant de loge.
- Un examinateur peut appartenir à plusieurs établissements (séparés par virgule) : `St Ex,Diderot`

---

## 2. Contraintes respectées par l'algorithme

L'algorithme (`algo.py`) tente de trouver le meilleur placement en respectant :

| Contrainte | Valeur actuelle | Description |
|---|---|---|
| Écart minimum entre deux oraux d'un même candidat | 1 h 20 min | Un candidat ne peut pas passer deux oraux à moins de 1h20 d'intervalle |
| Heure de début | 8h10 | Premier créneau possible |
| Nombre de runs | 1 000 | L'algorithme tente 1 000 placements aléatoires et conserve le meilleur |
| Anti-conflit établissement | Automatique | Un candidat ne passe pas avec un examinateur de son propre lycée |
| Examinateurs à éviter | Colonne `Profs` | Évite des examinateurs spécifiques pour un candidat |
| Tiers-temps | Colonne `TT` | Majore automatiquement le temps de préparation d'un tiers |

---

## 3. Processus de préparation des fichiers

### Étape 1 — Récupérer les données

**Candidats :** extraire depuis le logiciel de gestion des inscriptions (Cyclades ou équivalent). Vérifier que chaque candidat a bien deux disciplines et son INE.

**Examinateurs :** établir la liste des examinateurs disponibles avec leurs salles et loges. Confirmer les heures de disponibilité.

**Matières :** vérifier que toutes les disciplines choisies par les candidats et les disciplines des examinateurs sont présentes dans `preps.csv`. Ajouter les manquantes si nécessaire.

### Étape 2 — Vérifier la cohérence

Avant de lancer l'algorithme, s'assurer que :

- [ ] Chaque discipline dans `candidats.csv` (`CHOIX DISCIPLINE 1`, `CHOIX DISCIPLINE 2`) existe dans `preps.csv` (nom complet ou abréviation)
- [ ] Chaque discipline dans `examinateurs.csv` (`Disc.poste`) existe dans `preps.csv`
- [ ] Chaque candidat a exactement deux disciplines différentes
- [ ] Les abréviations d'établissements sont cohérentes entre `candidats.csv` et `examinateurs.csv`
- [ ] Chaque examinateur a une salle et une loge renseignées
- [ ] Il y a suffisamment d'examinateurs pour couvrir le nombre de candidats par discipline

> **Règle de dimensionnement :** pour une discipline avec N candidats, il faut au minimum ⌈N / créneaux_par_examinateur⌉ examinateurs. Avec une journée de 8h et des créneaux de 20 min d'oral, un examinateur peut recevoir environ 12 à 15 candidats.

### Étape 3 — Uploader via l'interface web

**Méthode ODS (recommandée) :**

1. Se connecter en admin : `/login`
2. Aller sur `/gestion/algo`
3. Cliquer **Télécharger le modèle ODS** pour obtenir le fichier pré-rempli
4. Remplir les feuilles `candidats` et `examinateurs` (les listes déroulantes guident la saisie des disciplines et des établissements)
5. Dans la section **Import via fichier ODS**, sélectionner le fichier et cliquer **Envoyer**
6. Le rapport de validation s'affiche immédiatement

**Méthode CSV individuelle (alternative) :**

1. Dans la section **Import CSV individuel**, sélectionner un ou plusieurs fichiers `.csv`
2. Cliquer **Envoyer les fichiers sélectionnés**
3. Vérifier les badges de présence (vert = présent)

### Étape 4 — Lancer l'algorithme

1. Cliquer **Lancer algo.py** sur la page `/gestion/algo`
2. La sortie s'affiche en temps réel dans la console
3. Surveiller les messages d'erreur :
   - `Discipline '...' introuvable` → discipline mal orthographiée dans un CSV
   - `Pas de créneau trouvé` → pas assez d'examinateurs pour une discipline
   - `Aucun placement valide trouvé` → contraintes incompatibles, revoir les données
4. En cas de succès, le message `=== Terminé — succès ✔ ===` s'affiche

**Durée estimée :** 2 à 10 minutes selon le nombre de candidats et les ressources serveur.

**Affichage détaillé (debug) :** dans les paramètres avancés, l'option **Affichage détaillé (debug)** permet d'afficher dans la console le détail interne de chaque run (chargement des données, appairage, calcul des horaires...), en plus du lancement et de la fin de chaque run affichés par défaut. Très verbeux — à activer uniquement pour diagnostiquer un problème.

### Étape 5 — Vérifier le résultat

Après un lancement réussi :

1. Aller sur `/gestion` → **Liste des oraux** pour vérifier le planning global
2. Aller sur `/salle` → vérifier chaque salle (nombre d'oraux, horaires)
3. Vérifier qu'aucun candidat n'a deux oraux trop proches
4. Si besoin, modifier manuellement un oral via `/gestion/edit-oral`

> **Validation lors de la modification d'un oral**
>
> L'interface `/gestion/edit-oral` effectue trois vérifications automatiques lors de la soumission :
>
> - **Chevauchement candidat (bloquant)** : le nouvel horaire `[heure_sujet, heure_fin]` ne peut pas chevaucher un autre oral du même candidat.
> - **Écart minimum candidat (avertissement)** : si l'écart entre `heure_sujet` des deux oraux du candidat est inférieur à `ecart_mini` (défaut 80 min), un avertissement s'affiche. L'admin peut forcer la validation en cliquant « Valider quand même ».
> - **Chevauchement examinateur (bloquant)** : l'intervalle `[heure_oral, heure_fin]` (oral seul, sans la préparation qui se déroule en loge) ne peut pas chevaucher un autre oral du même examinateur.

### Étape 6 — Générer et télécharger les documents

Sur la page `/gestion/documents` (accueil admin → bloc **Préparation** → Documents) :

| Document | Action | À faire |
|---|---|---|
| Papillons examinateurs | Télécharger | Distribuer aux examinateurs avant le jour J |
| Papillons candidats | Générer + télécharger | Distribuer aux candidats avant le jour J |
| Papillons loges | Télécharger | Distribuer aux surveillants de loge |
| Fiches salles (lot) | Générer + télécharger | Afficher ou distribuer aux examinateurs |
| Fiches loges (lot) | Générer + télécharger | Remettre aux surveillants de loge |
| Liste générale | Générer + télécharger | Affichage public ou usage interne |

> À la fin d'un run réussi, la page `/gestion/algo` affiche deux raccourcis : **→ Vérifier les oraux** et **→ Documents** pour enchaîner directement sur l'étape suivante.

---

## 4. Corrections après le lancement

### Modifier un oral

1. `/gestion` → cliquer sur l'oral à modifier
2. Changer l'horaire, l'examinateur ou cocher **"Marquer comme modifié"**
3. Si "Marquer comme modifié" est coché, les pages des candidats concernés s'actualisent en temps réel (SSE)

### Forcer le rechargement de toutes les pages

Dans la barre latérale admin (icône ↺, présente sur toutes les pages de gestion), cliquer **Recharger les pages** : tous les navigateurs connectés rechargent instantanément.

### Ajouter ou modifier un examinateur

`/gestion/liste-examinateurs` → modifier ou ajouter. Après ajout, un papillon PDF est automatiquement généré avec le nouveau mot de passe.

### Renouvellement des identifiants

`/gestion/credentials` (accessible depuis l'accueil admin → bloc **Préparation** → **Identifiants**) permet de **renouveler les identifiants sans relancer l'algo**, pour chaque catégorie indépendamment :

| Catégorie | Granularité | Effet |
|---|---|---|
| **Candidats** | Un ou tous | Génère un nouveau `login_key` + hash en DB + regénère le fichier de lot papillons_candidats.pdf |
| **Examinateurs** | Un ou tous | Génère un nouveau mot de passe + hash en DB + regénère le papillon PDF |
| **Loges** | Une ou toutes | Génère un nouveau mot de passe + hash en DB + regénère le papillon PDF |

**Stockage sécurisé :** les mots de passe en clair des examinateurs et des loges sont chiffrés (AES-256-GCM) dans `data/credentials.enc`. Ce store est initialisé automatiquement à la fin de chaque lancement de l'algorithme, et mis à jour à chaque renouvellement.

> **Cas d'usage :** un examinateur perd son papillon → aller sur `/gestion/credentials`, cliquer "Renouveler" sur la ligne de cet examinateur → un nouveau papillon PDF est regénéré et disponible au téléchargement.

**Renouvellement individuel depuis les listes :** `/gestion/liste-candidats` et `/gestion/liste-examinateurs` disposent chacune d'une colonne **Identifiants** avec un bouton "↺ Renouveler" par ligne. C'est un raccourci vers la même action que ci-dessus, sans quitter la liste : l'admin reste sur la page après renouvellement, avec un message en haut de page pointant vers le fichier de lot regénéré (même bandeau que sur `/gestion/credentials`).

---

## 5. Jour des épreuves

### Accès des différents acteurs

| Acteur | Comment se connecter | Identifiants |
|---|---|---|
| Candidat | `https://[site]/login-candidat` | INE + mot de passe du papillon |
| Examinateur | `https://[site]/login-examinateur` | Numéro de salle + mot de passe du papillon |
| Surveillant de loge | `https://[site]/login-loge` | Nom de la loge + mot de passe du papillon |
| Admin | `https://[site]/login` | Code TOTP (application type Aegis) |

### Émargement

- L'examinateur clique **Émarger** sur sa fiche salle
- Il signe sur l'écran ou génère un QR code pour signer depuis un autre appareil (tablette, téléphone)
- La signature est horodatée et enregistrée en base
- L'admin peut également émarger depuis n'importe quelle fiche salle

### Suivi en temps réel

- La page `/liste` (grand écran) s'actualise automatiquement à chaque modification
- Les candidats voient leur fiche mise à jour en temps réel depuis leur téléphone

---

## 6. Résolution des problèmes courants

| Symptôme | Cause probable | Solution |
|---|---|---|
| `Discipline '...' introuvable` | Orthographe différente entre CSV | Corriger le nom dans `candidats.csv` ou `examinateurs.csv` pour qu'il corresponde au nom complet ou à l'abréviation de `preps.csv` |
| `Pas de créneau trouvé. Abandon` | Trop peu d'examinateurs pour une discipline | Ajouter des examinateurs dans `examinateurs.csv` |
| `Aucun placement valide trouvé` | Contraintes incompatibles (anti-conflit établissement trop restrictif, horaires trop contraints) | Réduire les contraintes `Etab` ou élargir les `Heure mini` |
| Algo très long (> 15 min) | Données volumineuses ou beaucoup de contraintes | Normal ; attendre. Réduire `N_run` dans `algo.py` si nécessaire (défaut : 1000) |
| Candidat sans oral | Discipline non couverte par un examinateur | Vérifier que la discipline du candidat a au moins un examinateur dans `examinateurs.csv` |
| Mot de passe de papillon invalide | Identifiants renouvelés manuellement via `/gestion/credentials` | Redistribuer les nouveaux papillons générés lors du renouvellement |
