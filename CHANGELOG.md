# CHANGELOG

## [Unreleased] — 2026.2

### Added
- Tests unitaires pour `algo.py` : placement, capacité insuffisante, cohérence des horaires, écart minimum candidat, timing (`tests/unit/test_algo.py`)
- Route `GET /health` dans Flask (vérification DB + Redis) exemptée du rate limiter
- Healthcheck Docker pour le service `app` (via Python urllib, sans dépendance curl)
- Sentry : intégration optionnelle via `SENTRY_DSN` (prod uniquement, `traces_sample_rate=0.05`)
- Script de test de charge SSE (`tests/load/test_sse_rate_limit.py`)
- Documentation de l'algorithme de placement (`docs/algo.md`)
- Documentation de la stratégie de backup des secrets (`docs/secrets_backup.md`)
- Documentation de la capacité et du rate limiting SSE (`docs/capacity.md`)

### Changed
- Rate limiting SSE : `30/min` → `300/min` (validé empiriquement — supporte ~120 connexions simultanées : 90 candidats + examinateurs + loges)

### Changed
- `algo.py` : credentials temporaires écrits dans `/dev/shm` (RAM, jamais sur disque) avec fallback sur `data/` si indisponible
- `app.py` : `_CREDENTIALS_TMP_FILE` pointe vers `/dev/shm/second_oral_creds_new.json` (cohérent avec algo.py)

### Fixed
- `algo.py` : erreur "Pas de créneau trouvé" remplacée par `PasDeCreneauDisponible` avec contexte (numéro candidat, nombre d'examinateurs) — les causes d'échec sont maintenant loggées en `CRITICAL` quand tous les runs échouent

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

[Unreleased]: https://github.com/olivier-boesch/second-oral/compare/v2026.1...HEAD
[2026.1]: https://github.com/olivier-boesch/second-oral/releases/tag/v2026.1
