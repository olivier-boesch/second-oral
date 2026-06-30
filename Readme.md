# 2ndOral

[![CI](https://github.com/olivier-boesch/second-oral/actions/workflows/ci.yml/badge.svg)](https://github.com/olivier-boesch/second-oral/actions/workflows/ci.yml)

Application web de gestion des oraux de second groupe du baccalauréat :
placement automatique des candidats et examinateurs, consultation en temps réel,
émargement dématérialisé, génération de documents PDF.

**En production depuis 2023** sur un centre d'examen de l'académie d'Aix-Marseille.
~90 candidats (180 oraux) traités en une journée sur une instance.

---

## Documentation

| Document | Contenu |
|---|---|
| [docs/setup.md](docs/setup.md) | Configuration d'un nouveau site (`setup_new_site.py`), prérequis, checklist |
| [docs/workflow_admin.md](docs/workflow_admin.md) | Workflow annuel complet : CSV, algorithme, jour J, archivage |
| [docs/algo.md](docs/algo.md) | Fonctionnement de l'algorithme de placement |
| [docs/securite.md](docs/securite.md) | Niveaux d'accès, mesures de sécurité, RGPD |
| [docs/architecture.md](docs/architecture.md) | Choix de conception et décisions techniques |
| [docs/structure.md](docs/structure.md) | Arborescence des fichiers et dépendances |
| [docs/secrets_backup.md](docs/secrets_backup.md) | Sauvegarde des secrets |

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
ReportLab (PDF), pypdf (concat PDF), nginx (hôte + Docker), Let's Encrypt.

---

## Déploiement rapide

```bash
# 1. Cloner le dépôt
git clone ... && cd second_oral

# 2. Configurer le site (secrets, TOTP, nginx, certbot, Docker, DB)
sudo python setup_new_site.py

# 3. Recharger nginx hôte si nécessaire
sudo nginx -t && sudo nginx -s reload
```

> Voir [docs/setup.md](docs/setup.md) pour le détail complet et le mode batch.

### Commandes courantes

```bash
docker compose up -d          # Démarrer
docker compose stop           # Arrêter
docker compose logs -f app    # Logs de l'application
docker compose restart app    # Appliquer un changement de code
docker compose build app      # Rebuild si requirements.txt a changé
```

---

## Tests et CI

```bash
python -m pytest tests/ -v          # Toute la suite
python -m pytest tests/unit/        # Unitaires uniquement
python -m pytest tests/integration/ # Intégration Flask (DB mockée)
```

Le pipeline GitHub Actions (`.github/workflows/ci.yml`) exécute à chaque push :
1. **`pip-audit`** — détection des CVE connues dans les dépendances
2. **Tests** — unitaires + intégration (annotations de type, PEP8, mypy, non-régression sécurité)
