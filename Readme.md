# 2ndOral

[![CI](https://github.com/olivier-boesch/second-oral/actions/workflows/ci.yml/badge.svg)](https://github.com/olivier-boesch/second-oral/actions/workflows/ci.yml)

Application web de gestion des oraux de second groupe du baccalauréat :
placement automatique des candidats et examinateurs, consultation en temps réel,
émargement dématérialisé, génération de documents PDF.

## En production

Déployé depuis 2023 sur un centre d'examen de l'académie d'Aix-Marseille.
~180 candidats traités en une journée sur une instance.
Utilisé chaque année lors des oraux de second groupe du baccalauréat.

---

## Sommaire

1. [Architecture](#architecture)
2. [Niveaux d'accès et RGPD](#niveaux-daccès-et-rgpd)
3. [Déploiement rapide (Docker)](#déploiement-rapide-docker)
4. [Configuration d'un nouveau site](#configuration-dun-nouveau-site)
5. [Workflow annuel](#workflow-annuel)
6. [Référence des scripts](#référence-des-scripts)
7. [Sécurité](#sécurité)
8. [Tests et CI](#tests-et-ci)
9. [Structure des fichiers](#structure-des-fichiers)
10. [Dépendances](#dépendances)

---

## Architecture

```
Internet
  │  HTTPS 443 (Certbot / Let's Encrypt)
  ▼
nginx hôte  ──────────── /static/* servi depuis l'hôte
  │  HTTP → 127.0.0.1:8080
  ▼
nginx Docker  ──────────── /static/* servi en lecture seule
  │  HTTP → app:8000 (réseau Docker interne)
  ▼
gunicorn (gevent, 4 workers)  ←─ code monté en volume
  ├── MariaDB 11 (Docker)         Données persistantes
  └── Redis 7   (Docker)          SSE pub/sub + rate limiting
```

**Stack :** Python 3.12, Flask, MariaDB, Redis, gunicorn/gevent,
ReportLab (PDF), pypdftk (concat PDF), nginx (hôte + Docker), Let's Encrypt.

---

## Niveaux d'accès et RGPD

Toutes les pages contenant des données personnelles sont protégées.

| Rôle | Session | Fiche candidat | Fiche salle | Fiche loge | Liste générale |
|---|---|---|---|---|---|
| **Admin** (TOTP) | `session['user'] = 'admin'` | ✅ toutes | ✅ toutes | ✅ toutes | ✅ |
| **Examinateur** | `session['user'] = '<salle>'` | ✅ toutes | ✅ sa salle | ✅ sa loge | ✅ |
| **Loge** | `session['loge'] = '<nom>'` | ✅ toutes | ✅ salles de sa loge | ✅ sa loge | ✅ |
| **Candidat** | `session['candidat'] = '<numero>'` | ✅ sa fiche uniquement | ❌ | ❌ | ❌ |
| **Anonyme** | — | ❌ → login | ❌ → login | ❌ → login | ❌ → login |

**Pages de connexion :**

| Rôle | URL | Mécanisme |
|---|---|---|
| Admin | `/login` | Code TOTP 6 chiffres (toutes les 30 s) |
| Examinateur | `/login-examinateur` | Mot de passe par salle |
| Surveillant de loge | `/login-loge` | Mot de passe par loge |
| Candidat | `/login-candidat` | Numéro de candidat + mot de passe du papillon |

---

## Déploiement rapide (Docker)

### Prérequis

- Docker + Docker Compose v2
- nginx installé sur l'hôte (SSL via Certbot)
- DNS du domaine pointant sur le serveur

### Mise en service

```bash
# 1. Cloner le dépôt
git clone ... && cd second_oral

# 2. Tout-en-un : secrets, TOTP, .env, nginx, certbot, Docker, DB
sudo python setup_new_site.py
# Le script demande confirmation avant chaque étape Docker.
# À la fin il propose de lancer algo.py si les CSV sont prêts.

# 3. Recharger nginx hôte (si certbot ne l'a pas fait)
sudo nginx -t && sudo nginx -s reload
```

> **`setup_new_site.py` génère le `.env` automatiquement** avec des credentials
> cohérents avec `app_secrets.py` — pas besoin de le créer manuellement.

### Commandes courantes

```bash
docker compose up -d          # Démarrer
docker compose stop           # Arrêter
docker compose logs -f app    # Logs de l'application
docker compose restart app    # Appliquer un changement de code (sans rebuild)
docker compose restart nginx  # Appliquer un changement de config nginx Docker
docker compose build app      # Rebuild si requirements.txt a changé
sudo nginx -s reload          # Appliquer un changement de config nginx hôte
```

---

## Configuration d'un nouveau site

`setup_new_site.py` configure une instance complète en une commande.
Il doit être lancé avec `sudo` car il crée des fichiers appartenant à root.

```bash
# Mode interactif (questions posées une par une)
sudo python setup_new_site.py

# Mode batch (pour l'automatisation)
sudo python setup_new_site.py \
    --subdomain stex \
    --domain mesoraux.fr \
    --name "Lycée Saint Exupéry - Marseille" \
    --db-user secondoral \
    --db-password MOT_DE_PASSE \
    --certbot-email admin@mesoraux.fr \
    --director-name "Jean Dupont" \
    --centre-address "13 avenue du Lycée, 13001 Marseille" \
    --academie "Académie d'Aix-Marseille" \
    --hebergeur "OVHcloud SAS, 2 rue Kellermann, 59100 Roubaix" \
    --dpd-email "dpd@ac-aix-marseille.fr" \
    --no-digital-sign   # optionnel : désactive l'émargement en ligne
```

**Ce que fait le script (dans l'ordre) :**

1. **`webserver/app_secrets.py`** — génère tous les secrets (clé OTP, `APP_SECRET_KEY`, `DB_SALT`, pepper/sel) + infos légales (directeur de publication, adresse, académie, DPD)
2. **QR code TOTP** — affiché dans le terminal + sauvegardé en PNG (`otp_setup.png`) + vérification interactive du code
3. **PDF administrateur** — clé TOTP + démarches légales RGPD à effectuer par le chef de centre
4. **`.env` Docker** — généré automatiquement avec `DB_ROOT_PASSWORD` aléatoire et credentials cohérents avec `app_secrets.py`
5. **Config nginx** — écrite dans `nginx-conf/<fqdn>` et installée dans `/etc/nginx/sites-available/` (TLS 1.2+, HTTP→HTTPS, HSTS)
6. **Certbot** — `certbot --nginx -d <fqdn>` (Let's Encrypt)
7. **Docker** *(optionnel, demande confirmation)* — `docker compose build`, démarrage MariaDB + Redis, attente de disponibilité, création de la base et de l'utilisateur avec privilèges limités, démarrage de la stack complète
8. **algo.py** *(optionnel)* — proposé si les fichiers CSV sont disponibles

> **Fichiers protégés** : `app_secrets.py` (`chmod 640`, root) et `.env` (`chmod 600`) ne doivent jamais être versionnés.

---

## Workflow annuel

> 📖 **Documentation complète** : [docs/workflow_admin.md](docs/workflow_admin.md)
> — format détaillé des CSV, contraintes de l'algorithme, résolution des problèmes.

### Préparer les fichiers

Trois fichiers CSV sont nécessaires dans `data/` :

| Fichier | Colonnes |
|---|---|
| `candidats.csv` | `CANDIDAT` (Nom Prénom (numéro)) ; `CHOIX DISCIPLINE 1` ; `CHOIX DISCIPLINE 2` ; `TT` (0/1) ; `Etab` ; `Profs` |
| `examinateurs.csv` | `Nom` ; `Disc.poste` ; `Salle` ; `Heure mini` ; `Etab` ; `Loge` |
| `preps.csv` | `Matiere` ; `Matière court` ; `Temps preparation (min)` ; `Duree (min)` |

**Méthode recommandée — fichier ODS unique :**
depuis `/gestion/algo`, téléchargez le modèle ODS (feuille `preps` pré-remplie avec les 16 disciplines, listes déroulantes de validation sur `examinateurs` et `candidats`), remplissez-le sous LibreOffice Calc ou Excel, puis uploadez-le — le serveur le découpe automatiquement en 3 CSV.

**Méthode alternative — CSV individuels :**
modèles téléchargeables dans `webserver/static/templates_csv/` ou depuis `/gestion/algo`.

### Lancer l'algorithme

```bash
# Via le script shell (recommandé)
./run_algo.sh

# Ou depuis l'interface web admin
# → /gestion/algo → bouton "Lancer algo.py"
# La sortie s'affiche en temps réel dans le navigateur.
```

`run_algo.sh` arrête le conteneur `app`, attend que MariaDB soit prête,
lance `algo.py` dans Docker, puis redémarre l'app.

**Résultats produits dans `webserver/static/docs/` :**

| Fichier | Contenu | Regénérable depuis le web |
|---|---|---|
| `papillons_examinateurs.pdf` | Salle + nom + mot de passe | ❌ (hash uniquement en base) |
| `papillons_candidats.pdf` | N° candidat + mot de passe + QR code | ✅ via `/gestion/algo` |
| `papillons_loges.pdf` | Loge + mot de passe | ❌ (hash uniquement en base) |

### Télécharger les documents depuis l'interface web

La page `/gestion/algo` propose, en plus du lancement :

| Document | Action | Remarque |
|---|---|---|
| Papillons candidats | Générer + télécharger | Depuis la base courante |
| Papillons examinateurs | Télécharger | Produit par algo.py uniquement |
| Papillons loges | Télécharger | Produit par algo.py uniquement |
| Fiches candidats (lot) | Générer + télécharger | Toutes les fiches en un seul PDF |
| Fiches salles (lot) | Générer + télécharger | Toutes les fiches d'émargement |
| Fiches loges (lot) | Générer + télécharger | Toutes les fiches de loge |
| Liste générale des oraux | Générer + télécharger | Tous candidats/matières/salles |

Les boutons **Générer** recalculent le PDF depuis la base de données courante.
Les boutons **Télécharger** servent le dernier fichier produit par algo.py.

### Distribuer les papillons

- Imprimer et distribuer **avant** le jour des épreuves
- Chaque papillon contient : identifiant + mot de passe + QR code vers la page personnelle
- Les identifiants de connexion apparaissent aussi sur la fiche web du candidat
  (visible par l'admin et le candidat lui-même une fois connecté)

### Archiver les données de fin de session

Depuis l'accueil admin → **« Archiver la session (zip) »** (`/gestion/archive`),
une page de confirmation détaille le contenu avant de proposer le téléchargement
d'une archive zip regroupant les données à conserver, conformément au principe
de minimisation RGPD :

| Fichier | Contenu |
|---|---|
| `planning_oraux.csv` | Planning final des oraux (candidat, matière, salle, examinateur, horaires) |
| `emargements.csv` | Preuves de signature des examinateurs (métadonnées seulement, sans les images) |
| `journal_audit.json` | Journal d'audit chaîné par hash (intégrité vérifiable, cf. `/gestion/verify-logs`) |
| `documents/` | Fiches d'émargement de **toutes** les salles, régénérées à la volée (jamais celles déjà présentes dans `static/docs`, potentiellement incomplètes) — embarquent les images de signature des candidats, seule preuve d'émargement à conserver |
| `LISEZMOI.txt` | Manifeste : contenu de l'archive, date et auteur de la génération |

**Volontairement exclus** de l'archive : mots de passe, clés de connexion,
fichiers CSV bruts d'inscription (`data/candidats.csv`, `examinateurs.csv`, `preps.csv`),
et les autres PDF générables à la demande (papillons, fiches candidats/loges, liste générale des oraux).

> **RGPD — fin de session :** les PDF générés à la demande persistent dans `static/docs/` jusqu'à
> redémarrage du conteneur. Après archivage, supprimer ce dossier ou relancer Docker pour le vider.

### Monitoring (`/gestion/monitoring`)

Tableau de bord admin-only (accès : accueil admin → « Monitoring »). Affiche en temps réel :

- **Requêtes** : total, répartition succès / redirections / erreurs client / erreurs serveur, activité des 24 dernières heures (histogramme heure par heure)
- **Sessions actives** : type (admin / examinateur / candidat / loge), identifiant, IP de connexion
- **Échecs d'authentification** : IPs ayant échoué dans les 5 dernières minutes

Toutes les données de monitoring sont stockées exclusivement en Redis (TTL 8 h pour les sessions, TTL 49 h pour les compteurs horaires) — aucune écriture en base ni sur disque. La page se rafraîchit automatiquement toutes les 10 secondes.

> **RGPD :** les adresses IP des sessions actives sont des données personnelles (CNIL). Elles sont
> traitées sur la base de l'intérêt légitime (art. 6.1.f RGPD) à des fins de diagnostic et de
> détection d'anomalies, accessibles au seul admin (responsable du traitement), et supprimées
> automatiquement après 8 h. Aucun croisement avec d'autres données n'est effectué.

### Minuteurs de loge

La page de loge affiche un minuteur par candidat, pré-réglé sur la durée de préparation de la matière (tiers-temps inclus dans le calcul des horaires). Chaque minuteur peut être démarré, mis en pause et réinitialisé individuellement. L'état est stocké en Redis (TTL 24 h) et survit aux rechargements de page.

- **Bip d'avertissement** à 60 secondes restantes
- **Signal sonore de fin** à 0 (3 bips)
- Le son nécessite une première interaction utilisateur sur la page (restriction navigateur)

---

## Référence des scripts

| Script | Usage | Notes |
|---|---|---|
| `run_algo.sh` | `./run_algo.sh` | Lance algo.py dans Docker, gère le stop/start de l'app |
| `run_algo.sh --dry-run` | Affiche les commandes sans les exécuter | |
| `setup_new_site.py` | `sudo python setup_new_site.py` | Configuration complète : secrets, QR TOTP, .env, nginx, certbot, Docker, DB |
| `verify_logs.py` | `python verify_logs.py` | Vérifie l'intégrité de la chaîne de hash des logs |
| `verify_logs` | `./verify_logs` | Wrapper shell — exécute `verify_logs.py` dans le conteneur `app` (`docker compose exec`) |
| `2fa.sh` | `./2fa.sh` | Re-teste la clé OTP existante (QR + vérification interactive) |
| `login_key_generator.py` | `python login_key_generator.py` | Génère une nouvelle clé OTP base32 |
| `update_python_packages` | `./update_python_packages` | Met à jour les paquets Python du venv hôte |

---

## Sécurité

| Mesure | Détail |
|---|---|
| **SQL** | Paramètres liés `%s` / `%(name)s` — aucune interpolation |
| **Mots de passe** | `scrypt(n=2**15, r=8, p=2)` + pepper + sel dérivé par compte (numéro candidat / salle / loge), encodé base64 ; comparaison via `hmac.compare_digest` (protection timing attack) |
| **TOTP admin** | `pyotp`, fenêtre ±1 intervalle, rate limit 10 req/min |
| **CSRF** | `CSRFProtect` (Flask-WTF) enregistré globalement — vérifie le jeton sur toute requête mutante (POST/PUT/PATCH/DELETE) |
| **CSP** | `default-src 'self'`, nonce par requête + `'strict-dynamic'` sur `script-src`, `form-action 'self'`, `base-uri 'self'` via Flask-Talisman |
| **Sessions** | `HttpOnly`, `Secure`, `SameSite=Lax` ; expiration 8 h ; `session.clear()` avant chaque login (protection fixation de session) |
| **N° candidat en session** | Stocké en Redis (TTL 5 min, token aléatoire) — jamais en clair dans le cookie |
| **Rate limiting** | Flask-Limiter + Redis ; 10 req/min sur toutes les routes de connexion (`login`, `login-examinateur`, `login-candidat`, `login-loge`) ; 30 connexions/min sur le flux SSE (`/stream`) — limite dédiée (et non une exemption totale) pour ne pas pénaliser les reconnexions EventSource légitimes tout en empêchant un compte d'ouvrir un flot de connexions en boucle (chacune retient indéfiniment un greenlet + une souscription Redis) |
| **Alerting auth** | Compteur d'échecs par IP (fenêtre glissante de 5 min, purgée des entrées obsolètes) ; `WARNING` gunicorn après 5 tentatives — sans journaliser le mot de passe ni le code OTP soumis |
| **HSTS** | `max-age=31536000; includeSubDomains; preload` côté Talisman et nginx |
| **TLS** | TLS 1.2+ uniquement ; suites ECDHE/DHE-GCM/CHACHA20 ; `ssl_session_tickets off` ; redirection HTTP→HTTPS |
| **Headers** | `Referrer-Policy: strict-origin-when-cross-origin`, `Cross-Origin-Opener-Policy: same-origin`, `X-Content-Type-Options: nosniff` |
| **Permissions-Policy** | `camera`, `microphone`, `geolocation`, `payment`, `usb` désactivés |
| **Open redirect** | Toutes les URLs `link_back` validées : schéma limité à `http`/`https`/vide (les URI `javascript:`, `data:`, `vbscript:` sont rejetées) + même domaine obligatoire |
| **IDOR** | `/generate-doc-one` protégé — auth obligatoire (fiche candidat, salle, loge) ; `/download?filename=candidat_*` requiert une session active (personnel ou candidat) ; canaux SSE soumis à autorisation par session (cf. ligne SSE) |
| **Path traversal** | `/download` : regex `^[\w\-. ]+\.pdf$` + `send_from_directory` |
| **Logs d'audit** | Les triggers DB n'enregistrent pas `password_hash` dans `journal_audit.json` — les hashs scrypt des examinateurs/loges ne transitent pas dans l'archive RGPD |
| **SSE** | Auth requise (`before_request`) **et** autorisation par canal (`_sse_channel_allowed` — un candidat/loge/examinateur ne peut s'abonner qu'à ses propres canaux ; `general` ne diffuse aucune donnée personnelle) + connexion Redis sans socket timeout (gevent) |
| **Actions mutantes** | `delete-examinateur` / `reload-pages` exposées uniquement en POST (+ CSRF) — jamais déclenchables par un simple lien GET |
| **Tokens en logs** | Tokens de signature tronqués (`_redact_token`) avant journalisation — jamais en clair (fenêtre de validité de 5 min) |
| **Noms examinateurs** | Dropdown `/login-examinateur` : numéro de salle uniquement, pas de noms |
| **Server header** | `server_tokens off` sur nginx hôte et Docker |
| **IP client réelle** | nginx hôte remplace `X-Forwarded-For` par `$remote_addr` (anti-spoofing) ; nginx Docker le transmet intact ; `ProxyFix(x_for=1)` dans Flask |
| **IPs de session (monitoring)** | Adresse IP stockée en Redis à chaque login (TTL 8 h, supprimée au logout) — visible uniquement par l'admin dans `/gestion/monitoring` à des fins de diagnostic (intérêt légitime art. 6.1.f RGPD) ; aucune écriture en base ni sur disque |
| **Logs gunicorn** | Format `%({x-forwarded-for}i)s` — affiche l'IP réelle du client (pas l'IP Docker) |
| **Logs d'audit** | Chaîne de hash SHA-256 + sel côté DB (falsification détectable) |
| **Tokens signature** | Usage unique, expiration 5 min, canal `sign_<token>` dédié |
| **DB privilèges** | Compte applicatif limité à `SELECT, INSERT, UPDATE, DELETE` — pas de `DROP`/`ALTER` |
| **Docker** | Conteneur exécuté en tant qu'utilisateur non-root `appuser` (UID 1000) |
| **Dépendances** | Versions épinglées dans `requirements.txt` ; `pip-audit` exécuté à chaque CI |
| **security.txt** | `/.well-known/security.txt` (RFC 9116) — contact de divulgation responsable |

---

## Tests et CI

```bash
# Lancer toute la suite de tests
python -m pytest tests/ -v

# Tests unitaires uniquement (pas de dépendances Flask)
python -m pytest tests/unit/

# Tests d'intégration Flask (DB mockée, sans MariaDB réel)
python -m pytest tests/integration/
```

Le pipeline GitHub Actions (`.github/workflows/ci.yml`) exécute à chaque push sur `main` :
1. **`pip-audit`** — détection des CVE connues dans les dépendances
2. **Tests** — 217 tests unitaires + intégration avec couverture (dont vérification
   automatisée des annotations de type, du PEP8 et de mypy sur `app.py`, et
   non-régression des constats de l'audit de sécurité)

---

## Structure des fichiers

```
second_oral/
│
├── algo.py                      Algorithme de placement (multiprocessing)
├── db_facility_save.py          Création du schéma DB + insertion initiale
├── reports.py                   Façade : réexporte depuis webserver/reports.py
├── setup_new_site.py            Configuration d'un nouveau déploiement (sudo)
├── run_algo.sh                  Lance algo.py dans Docker
├── verify_logs.py               Vérification standalone de l'intégrité des logs
├── verify_logs                  Wrapper shell
├── login_key_generator.py       Génère une clé OTP base32
├── 2fa.sh                       Teste la configuration OTP (QR + vérification)
├── update_python_packages       Met à jour le venv hôte
├── requirements.txt             Dépendances Python (versions épinglées)
├── requirements-test.txt        Dépendances de test uniquement
├── pytest.ini                   Configuration pytest
│
├── data/
│   ├── candidats.csv            Données candidats (séparateur ;)
│   ├── examinateurs.csv         Données examinateurs
│   ├── preps.csv                Matières et durées
│   └── algo_params.json         Paramètres algo (généré par l'interface web)
│
├── docs/
│   └── workflow_admin.md        Guide administrateur : CSV, algo, jour J
│
├── tests/
│   ├── unit/
│   │   ├── test_code_quality.py    Annotations, PEP8 et mypy sur app.py
│   │   ├── test_csv_validator.py   Tests unitaires du validateur CSV
│   │   ├── test_ods_handler.py     Tests unitaires du lecteur/générateur ODS
│   │   └── test_setup_utils.py     Tests unitaires des utilitaires de setup
│   └── integration/
│       ├── test_flask_routes.py    Tests d'intégration Flask (DB mockée)
│       └── test_security.py        Non-régression des constats de l'audit sécurité
│
├── .github/workflows/ci.yml     Pipeline CI : pip-audit + tests
├── docker-compose.yml           Stack : app, nginx, redis, mariadb
├── Dockerfile                   Image runtime (Python + pdftk, non-root)
├── .env.example                 Template des variables Docker à copier en .env
├── .dockerignore                Exclut tout sauf requirements.txt du contexte
│
├── nginx-conf/                  Configs nginx générées par setup_new_site.py
│
└── webserver/
    ├── app.py                   Application Flask principale
    ├── app_secrets.py           Secrets (root:root 640, non versionné)
    ├── algo_bg.py               Exécution de algo.py en tâche de fond (SSE)
    ├── csv_validator.py         Validation et normalisation des fichiers CSV
    ├── ods_handler.py           Lecture et génération ODS 4 feuilles (odfpy) + 249 lycées Aix-Marseille
    ├── db_facility_web.py       Requêtes SQL paramétrées
    ├── reports.py               Génération PDF (ReportLab + pypdftk)
    ├── flask_sse.py             Blueprint SSE avec cache Redis
    ├── patched_app.py           Point d'entrée gunicorn (gevent monkey-patch)
    │
    ├── static/
    │   ├── .well-known/
    │   │   └── security.txt     Contact de divulgation responsable (RFC 9116)
    │   ├── templates_csv/       Modèles CSV individuels (examinateurs, candidats, preps)
    │   ├── docs/                PDFs générés (volume Docker nommé)
    │   └── ...                  CSS, images, JS
    │
    └── templates/
        ├── login.html           Login admin (TOTP)
        ├── login_examinateur.html
        ├── login_loge.html
        ├── login_candidat.html
        ├── candidat.html        Fiche candidat (avec identifiants si autorisé)
        ├── salle.html           Fiche salle + émargement
        ├── loge.html            Fiche loge
        ├── liste.html           Liste générale (affichage grand écran)
        ├── sign.html            Signature dématérialisée
        ├── mentions_legales.html Mentions légales + politique de confidentialité
        ├── gestion_algo.html    Upload ODS/CSV + paramètres algo + lancement + log
        └── ...
```

---

## Dépendances

### Système

- **Docker** + **Docker Compose v2**
- **nginx** (hôte, pour le SSL)
- **certbot** + `python3-certbot-nginx` (pour Let's Encrypt)

### Python (dans `requirements.txt`, versions épinglées)

```
Flask==3.1.3, Flask-SSE==1.0.0, Flask-WTF==1.3.0, Flask-Compress==1.24
Flask-Limiter==4.1.1, Flask-Talisman==1.1.0
gunicorn==23.0.0, gevent==25.5.1
mysql-connector-python==9.7.0
redis==8.0.0
pyotp==2.9.0, segno==1.6.6
reportlab==4.5.1, pypdftk==0.5, pillow==12.2.0
odfpy>=1.4.0
pytz==2026.2, colorama==0.4.6, setuptools==80.9.0
```

### Checklist déploiement initial

- [ ] `sudo python setup_new_site.py` exécuté jusqu'à la fin (avec infos légales)
- [ ] Clé OTP configurée dans l'application TOTP (scannée pendant le script)
- [ ] PDF administrateur imprimé et fichier numérique supprimé
- [ ] nginx hôte rechargé (`sudo nginx -s reload`)
- [ ] DPD de l'académie informé du traitement (voir section RGPD du PDF admin)
- [ ] Fichiers CSV déposés dans `data/` et `./run_algo.sh` lancé (si pas fait pendant le script)
- [ ] PDFs papillons imprimés et distribués avant le jour des épreuves
