# 2ndOral

Application web de gestion des oraux de second groupe du baccalauréat :
placement automatique des candidats et examinateurs, consultation en temps réel,
émargement dématérialisé, génération de documents PDF.

---

## Sommaire

1. [Architecture](#architecture)
2. [Niveaux d'accès et RGPD](#niveaux-daccès-et-rgpd)
3. [Déploiement rapide (Docker)](#déploiement-rapide-docker)
4. [Configuration d'un nouveau site](#configuration-dun-nouveau-site)
5. [Workflow annuel](#workflow-annuel)
6. [Référence des scripts](#référence-des-scripts)
7. [Sécurité](#sécurité)
8. [Structure des fichiers](#structure-des-fichiers)
9. [Dépendances](#dépendances)

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
| **Candidat** | `session['candidat'] = '<ine>'` | ✅ sa fiche uniquement | ❌ | ❌ | ❌ |
| **Anonyme** | — | ❌ → login | ❌ → login | ❌ → login | ❌ → login |

**Pages de connexion :**

| Rôle | URL | Mécanisme |
|---|---|---|
| Admin | `/login` | Code TOTP 6 chiffres (toutes les 30 s) |
| Examinateur | `/login-examinateur` | Mot de passe par salle |
| Surveillant de loge | `/login-loge` | Mot de passe par loge |
| Candidat | `/login-candidat` | Numéro INE + mot de passe du papillon |

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
    --no-digital-sign   # optionnel : désactive l'émargement en ligne
```

**Ce que fait le script (dans l'ordre) :**

1. **`webserver/app_secrets.py`** — génère tous les secrets (clé OTP, `APP_SECRET_KEY`, `DB_SALT`, pepper/sel)
2. **QR code TOTP** — affiché dans le terminal + sauvegardé en PNG (`otp_setup.png`) + vérification interactive du code
3. **`.env` Docker** — généré automatiquement avec `DB_ROOT_PASSWORD` aléatoire et credentials cohérents avec `app_secrets.py`
4. **Config nginx** — écrite dans `nginx-conf/<fqdn>` et installée dans `/etc/nginx/sites-available/`
5. **Certbot** — `certbot --nginx -d <fqdn>` (Let's Encrypt)
6. **Docker** *(optionnel, demande confirmation)* — `docker compose build`, démarrage MariaDB + Redis, attente de disponibilité, création de la base et de l'utilisateur, démarrage de la stack complète
7. **algo.py** *(optionnel)* — proposé si les fichiers CSV sont disponibles

> **Fichiers protégés** : `app_secrets.py` (`chmod 640`, root) et `.env` (`chmod 600`) ne doivent jamais être versionnés.

---

## Workflow annuel

> 📖 **Documentation complète** : [docs/workflow_admin.md](docs/workflow_admin.md)
> — format détaillé des CSV, contraintes de l'algorithme, résolution des problèmes.

### Préparer les fichiers CSV

Trois fichiers sont nécessaires dans `data/` :

| Fichier | Colonnes |
|---|---|
| `candidats.csv` | `CANDIDAT` (Nom Prénom (INE)) ; `CHOIX DISCIPLINE 1` ; `CHOIX DISCIPLINE 2` ; `TT` (0/1) ; `Etab` ; `Profs` |
| `profs_total.csv` | `Nom` ; `Disc.poste` ; `Salle` ; `Heure mini` ; `Etab` ; `Loge` |
| `preps.csv` | `Matiere` ; `Matière court` ; `Temps preparation (min)` ; `Duree (min)` |

**Modèles téléchargeables** depuis l'interface admin → `/gestion/algo`,
ou directement dans `webserver/static/templates_csv/`.

Les fichiers peuvent être uploadés directement depuis la page `/gestion/algo`.

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
| `papillons_candidats.pdf` | INE + mot de passe + QR code | ✅ via `/gestion/algo` |
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

---

## Référence des scripts

| Script | Usage | Notes |
|---|---|---|
| `run_algo.sh` | `./run_algo.sh` | Lance algo.py dans Docker, gère le stop/start de l'app |
| `run_algo.sh --dry-run` | Affiche les commandes sans les exécuter | |
| `setup_new_site.py` | `sudo python setup_new_site.py` | Configuration complète : secrets, QR TOTP, .env, nginx, certbot, Docker, DB |
| `verify_logs.py` | `python verify_logs.py` | Vérifie l'intégrité de la chaîne de hash des logs |
| `verify_logs` | `./verify_logs` | Wrapper shell pour `verify_logs.py` (avec venv) |
| `2fa.sh` | `./2fa.sh` | Re-teste la clé OTP existante (QR + vérification interactive) |
| `login_key_generator.py` | `python login_key_generator.py` | Génère une nouvelle clé OTP base32 |
| `update_python_packages` | `./update_python_packages` | Met à jour les paquets Python du venv hôte |

---

## Sécurité

| Mesure | Détail |
|---|---|
| **SQL** | Paramètres liés `%s` / `%(name)s` — aucune interpolation |
| **Mots de passe** | `scrypt(n=2048, r=8, p=2)` + pepper + sel, encodé base64 |
| **TOTP admin** | `pyotp`, fenêtre ±1 intervalle, rate limit 10 req/min |
| **CSRF** | Flask-WTF sur tous les formulaires |
| **CSP** | `default-src 'self'`, `script-src 'self' 'unsafe-inline'`, `form-action 'self'`, `base-uri 'self'` via Flask-Talisman — scripts externes bloqués, injection de base et de formulaire bloquées |
| **Sessions** | `HttpOnly`, `Secure`, `SameSite=Lax` (production) |
| **INE en session** | Stocké en Redis (TTL 5 min, token aléatoire) — jamais en clair dans le cookie |
| **Rate limiting** | Flask-Limiter + Redis ; 10 req/min sur les routes de login |
| **Open redirect** | Toutes les URLs `link_back` validées (même domaine) — encodage simple via `url_for` |
| **IDOR** | `/generate-doc-one` protégé — auth obligatoire (fiche candidat, salle, loge) |
| **Path traversal** | `/download` : regex `^[\w\-. ]+\.pdf$` + `send_from_directory` |
| **SSE** | Auth requise (`before_request`) + connexion Redis sans socket timeout (gevent) |
| **Noms examinateurs** | Dropdown `/login-examinateur` : numéro de salle uniquement, pas de noms |
| **Server header** | `server_tokens off` sur nginx hôte et Docker |
| **Permissions-Policy** | `camera`, `microphone`, `geolocation`, `payment`, `usb` désactivés |
| **IP client réelle** | nginx hôte remplace `X-Forwarded-For` par `$remote_addr` (anti-spoofing) ; nginx Docker le transmet intact ; `ProxyFix(x_for=1)` dans Flask |
| **Logs gunicorn** | Format `%({x-forwarded-for}i)s` — affiche l'IP réelle du client (pas l'IP Docker) |
| **Logs d'audit** | Chaîne de hash SHA-256 + sel côté DB (falsification détectable) |
| **Tokens signature** | Usage unique, expiration 5 min, canal `sign_<token>` dédié |

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
│
├── data/
│   ├── candidats.csv            Données candidats (séparateur ;)
│   ├── profs_total.csv          Données examinateurs
│   └── preps.csv                Matières et durées
│
├── docs/
│   └── workflow_admin.md        Guide administrateur : CSV, algo, jour J
│
├── docker-compose.yml           Stack : app, nginx, redis, mariadb
├── Dockerfile                   Image runtime (Python + pdftk, sans code source)
├── .env.example                 Template des variables Docker à copier en .env
├── .dockerignore                Exclut tout sauf requirements.txt du contexte
│
├── nginx-conf/                  Configs nginx générées par setup_new_site.py
│
└── webserver/
    ├── app.py                   Application Flask principale
    ├── app_secrets.py           Secrets (root:root 640, non versionné)
    ├── algo_bg.py               Exécution de algo.py en tâche de fond (SSE)
    ├── db_facility_web.py       Requêtes SQL paramétrées
    ├── reports.py               Génération PDF (ReportLab + pypdftk)
    ├── flask_sse.py             Blueprint SSE avec cache Redis
    ├── patched_app.py           Point d'entrée gunicorn (gevent monkey-patch)
    │
    ├── static/
    │   ├── templates_csv/       Modèles CSV téléchargeables depuis /gestion/algo
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
        ├── gestion_algo.html    Upload CSV + lancement algo + log temps réel
        └── ...
```

---

## Dépendances

### Système

- **Docker** + **Docker Compose v2**
- **nginx** (hôte, pour le SSL)
- **certbot** + `python3-certbot-nginx` (pour Let's Encrypt)

### Python (dans `requirements.txt`)

```
Flask, Flask-SSE, Flask-WTF, Flask-Compress, Flask-Limiter, Flask-Talisman
gunicorn, gevent
mysql-connector-python
redis
pyotp, segno
reportlab, pypdftk, pillow
pytz, colorama, setuptools
```

### Checklist déploiement initial

- [ ] `sudo python setup_new_site.py` exécuté jusqu'à la fin
- [ ] Clé OTP configurée dans l'application TOTP (scannée pendant le script)
- [ ] nginx hôte rechargé (`sudo nginx -s reload`)
- [ ] Fichiers CSV déposés dans `data/` et `./run_algo.sh` lancé (si pas fait pendant le script)
- [ ] PDFs papillons imprimés et distribués avant le jour des épreuves
