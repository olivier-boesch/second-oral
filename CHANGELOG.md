# CHANGELOG

## [2026.2] — 2026-07-01

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
