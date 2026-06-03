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

# Point d'entrée : gunicorn depuis le dossier webserver (monté en volume)
WORKDIR /app/webserver

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
