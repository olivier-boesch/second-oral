# ── Environnement runtime uniquement ─────────────────────────────────────────
# Le code source de l'application n'est PAS copié dans l'image.
# Il est monté via un bind-mount dans docker-compose.yml.
# Cette image contient seulement : Python, les dépendances système et
# les paquets pip (installés depuis requirements.txt).
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Dépendances système
#   - gcc : compilation de certains paquets pip (ex. gevent)
#   Note : pdftk-java supprimé — concaténation PDF assurée par pypdf (pure Python)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python à partir du fichier requirements
# (ce fichier est copié uniquement pour le build ; le code est en volume)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Enlever gcc après la compilation (image plus légère)
RUN apt-get purge -y --auto-remove gcc

# Utilisateur non-root dédié — son UID/GID doit correspondre à ceux du compte
# système dédié sur l'hôte (cf. setup_new_site.py: ensure_app_system_user),
# afin que appuser puisse lire les fichiers du bind-mount qui lui appartiennent
# côté hôte (ex. app_secrets.py). Valeurs transmises via --build-arg par
# `docker compose build` (setup_new_site.py: docker_setup) ; 1000 par défaut
# pour les builds manuels.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} appuser && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home appuser

# Crée le répertoire des PDFs générés avec les bons droits avant de passer à appuser.
# Le volume Docker nommé generated_docs est initialisé à partir de ce dossier ;
# sans cette étape il serait root:root 755 et appuser ne pourrait pas y écrire.
# Volontairement hors de webserver/static/ : jamais servi en statique par
# nginx (cf. docker-compose.yml et docs/securite.md).
RUN mkdir -p /app/webserver/generated \
    && chown appuser:appuser /app/webserver/generated

# Point d'entrée : gunicorn depuis le dossier webserver (monté en volume)
WORKDIR /app/webserver

USER appuser

EXPOSE 8000

CMD ["gunicorn", \
     "--worker-class", "gevent", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "--timeout", "120", \
     "--access-logformat", "%({x-forwarded-for}i)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s", \
     "patched_app:app"]
