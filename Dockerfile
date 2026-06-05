# ── Environnement runtime uniquement ─────────────────────────────────────────
# Le code source de l'application n'est PAS copié dans l'image.
# Il est monté via un bind-mount dans docker-compose.yml.
# Cette image contient seulement : Python, les dépendances système et
# les paquets pip (installés depuis requirements.txt).
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Dépendances système
#   - pdftk-java  : concaténation de PDFs (requis par pypdftk)
#   - gcc         : compilation de certains paquets pip (ex. gevent)
RUN apt-get update && apt-get install -y --no-install-recommends \
        pdftk-java \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python à partir du fichier requirements
# (ce fichier est copié uniquement pour le build ; le code est en volume)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Enlever gcc après la compilation (image plus légère)
RUN apt-get purge -y --auto-remove gcc

# Utilisateur non-root dédié (UID 1000 pour compatibilité avec les volumes hôte)
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid 1000 --no-create-home appuser

# Crée le répertoire des PDFs générés avec les bons droits avant de passer à appuser.
# Le volume Docker nommé static_docs est initialisé à partir de ce dossier ;
# sans cette étape il serait root:root 755 et appuser ne pourrait pas y écrire.
RUN mkdir -p /app/webserver/static/docs \
    && chown appuser:appuser /app/webserver/static/docs

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
