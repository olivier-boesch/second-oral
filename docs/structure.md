# Structure des fichiers et dépendances — 2ndOral

## Arborescence

```
second_oral/
│
├── algo.py                      Algorithme de placement (multiprocessing)
├── db_facility_save.py          Création du schéma DB + insertion initiale
├── setup_new_site.py            Configuration d'un nouveau déploiement (sudo)
├── run_algo.sh                  Lance algo.py dans Docker
├── requirements.txt             Dépendances Python (versions épinglées)
├── requirements-test.txt        Dépendances de test uniquement
├── pytest.ini                   Configuration pytest
│
├── data/
│   ├── candidats.csv            Données candidats (séparateur ;)
│   ├── examinateurs.csv         Données examinateurs
│   ├── preps.csv                Matières et durées
│   ├── algo_params.json         Paramètres algo (généré par l'interface web)
│   └── credentials.enc          Store chiffré des identifiants (AES-256-GCM)
│
├── docs/
│   ├── architecture.md          Choix de conception et décisions techniques
│   ├── securite.md              Niveaux d'accès, mesures de sécurité, RGPD
│   ├── setup.md                 Configuration d'un nouveau site
│   ├── workflow_admin.md        Guide administrateur : CSV, algo, jour J
│   ├── algo.md                  Fonctionnement de l'algorithme de placement
│   ├── structure.md             Ce fichier
│   └── secrets_backup.md        Procédure de sauvegarde des secrets
│
├── tests/
│   ├── unit/
│   │   ├── test_algo.py            Tests unitaires de l'algorithme
│   │   ├── test_code_quality.py    Annotations, PEP8 et mypy sur app.py
│   │   ├── test_csv_validator.py   Tests unitaires du validateur CSV
│   │   ├── test_ods_handler.py     Tests unitaires du lecteur/générateur ODS
│   │   └── test_setup_utils.py     Tests unitaires des utilitaires de setup
│   ├── integration/
│   │   ├── test_flask_routes.py    Tests d'intégration Flask (DB mockée)
│   │   └── test_security.py        Non-régression des constats de l'audit sécurité
│   └── load/
│       └── test_sse_rate_limit.py  Test de charge sur le flux SSE
│
├── .github/workflows/ci.yml     Pipeline CI : pip-audit + tests
├── docker-compose.yml           Stack : app, nginx, redis, mariadb
├── Dockerfile                   Image runtime (Python, non-root — sans dépendance Java)
├── .env.example                 Template des variables Docker à copier en .env
├── .dockerignore                Exclut tout sauf requirements.txt du contexte
│
├── nginx-conf/                  Configs nginx générées par setup_new_site.py
│
└── webserver/
    ├── app.py                   Application Flask principale
    ├── app_secrets.py           Secrets (root:root 640, non versionné) + ACCENT_COLOR
    ├── algo_bg.py               Exécution de algo.py en tâche de fond (SSE)
    ├── credential_store.py      Chiffrement AES-256-GCM du store d'identifiants
    ├── theme.py                 Dérivation de la palette depuis ACCENT_COLOR
    ├── csv_validator.py         Validation et normalisation des fichiers CSV
    ├── ods_handler.py           Lecture et génération ODS (odfpy) + lycées Aix-Marseille
    ├── db_facility_web.py       Requêtes SQL paramétrées
    ├── reports.py               Génération PDF (ReportLab + pypdf, palette thème)
    ├── flask_sse.py             Blueprint SSE avec cache Redis
    ├── patched_app.py           Point d'entrée gunicorn (gevent monkey-patch)
    │
    ├── static/
    │   ├── .well-known/
    │   │   └── security.txt     Contact de divulgation responsable (RFC 9116)
    │   ├── templates_csv/       Modèles CSV individuels
    │   └── ...                  CSS (main.css), polices, timer.js, icônes
    │
    ├── generated/                PDFs générés (volume Docker dédié) — HORS de static/ :
    │                              jamais servi par nginx ni par le handler statique
    │                              Flask, accès uniquement via /download (authentifié)
    │
    └── templates/
        ├── admin_nav.html       Sidebar de navigation admin (incluse dans les pages /gestion/*)
        ├── auth_icon.html       Header fixe (logo + badge utilisateur)
        ├── login.html           Login admin (TOTP)
        ├── login_examinateur.html
        ├── login_loge.html
        ├── login_candidat.html
        ├── candidat.html        Fiche candidat
        ├── salle.html           Fiche salle + émargement + minuteurs
        ├── loge.html            Fiche loge + minuteurs
        ├── liste.html           Liste générale (grand écran)
        ├── sign.html            Signature dématérialisée
        ├── index_gestion.html   Liste des oraux (admin)
        ├── liste_candidats.html Liste des candidats (admin)
        ├── liste_examinateurs.html
        ├── gestion_algo.html    Upload + paramètres + lancement algo
        ├── gestion_documents.html  Téléchargement centralisé des PDFs
        ├── credentials.html     Renouvellement des identifiants
        ├── monitoring.html      Tableau de bord admin
        ├── archive.html         Archive de fin de session
        ├── verify_logs.html     Vérification de l'intégrité des logs
        └── mentions_legales.html
```

---

## Dépendances

### Système

- **Docker** + **Docker Compose v2**
- **nginx** (hôte, pour le SSL)
- **certbot** + `python3-certbot-nginx` (pour Let's Encrypt)

### Python (`requirements.txt`, versions épinglées)

```
Flask==3.1.3, Flask-SSE==1.0.0, Flask-WTF==1.3.0, Flask-Compress==1.24
Flask-Limiter==4.1.1, Flask-Talisman==1.1.0
gunicorn==26.0.0, gevent==26.5.0
mysql-connector-python==9.7.0
redis==8.0.0
pyotp==2.10.0, segno==1.6.6
reportlab==5.0.0, pypdf==6.14.2, pillow==12.2.0
odfpy>=1.4.0
cryptography==49.0.0          # Store credentials AES-256-GCM + HKDF
pytz==2026.2, colorama==0.4.6, setuptools==82.0.1
```
