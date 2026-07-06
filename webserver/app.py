"""
Second Oral — Application Web Flask

Gestion des oraux de second groupe :
  - Consultation publique (candidats, salles, loges)
  - Authentification examinateurs (mot de passe par salle)
  - Authentification admin (TOTP)
  - Authentification candidats (numéro de candidat + papillon)
  - Signature dématérialisée
  - Génération de documents PDF
"""
import csv
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
from functools import wraps
from urllib.parse import quote, urlencode, urlparse, urlunparse

import logging
import pyotp
import segno
from flask import (
    Flask, render_template, request, redirect, abort,
    session, jsonify, url_for, make_response, send_from_directory, send_file, g,
)
from flask.typing import ResponseReturnValue
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from wtforms.fields.choices import SelectField
from wtforms.fields.simple import PasswordField, SubmitField, StringField, HiddenField
from flask_sse import sse
from flask_talisman import Talisman
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect, generate_csrf
from wtforms.validators import DataRequired
from PIL import Image
from io import BytesIO, StringIO
from base64 import b64encode, b64decode
from credential_store import load_credentials as _cstore_load, save_credentials as _cstore_save
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import db_facility_web
import rebalance
import reports
from theme import derive_palette as _derive_palette
from ods_handler import LYCEES_DISPLAY as _LYCEES_DISPLAY
from app_secrets import (
    CENTRE_EXAMEN, DIGITAL_SIGN, LOGIN_KEY, APP_SECRET_KEY, HOSTNAME,
    TIMEZONE, hash_password, check_password, verify_log_item, generate_password,
)
import app_secrets as _app_secrets
# FQDN optionnel (ajouté par setup_new_site.py) — fallback sur valeur codée en dur
FQDN = getattr(_app_secrets, "FQDN", "stex.mesoraux.fr")
# Variables légales — optionnelles pour compatibilité avec les instances existantes
DIRECTOR_NAME  = getattr(_app_secrets, "DIRECTOR_NAME",  "")
CENTRE_ADDRESS = getattr(_app_secrets, "CENTRE_ADDRESS", "")
ACADEMIE       = getattr(_app_secrets, "ACADEMIE",       "")
HEBERGEUR      = getattr(_app_secrets, "HEBERGEUR",      "")
DPD_EMAIL      = getattr(_app_secrets, "DPD_EMAIL",      "")
ACCENT_COLOR   = getattr(_app_secrets, "ACCENT_COLOR",   "#6c63ff")
from secrets import token_urlsafe
from datetime import timedelta

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

try:
    from dev import dev_on  # noqa: F401
except ImportError:
    dev_on = False

app = Flask("2ndOral_app")

_REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost')

# Version des fichiers statiques pour cache-busting (hash git court, ou mtime fallback)
try:
    import subprocess as _sp
    _STATIC_VERSION = _sp.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        cwd=Path(__file__).parent,
        stderr=_sp.DEVNULL, text=True,
    ).strip()
except Exception:
    try:
        _STATIC_VERSION = str(int((Path(__file__).parent / 'static' / 'main.css').stat().st_mtime))
    except OSError:
        _STATIC_VERSION = '0'

_SENTRY_DSN = os.environ.get('SENTRY_DSN')
if not dev_on and _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.05,   # 5 % des requêtes tracées (éviter le quota gratuit)
        environment="production",
    )

if dev_on:
    app.config.update(
        REDIS_URL=_REDIS_URL,
        SEND_FILE_MAX_AGE_DEFAULT=0,
        TEMPLATES_AUTO_RELOAD=True,
        APPLICATION_ROOT='/',
    )
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["1000 per day", "100 per hour"],
        storage_uri="memory://",
    )
else:
    app.config.update(
        REDIS_URL=_REDIS_URL,
        SEND_FILE_MAX_AGE_DEFAULT=31_536_000,  # 1 an — cache-busting via ?v=<git_hash>
        TEMPLATES_AUTO_RELOAD=True,
        PREFERRED_URL_SCHEME='https',
        SERVER_NAME=FQDN,
        APPLICATION_ROOT='/',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE='Lax',
        # #6 — Expiration de session inactive (8 h)
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["1000 per day", "200 per hour"],
        storage_uri=_REDIS_URL,
    )
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

    # #5 — CSP : les handlers inline (onclick/onchange) imposent 'unsafe-inline'.
    # Talisman génère un nonce par requête via content_security_policy_nonce_in
    # et l'expose dans g.csp_nonce (lu par le context_processor ci-dessous).
    # 'strict-dynamic' + nonce neutralise 'unsafe-inline' dans les navigateurs
    # modernes ; 'unsafe-inline' reste comme fallback pour les anciens.
    csp = {
        'default-src': "'self'",
        'script-src':      "'self' 'unsafe-inline' 'strict-dynamic'",
        'script-src-attr': "'unsafe-inline'",
        'style-src':   "'self' 'unsafe-inline'",
        'img-src':     "'self' data:",
        'base-uri':    "'self'",
        'form-action': "'self'",
    }
    Talisman(
        app,
        content_security_policy=csp,
        content_security_policy_nonce_in=['script-src'],
        # #4 — HSTS explicite : 1 an, includeSubDomains
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=True,
        strict_transport_security_preload=True,
        # #8 — Headers supplémentaires
        referrer_policy='strict-origin-when-cross-origin',
        permissions_policy={
            'camera':      '()',
            'microphone':  '()',
            'geolocation': '()',
            'payment':     '()',
            'usb':         '()',
        },
    )

Compress(app)
app.secret_key = APP_SECRET_KEY
# Active la vérification CSRF sur toutes les requêtes mutantes (POST/PUT/PATCH/
# DELETE) : sans cet enregistrement, les jetons csrf_token rendus dans les
# formulaires ne sont jamais validés côté serveur.
CSRFProtect(app)

app.register_blueprint(sse, url_prefix='/stream')
# Le flux SSE est un flux long-vivant : les limites par défaut (pensées pour
# des requêtes courtes) gêneraient les reconnexions légitimes (plusieurs
# onglets, ré-essais EventSource après coupure réseau/proxy, cf.
# script_reload.html / sign.html). On applique donc une limite dédiée, large
# mais bornée, plutôt qu'une exemption totale : sans elle, n'importe quel
# compte authentifié pourrait ouvrir un flot de connexions en boucle, chacune
# retenant indéfiniment un greenlet + une souscription Redis (DoS, cf.
# flask_sse._get_redis_sub avec socket_timeout=None).
limiter.limit("300 per minute", override_defaults=True)(sse)


_AUTH_FAIL_THRESHOLD = 5   # échecs avant avertissement dans les logs
_auth_failures: dict = {}  # {ip: [timestamp, ...]} — en mémoire, réinitialisé au redémarrage


def _record_auth_failure(role: str, identifier: str) -> None:
    """Enregistre un échec d'auth et émet un WARNING si le seuil est dépassé."""
    import time as _time
    ip = request.remote_addr or "unknown"
    now = _time.time()
    window = 300  # 5 min glissantes
    timestamps = [t for t in _auth_failures.get(ip, []) if now - t < window]
    timestamps.append(now)
    _auth_failures[ip] = timestamps
    # Purge les IP qui n'ont plus d'échec récent — sans cela, _auth_failures
    # grossit indéfiniment (une entrée par IP ayant un jour échoué) et n'est
    # jamais libéré avant le redémarrage du serveur.
    for stale_ip in [k for k, v in _auth_failures.items()
                     if not any(now - t < window for t in v)]:
        del _auth_failures[stale_ip]
    count = len(timestamps)
    if count >= _AUTH_FAIL_THRESHOLD:
        app.logger.warning(
            f"ALERTE AUTH — {count} échecs en {window}s depuis {ip} "
            f"(rôle={role}, id={identifier})"
        )


@app.context_processor
def _inject_csp_nonce():
    """Rend csp_nonce disponible dans les templates (généré par Talisman)."""
    # Talisman stocke le nonce dans request.csp_nonce (via before_request),
    # pas dans g.csp_nonce. Lire depuis request pour que {{ csp_nonce }}
    # dans les templates reçoive la valeur correcte.
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}


@app.context_processor
def _inject_static_version():
    """Version des fichiers statiques pour cache-busting dans les templates."""
    return {'sv': _STATIC_VERSION}


@app.context_processor
def _inject_candidat_nom():
    """
    Rend le nom du candidat connecté disponible dans tous les templates —
    RGPD : l'en-tête (auth_icon.html) doit afficher son nom, jamais son numéro.
    """
    return {'candidat_nom': get_candidat_nom()}


@app.after_request
def _security_headers(response):
    """#8 — Headers de sécurité complémentaires non couverts par Talisman."""
    response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    return response


@app.before_request
def _check_session_expiry():
    """#6 — Invalide les sessions créées il y a plus de 8 h."""
    import time as _time
    ts = session.get('_ts')
    if ts and _time.time() - ts > 8 * 3600:
        session.clear()
        return
    if any(k in session for k in ('user', 'candidat', 'loge')):
        if not ts:
            session['_ts'] = _time.time()


def _sse_channel_allowed(channel: str) -> bool:
    """
    Vrai si la session courante peut s'abonner au canal SSE demandé.

    Le nom du canal est entièrement choisi par le client via `?channel=...`
    (cf. flask_sse, qui ne fait lui-même aucune vérification d'autorisation).
    Sans ce contrôle, n'importe quel utilisateur authentifié — y compris un
    candidat — pourrait s'abonner aux canaux d'autrui (ex. `candidat_<numero>`
    d'un autre candidat) et récupérer des données personnelles diffusées
    dessus (IDOR / fuite RGPD).
    """
    if is_admin_user():
        return True
    if channel == 'general':
        return True  # diffusion générale, sans donnée personnelle
    # Personnel (examinateur connecté ou surveillant de loge) : les routes
    # salle()/loge() les laissent déjà consulter n'importe quelle fiche
    # salle/loge via is_authenticated() (cf. commentaire "on laisse
    # l'examinateur voir toutes les loges pour l'instant") — l'autorisation
    # SSE doit suivre la même règle, sinon le flux temps réel se retrouve
    # bloqué (403) dès qu'un examinateur ou surveillant consulte une fiche
    # qui n'est pas la sienne. Seul le candidat reste restreint à son propre
    # canal (seul cas où une donnée personnelle individuelle est en jeu).
    is_personnel = 'user' in session or 'loge' in session
    if channel.startswith('salle_') or channel.startswith('loge_'):
        return is_personnel
    if channel.startswith('candidat_'):
        return session.get('candidat') == channel[len('candidat_'):]
    if channel.startswith('sign_'):
        # Le nom du canal embarque le token de signature à usage unique
        # (capacité secrète générée aléatoirement) : le connaître équivaut à
        # le posséder. Accessible à tout utilisateur déjà authentifié.
        return True
    return False  # canal inconnu (dont 'algo_output', 'sse') → admin uniquement


@app.before_request
def protect_sse():
    """Le flux SSE est réservé aux utilisateurs authentifiés et autorisés sur le canal demandé."""
    if request.path.startswith('/stream'):
        if ('user' not in session
                and 'candidat' not in session
                and 'loge' not in session):
            abort(401)
        # Plusieurs canaux séparés par des virgules sont acceptés (ex.
        # `candidat_<numero>,general` : une page écoute à la fois son canal
        # dédié et le canal général de rechargement global) — cf. flask_sse.
        channels = (request.args.get('channel') or 'sse').split(',')
        if not all(_sse_channel_allowed(channel) for channel in channels):
            abort(403)

app._db = db_facility_web.DbInterface()  # type: ignore[attr-defined]
app._otp = pyotp.TOTP(LOGIN_KEY)  # type: ignore[attr-defined]


def _get_redis_pub_for_ine():
    """Client Redis (court-circuit) pour stocker les tokens de numéro candidat temporaires."""
    from flask_sse import _get_redis_pub
    return _get_redis_pub(app.config.get('REDIS_URL', 'redis://localhost'))


def _redis():
    """Client Redis partagé (stats + sessions online)."""
    from flask_sse import _get_redis_pub
    return _get_redis_pub(app.config.get('REDIS_URL', 'redis://localhost'))


@app.after_request
def _stats_count_request(response):
    """Incrémente les compteurs de requêtes dans Redis (silencieux si Redis indisponible)."""
    if getattr(request, 'endpoint', None) in ('sse.stream', 'static', None):
        return response
    try:
        r = _redis()
        bucket = f"{response.status_code // 100}xx"
        hour_key = f"stats:req:h:{datetime.now(TIMEZONE).strftime('%Y%m%d%H')}"
        pipe = r.pipeline()
        pipe.incr('stats:req:total')
        pipe.incr(f'stats:req:status:{bucket}')
        pipe.incr(hour_key)
        pipe.expire(hour_key, 49 * 3600)
        pipe.execute()
    except Exception as e:
        app.logger.debug(f"stats Redis indisponible : {e}")
    return response


def _online_set(kind: str, ident: str) -> None:
    """Marque une session comme active avec l'IP (TTL = durée de session maxi)."""
    try:
        ip = request.remote_addr or 'inconnue'
        _redis().set(f'stats:online:{kind}:{ident}', ip, ex=8 * 3600)
    except Exception as e:
        app.logger.debug(f"online_set Redis indisponible : {e}")


def _online_clear(kind: str, ident: str) -> None:
    """Supprime le marqueur de session active."""
    try:
        _redis().delete(f'stats:online:{kind}:{ident}')
    except Exception as e:
        app.logger.debug(f"online_clear Redis indisponible : {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Utilitaires DB
# ──────────────────────────────────────────────────────────────────────────────

def db_get(sql, *args, no_list_auto=True):
    """Exécute un SELECT et renvoie un dict (1 résultat) ou une liste."""
    data = app._db.make_sql_select(sql, *args)
    if len(data) == 1 and no_list_auto:
        return data[0]
    return data


def db_update(sql, **kwargs):
    """Exécute un INSERT/UPDATE/DELETE."""
    app._db.make_sql_update(sql, **kwargs)


def fetch_candidat(id_candidat) -> dict | None:
    """Retourne les infos candidat + ses oraux, ou None si introuvable."""
    rows = db_get(db_facility_web.SELECT_INFOS_CANDIDAT, id_candidat, no_list_auto=False)
    if not rows:
        return None
    data = rows[0]
    data['oraux'] = db_get(db_facility_web.SELECT_ORAUX_CANDIDAT, id_candidat, no_list_auto=False)
    return data


def fetch_salle(id_salle) -> dict | None:
    """Retourne les infos salle + ses oraux, ou None si introuvable."""
    rows = db_get(db_facility_web.SELECT_INFOS_SALLE, id_salle, no_list_auto=False)
    if not rows:
        return None
    data = rows[0]
    data['oraux'] = db_get(db_facility_web.SELECT_ORAUX_SALLE, data['id'], no_list_auto=False)
    return data


def fetch_loge(id_loge) -> dict | None:
    """Retourne les infos loge + ses oraux, ou None si introuvable."""
    rows = db_get(db_facility_web.SELECT_INFOS_LOGE, id_loge, no_list_auto=False)
    if not rows:
        return None
    data = rows[0]
    data['oraux'] = db_get(db_facility_web.SELECT_ORAUX_LOGE, id_loge, no_list_auto=False)
    return data


# ──────────────────────────────────────────────────────────────────────────────
# Gestion des erreurs
# ──────────────────────────────────────────────────────────────────────────────

@app.errorhandler(404)
@app.errorhandler(400)
@app.errorhandler(500)
@app.errorhandler(403)
def page_not_found(e):
    return render_template("error.html", e=e), e.code


@app.errorhandler(429)
def page_forbidden(e):
    e.description = "Too many requests"
    return page_not_found(e)


# ──────────────────────────────────────────────────────────────────────────────
# Décorateurs / Helpers
# ──────────────────────────────────────────────────────────────────────────────

@app.template_filter('qr')
def qr(path, **kwargs):
    """Filtre Jinja2 : génère un QR code SVG data-URI."""
    return segno.make_qr(path).svg_data_uri(**kwargs)


@app.template_filter('heure')
def heure_filter(td):
    """Filtre Jinja2 : formate un timedelta en HH:MM (cf. rebalance.py)."""
    if td is None:
        return ''
    total_minutes = int(td.total_seconds()) // 60
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}:{m:02d}"


def nocache(f):
    """Désactive le cache navigateur pour la réponse."""
    @wraps(f)
    def no_cache(*args, **kwargs):
        response = make_response(f(*args, **kwargs))
        response.headers['Last-Modified'] = datetime.now()
        response.headers['Cache-Control'] = (
            'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        )
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response
    return no_cache


def _safe_redirect_url(url):
    """
    Valide qu'une URL de redirection reste sur le même domaine.
    Renvoie None si l'URL est externe, malformée, ou utilise un schéma dangereux
    (javascript:, data:, etc.).
    """
    if not url or url in ('None', ''):
        return None
    parsed = urlparse(url)
    # Rejeter tout schéma autre que http/https/vide (chemins relatifs)
    if parsed.scheme not in ('', 'http', 'https'):
        return None
    if parsed.netloc and parsed.netloc != request.host:
        return None
    return url


def _add_query_param(url: str, **params) -> str:
    """Ajoute des paramètres de requête à une URL relative déjà validée par
    _safe_redirect_url (utilisé pour transmettre new_papillon via link_back)."""
    parsed = urlparse(url)
    query = urlencode(params)
    if parsed.query:
        query = f"{parsed.query}&{query}"
    return urlunparse(parsed._replace(query=query))


def is_admin_user():
    """Renvoie True si l'utilisateur courant est l'admin."""
    return 'user' in session and session['user'] == 'admin'


def is_teacher_user():
    """
    Renvoie True si l'utilisateur courant est un examinateur authentifié.
    Résultat mis en cache dans le contexte de requête (flask.g).
    """
    if '_is_teacher' in g:
        return g._is_teacher
    if 'user' not in session:
        g._is_teacher = False
        return False
    info = db_get(db_facility_web.SELECT_PASSWORD_CHECK_SALLE,
                  session['user'], no_list_auto=False)
    g._is_teacher = len(info) == 1
    return g._is_teacher


def is_loge_user(loge_nom=None):
    """
    Renvoie True si un surveillant de loge est connecté.
    Si loge_nom est fourni, vérifie que c'est bien celle-là.
    """
    loge = session.get('loge')
    if not loge:
        return False
    if loge_nom is not None:
        return loge == loge_nom
    return True


def is_authenticated():
    """Renvoie True si admin, examinateur ou surveillant de loge."""
    return is_admin_user() or is_teacher_user() or is_loge_user()


def is_any_authenticated():
    """Renvoie True si n'importe quel utilisateur (y compris candidat) est connecté."""
    return is_authenticated() or is_student_user()


def is_student_user(numero=None):
    """
    Renvoie True si un candidat est connecté.
    Si numero est fourni, vérifie que le candidat connecté est bien celui-là.
    """
    if 'candidat' not in session:
        return False
    if numero is not None:
        return session['candidat'] == str(numero)
    return True


def get_candidat_nom():
    """
    Renvoie le nom (et jamais le numéro) du candidat connecté, ou None.
    Résultat mis en cache dans le contexte de requête (flask.g).
    """
    if '_candidat_nom' in g:
        return g._candidat_nom
    numero = session.get('candidat')
    if not numero:
        g._candidat_nom = None
        return None
    infos = db_get(db_facility_web.SELECT_CANDIDAT_AUTH, numero, no_list_auto=False)
    g._candidat_nom = infos[0]['nom'] if infos else None
    return g._candidat_nom


def get_username(complete=True):
    """Renvoie le nom d'affichage de l'utilisateur connecté (admin ou examinateur)."""
    if 'user' not in session:
        return None
    if session['user'] == 'admin':
        return 'Admin'
    if not complete:
        return session['user']
    infos = db_get(db_facility_web.SELECT_PASSWORD_CHECK_SALLE, session['user'])
    return f"{session['user']} - {infos['nom']}"


def admin_required(f):
    """Décorateur : redirige vers login si l'utilisateur n'est pas admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin_user():
            # url_for encode link_back une seule fois — pas de quote() manuel
            return redirect(url_for('login', link_back=request.url))
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────────────────────
# Gestion des tokens de signature
# ──────────────────────────────────────────────────────────────────────────────

def _redact_token(token):
    """
    Tronque un token sensible pour la journalisation : un token de signature
    donne un accès temporaire NON authentifié — le journaliser en clair
    permettrait à quiconque a accès aux logs de l'utiliser pendant sa
    fenêtre de validité (5 minutes). On ne garde qu'un préfixe, suffisant
    pour corréler les entrées de log entre elles sans exposer le secret.
    """
    if not token:
        return token
    return token[:6] + "…"


def generate_token(id_oral):
    """Génère un token de signature et le stocke en base."""
    tk = token_urlsafe(16)
    d = {
        'token': tk,
        'time_limit': (datetime.now() + timedelta(minutes=5)).isoformat(),
        'oral': id_oral,
    }
    db_update(db_facility_web.INSERT_TOKEN_SIGNATURE, **d)
    app.logger.info(f"Token: généré ({_redact_token(tk)}) pour l'oral {id_oral}")
    return tk


def check_token(token, id_oral):
    """
    Vérifie un token de signature :
    - absent → False
    - expiré → False
    - oral ne correspond pas → False
    - valide → True
    Supprime le token après vérification (usage unique).
    """
    results = db_get(db_facility_web.SELECT_TOKEN_SIGNATURE, token, no_list_auto=False)
    if len(results) != 1:
        return False
    row = results[0]
    # Comparaison en str pour gérer int/str depuis la BDD
    time_ok = datetime.fromisoformat(row['time_limit']) > datetime.now()
    oral_ok = str(id_oral) == str(row['oral'])
    valid = time_ok and oral_ok
    db_update(db_facility_web.DELETE_TOKEN_SIGNATURE, token=token)
    app.logger.info(f"Token: vérifié et supprimé ({_redact_token(token)}), valide={valid}")
    return valid


def clear_outdated_tokens(id_oral=None):
    """Supprime les tokens expirés ou liés à un oral spécifique."""
    results = db_get(db_facility_web.SELECT_TOKEN_SIGNATURE_ALL, no_list_auto=False)
    for res in results:
        if datetime.fromisoformat(res['time_limit']) <= datetime.now():
            db_update(db_facility_web.DELETE_TOKEN_SIGNATURE, token=res['token'])
            app.logger.info(f"Token: expiré supprimé ({_redact_token(res['token'])})")
        elif id_oral is not None and str(res['oral']) == str(id_oral):
            db_update(db_facility_web.DELETE_TOKEN_SIGNATURE, token=res['token'])
            app.logger.info(f"Token: oral {id_oral} supprimé ({_redact_token(res['token'])})")


def image_normalize(img, size=(300, 300)):
    """Redimensionne une image base64 et la renvoie au format PNG base64."""
    if img == '':
        return ''
    img_data = b64decode(img.split(",")[1])
    image_file = BytesIO(img_data)
    image_file.seek(0)
    image = Image.open(image_file)
    image = image.resize(size)
    img_out = BytesIO()
    image.save(img_out, format="PNG")
    app.logger.debug("Image: redimensionnée")
    return "data:image/png;base64," + b64encode(img_out.getbuffer()).decode('utf8')


# ──────────────────────────────────────────────────────────────────────────────
# Formulaires WTForms
# ──────────────────────────────────────────────────────────────────────────────

class LoginExaminateurForm(FlaskForm):
    salle = SelectField('Salle')
    password = PasswordField(
        'Password', id='id_password',
        render_kw={"autofocus": True},
        validators=[DataRequired(message="Entrez le mot de passe")],
    )
    submit_button = SubmitField('Se Connecter')
    dumb_field = StringField('Rien')


class LoginAdminForm(FlaskForm):
    key = StringField(
        "Code d'authentification OTP",
        render_kw={'inputmode': 'numeric', 'pattern': "[0-9]{6}",
                   "autofocus": True, "placeholder": "OTP"},
        validators=[DataRequired()],
    )
    link_back = HiddenField(name='link_back', validators=[DataRequired()])
    dumb_field = StringField('Rien')


class LoginLogeForm(FlaskForm):
    loge = SelectField('Loge')
    password = PasswordField(
        'Mot de passe', id='id_password_loge',
        render_kw={"autofocus": True},
        validators=[DataRequired(message="Entrez le mot de passe")],
    )
    submit_button = SubmitField('Se Connecter')
    dumb_field = StringField('Rien')


class LoginCandidatForm(FlaskForm):
    numero = StringField(
        'N° candidat',
        render_kw={"autofocus": True, "placeholder": "Numéro de candidat"},
        validators=[DataRequired(message="Entrez votre numéro de candidat")],
    )
    password = PasswordField(
        'Mot de passe',
        render_kw={"placeholder": "Mot de passe du papillon"},
        validators=[DataRequired(message="Entrez le mot de passe")],
    )
    submit_button = SubmitField('Se Connecter')
    dumb_field = StringField('Rien')


class SignatureExaminateurForm(FlaskForm):
    link_back = HiddenField(name='link_back', validators=[DataRequired()])
    signature_image = HiddenField(name='signature_image', id="signature",
                                  validators=[DataRequired()])
    id_oral = HiddenField(name="id_oral", validators=[DataRequired()])
    cancel = HiddenField(name='cancel', validators=[DataRequired()])


class SignatureOtherDeviceForm(FlaskForm):
    signature_image = HiddenField(name='signature_image', id="signature",
                                  validators=[DataRequired()])
    id_oral = HiddenField(name="id_oral", validators=[DataRequired()])
    cancel = HiddenField(name='cancel', id='cancel', validators=[DataRequired()])
    token = HiddenField(name='token', validators=[DataRequired()])


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Infrastructure
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
@limiter.exempt
def health() -> ResponseReturnValue:
    """Health check pour orchestration Docker/K8s — ne pas rate-limiter."""
    try:
        app._db.make_sql_select("SELECT 1")  # type: ignore[attr-defined]
        _redis().ping()
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        app.logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Consultation publique
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index() -> ResponseReturnValue:
    """Page d'accueil — accès personnels (candidat/examinateur/loge) et administration."""
    return render_template(
        "index.html",
        url_of_page=request.url,
        centre=CENTRE_EXAMEN,
        authenticated=is_authenticated(),
        username=get_username(),
        admin=is_admin_user(),
        centre_examen=CENTRE_EXAMEN,
    )


@app.route('/c/<id_candidat>')
@nocache
def candidat_court(id_candidat: str) -> ResponseReturnValue:
    """Raccourci `/c/<id>` → redirige vers la fiche du candidat."""
    return redirect(url_for('candidat', id_candidat=id_candidat))


@app.route("/candidat/<id_candidat>")
@nocache
def candidat(id_candidat: str | None = None) -> ResponseReturnValue:
    """Fiche candidat — RGPD : authentification requise."""
    # Candidat connecté : seulement sa propre fiche
    if is_student_user() and not is_student_user(id_candidat):
        abort(403)
    # Toute autre personne non authentifiée
    if not is_any_authenticated():
        # Stocker le numéro dans Redis (TTL 5 min, token aléatoire) pour éviter
        # qu'il apparaisse dans l'URL ET pour éviter qu'il soit lisible en
        # base64 dans le payload du cookie Flask (non chiffré).
        _ine_token = token_urlsafe(16)
        _r = _get_redis_pub_for_ine()
        _r.setex(f"login_ine:{_ine_token}", 300, str(id_candidat))
        session['_login_tok'] = _ine_token
        return redirect(url_for('login_candidat'))
    donnees_candidat = fetch_candidat(id_candidat)
    if not donnees_candidat:
        abort(404, "Pas de candidat avec ce numéro")
    show_credentials = is_admin_user() or is_student_user(id_candidat)
    if not show_credentials:
        donnees_candidat.pop('login_key', None)
    return render_template(
        "candidat.html",
        data=donnees_candidat,
        centre=CENTRE_EXAMEN,
        admin=is_admin_user(),
        authenticated=is_authenticated(),
        show_credentials=show_credentials,
        sse_channel=f"candidat_{id_candidat}",
    )


@app.route("/loge")
@nocache
def loge_form() -> ResponseReturnValue:
    """Liste des loges — réservée au personnel (pas au public ni aux candidats)."""
    if not is_authenticated():
        return redirect(url_for('login_loge'))
    liste_loges = db_get(db_facility_web.SELECT_LISTE_LOGES, no_list_auto=False)
    return render_template(
        "loge_form.html",
        centre=CENTRE_EXAMEN,
        loges=liste_loges,
        url_of_page=request.url,
        authenticated=is_authenticated(),
        username=get_username(),
    )


@app.route('/l/<id_loge>')
@nocache
def loge_court(id_loge: str) -> ResponseReturnValue:
    """Raccourci `/l/<id>` → redirige vers la fiche de la loge."""
    return redirect(url_for('loge', id_loge=id_loge))


@app.route("/loge/<id_loge>")
@nocache
def loge(id_loge: str) -> ResponseReturnValue:
    """Fiche loge — RGPD : personnel uniquement."""
    if not is_authenticated() and not is_loge_user(id_loge):
        return redirect(url_for('login_loge', loge=id_loge))
    # Examinateur : seulement sa loge
    if is_teacher_user() and not is_admin_user():
        infos_salle = db_get(
            db_facility_web.SELECT_PASSWORD_CHECK_SALLE,
            session.get('user'), no_list_auto=False
        )
        if infos_salle:
            pass  # on laisse l'examinateur voir toutes les loges pour l'instant
    donnees_loge = fetch_loge(id_loge)
    if not donnees_loge:
        abort(404, "Cette loge n'est pas dans la liste des loges utilisées")
    students_ine_list = [item['numero'] for item in donnees_loge['oraux']]
    return render_template(
        "loge.html",
        data=donnees_loge,
        students_numeros=students_ine_list,
        authenticated=is_authenticated(),
        username=get_username(),
        centre=CENTRE_EXAMEN,
        sse_channel=f"loge_{id_loge}",
        can_use_timers=is_loge_user(id_loge),
    )


@app.route("/liste", methods=["GET"])
@nocache
def liste() -> ResponseReturnValue:
    """Liste générale — RGPD : personnel uniquement (pas les candidats)."""
    if not is_authenticated():
        return redirect(url_for('login', link_back=request.url))
    dont_scroll = request.args.get("dont_scroll", type=bool, default=False)
    oraux = db_get(db_facility_web.SELECT_LISTE_ORAUX, no_list_auto=False)
    return render_template(
        "liste.html",
        centre=CENTRE_EXAMEN,
        oraux=oraux,
        dont_scroll=dont_scroll,
        url_of_page=request.url,
        authenticated=is_authenticated(),
        username=get_username(),
        sse_channel="general",
    )


@app.route('/generate-screen-one', methods=['GET'])
@nocache
def generate_screen_one() -> ResponseReturnValue:
    """Affiche l'écran d'attente pendant la génération d'un document PDF individuel."""
    type_doc = request.args.get('type_doc')
    id_doc = request.args.get('id_doc')
    app.logger.info(f"Document: génération écran pour {type_doc} id={id_doc}")
    return render_template(
        'generate_screen.html',
        centre=CENTRE_EXAMEN,
        type_doc=type_doc,
        id_doc=id_doc,
        generation_url=url_for('generate_doc_one', type_doc=type_doc, id_doc=id_doc),
    )


@app.route('/generate-doc-one/<type_doc>-<id_doc>', methods=['GET'])
@nocache
def generate_doc_one(type_doc: str, id_doc: str | None = None) -> ResponseReturnValue:
    """Génère un PDF individuel (fiche candidat, salle ou loge)."""
    if type_doc == 'fiche_candidat':
        # Auth obligatoire : IDOR — sans contrôle, les noms complets sont
        # énumérables par ID séquentiel sans authentification.
        if not is_any_authenticated():
            abort(403)
        info_candidat = db_get(db_facility_web.SELECT_DOC_INFOS_CANDIDAT,
                               id_doc, no_list_auto=False)
        if not info_candidat:
            abort(404)
        info_candidat = info_candidat[0]
        info_candidat['oraux'] = db_get(
            db_facility_web.SELECT_DOC_INFOS_CANDIDATS_ORAUX, id_doc, no_list_auto=False
        )
        app.logger.debug(f"Document: fiche candidat {id_doc}")
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = reports.fiche_candidat(
                info_candidat, tmpdir, 'static/docs',
                filename_root='candidat_', centre_examen=CENTRE_EXAMEN,
            )
        return jsonify({"url": url_for('download', filename=filename)})

    if type_doc == 'fiche_salle':
        if not is_authenticated():
            abort(403)
        info_salle = fetch_salle(id_doc)
        if not info_salle:
            abort(404)
        app.logger.debug(f"Document: fiche salle {id_doc}")
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = reports.salle_oraux(
                info_salle, tmpdir, file_dir='static/docs',
                filename_root='salle', centre_examen=CENTRE_EXAMEN,
            )
        return jsonify({"url": url_for('download', filename=filename)})

    if type_doc == 'fiche_loge':
        if not is_authenticated():
            abort(403)
        donnees_loge = fetch_loge(id_doc)
        if not donnees_loge:
            abort(404)
        app.logger.debug(f"Document: fiche loge {id_doc}")
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = reports.loge_oraux(
                donnees_loge, tmpdir, file_dir='static/docs',
                filename_root='loge', centre_examen=CENTRE_EXAMEN,
            )
        return jsonify({"url": url_for('download', filename=filename)})

    app.logger.warning(f"Document: paramètres invalides ({type_doc}; {id_doc})")
    abort(404)


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Authentification examinateurs
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/login-examinateur', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute")
def login_examinateur() -> ResponseReturnValue:
    """Connexion d'un examinateur par numéro de salle + mot de passe."""
    form = LoginExaminateurForm()
    if request.method == 'GET':
        salle = request.args.get('salle', None)
        message = request.args.get('message', None)
        liste_salles = db_get(db_facility_web.SELECT_LISTE_SALLES, no_list_auto=False)
        # N'afficher que le numéro de salle — pas les noms ni les disciplines.
        # (RGPD : /login-examinateur est accessible sans auth, les noms
        # d'examinateurs ne doivent pas y être exposés au grand public.)
        form.salle.choices = [
            (s['salle'], s['salle'])
            for s in liste_salles
        ]
        form.salle.data = salle
        return render_template(
            "login_examinateur.html",
            centre=CENTRE_EXAMEN,
            salles=liste_salles,
            url_of_page=request.url,
            salle=salle,
            message=message,
            form=form,
        )

    # POST
    salle = form.salle.data
    passwd = form.password.data
    infos = db_get(db_facility_web.SELECT_PASSWORD_CHECK_SALLE,
                   salle, no_list_auto=False)
    if len(infos) == 1 and check_password(passwd, salle, infos[0]['password_hash']):
        session.clear()
        session['user'] = salle
        session['_ts'] = __import__('time').time()
        _online_set('exam', salle)
        app.logger.info(f"{salle}: connecté")
        return redirect(url_for('salle', id_salle=salle))
    app.logger.warning(f"{salle}: échec connexion")
    _record_auth_failure("examinateur", salle)
    return redirect(url_for('login_examinateur', salle=salle,
                             message='Mot de passe incorrect'))


@app.route("/salle")
@nocache
def salle_form() -> ResponseReturnValue:
    """Liste des salles — réservée au personnel (pas au public ni aux candidats)."""
    if not is_authenticated():
        return redirect(url_for('login_examinateur'))
    liste_salles = db_get(db_facility_web.SELECT_LISTE_SALLES, no_list_auto=False)
    return render_template(
        "salle_form.html",
        centre=CENTRE_EXAMEN,
        salles=liste_salles,
        url_of_page=request.url,
        authenticated=is_authenticated(),
        username=get_username(),
    )


@app.route('/s/<id_salle>')
@nocache
def salle_court(id_salle: str) -> ResponseReturnValue:
    """Raccourci `/s/<id>` → redirige vers la fiche de la salle."""
    return redirect(url_for('salle', id_salle=id_salle))


@app.route("/salle/<id_salle>")
@nocache
def salle(id_salle: str) -> ResponseReturnValue:
    """Fiche salle — RGPD : personnel uniquement."""
    if not is_authenticated():
        return redirect(url_for('login_examinateur', salle=id_salle))
    # Supprime les tokens de signature en attente pour cette salle
    # uniquement si c'est le propre examinateur de cette salle qui consulte
    if 'token_emargement' in session:
        session.pop('token_emargement')
    if 'user' in session and session['user'] == id_salle:
        db_update(db_facility_web.DELETE_SALLE_TOKEN_SIGNATURE, id_salle=id_salle)

    donnees_salle = fetch_salle(id_salle)
    if not donnees_salle:
        abort(404, "Cette salle n'est pas dans la liste des salles utilisées")
    students_ine_list = [item['numero'] for item in donnees_salle['oraux']]
    # L'admin peut émarger sur n'importe quelle salle
    is_userpage = is_admin_user() or (session.get('user') == donnees_salle['salle'])
    return render_template(
        "salle.html",
        centre=CENTRE_EXAMEN,
        data=donnees_salle,
        students_numeros=students_ine_list,
        url_of_page=request.url,
        username=get_username(),
        is_userpage=is_userpage,
        digital_sign=DIGITAL_SIGN,
        sse_channel=f"salle_{id_salle}",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Signature dématérialisée
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/sign", methods=['GET', 'POST'])
@nocache
def sign() -> ResponseReturnValue:
    """Signature dématérialisée de l'émargement par l'examinateur (sur place)."""
    form = SignatureExaminateurForm()
    if request.method == 'GET':
        form.link_back.data = request.args.get('link_back', None)
        form.id_oral.data = request.args.get('id', None)
        if form.id_oral.data is None:
            return abort(403)
        if is_admin_user():
            # L'admin peut signer depuis n'importe quelle salle.
            # link_back contient l'id de la salle (pour le retour).
            token_key = 'admin'
        else:
            user = get_username(complete=False)
            if user is None or form.link_back.data != user:
                return abort(403)
            token_key = user
        app.logger.info(f"Signature: {token_key} id_oral={form.id_oral.data}")
        # Usage interne en tant que MAC (pas un mot de passe stocké) — pas
        # d'identifiant de sel par compte ici, l'id_oral fait déjà partie de
        # l'entrée hachée.
        session['token_emargement'] = hash_password(token_key + str(form.id_oral.data), '')
        data = db_get(db_facility_web.SELECT_SIGNATURE_ORAL, form.id_oral.data)
        return render_template("sign.html", centre=CENTRE_EXAMEN, data=data, form=form)

    # POST
    link = form.link_back.data
    id_oral = form.id_oral.data
    stored_token = session.get('token_emargement')
    if is_admin_user():
        token_key = 'admin'
    else:
        token_key = get_username(complete=False)
        if link != token_key:
            session.pop('token_emargement', None)
            return abort(403)
    expected_token = (
        hash_password(token_key + id_oral, '') if token_key and id_oral else None
    )
    if stored_token != expected_token:
        session.pop('token_emargement', None)
        return abort(403)
    session.pop('token_emargement', None)
    image = image_normalize(form.signature_image.data)
    cancel = form.cancel.data
    d = {
        'id': id_oral,
        'signature': image if cancel != "1" else '',
        'heure_signature': (
            datetime.now().astimezone(TIMEZONE).strftime("%d/%m/%Y - %H:%M:%S")
            if cancel != "1" else ''
        ),
    }
    db_update(db_facility_web.UPDATE_SIGNATURE_ORAL, **d)
    clear_outdated_tokens(id_oral)
    return redirect(url_for('salle', id_salle=link))


@app.route('/request-token/<id_oral>')
@nocache
def request_token(id_oral: str) -> ResponseReturnValue:
    """Génère un token de signature à usage unique + QR code (signature sur un autre appareil)."""
    if 'token_emargement' not in session:
        return abort(403)
    token = generate_token(id_oral)
    image = qr(url_for('sign_other_device', token=token, _external=True), scale=5)
    app.logger.info(f"Token: demandé pour signature autre appareil ({_redact_token(token)})")
    return jsonify({'token': token, 'image': image})


@app.route("/sign-other-device/<token>", methods=['GET', 'POST'])
@nocache
def sign_other_device(token: str) -> ResponseReturnValue:
    """Signature depuis un autre appareil via un token à usage unique (sans authentification)."""
    form = SignatureOtherDeviceForm()
    if request.method == 'GET':
        form.token.data = token
        token_infos = db_get(db_facility_web.SELECT_TOKEN_SIGNATURE,
                             token, no_list_auto=False)
        if len(token_infos) != 1:
            return abort(404, "token invalide")
        token_infos = token_infos[0]
        form.id_oral.data = token_infos['oral']
        infos = db_get(db_facility_web.SELECT_SIGNATURE_ORAL, token_infos['oral'])
        return render_template('sign_other_device.html', data=infos, form=form)

    # POST
    id_oral = form.id_oral.data
    if not check_token(token, id_oral):
        return abort(403)
    image = image_normalize(form.signature_image.data)
    cancel = form.cancel.data
    d = {
        'id': id_oral,
        'signature': image if cancel != "1" else '',
        'heure_signature': (
            datetime.now().astimezone(TIMEZONE).strftime("%d/%m/%Y - %H:%M:%S")
            if cancel != "1" else ''
        ),
    }
    db_update(db_facility_web.UPDATE_SIGNATURE_ORAL, **d)
    # Canal dédié au token : seule la page de signature concernée reçoit l'événement
    sse.publish(token, type='sign_done', channel=f"sign_{token}")
    return render_template('sign_other_device_done.html', centre=CENTRE_EXAMEN)


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Authentification admin
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
@nocache
@limiter.limit("10 per minute")
def login() -> ResponseReturnValue:
    """Connexion administrateur par code TOTP."""
    form = LoginAdminForm()
    if request.method == "GET":
        # Flask décode déjà les query params — pas besoin de unquote() supplémentaire
        form.link_back.data = request.args.get("link_back", '')
        return render_template('login.html', centre=CENTRE_EXAMEN,
                               hide_admin=True, username=get_username(), form=form)

    url = _safe_redirect_url(form.link_back.data)
    passcode = form.key.data
    if app._otp.verify(otp=passcode, valid_window=1):  # type: ignore[attr-defined]
        session.clear()
        session['user'] = 'admin'
        session['_ts'] = __import__('time').time()
        _online_set('admin', 'admin')
        app.logger.info("admin: connecté")
        return redirect(url or url_for("index"))

    app.logger.warning("admin: échec connexion (code OTP incorrect)")
    _record_auth_failure("admin", "totp")
    if url:
        return redirect(url_for('login', link_back=quote(url)))
    return redirect(url_for("login"))


@app.route("/logout")
@nocache
def logout() -> ResponseReturnValue:
    """Déconnexion de l'utilisateur courant (admin ou examinateur)."""
    if 'user' in session:
        user = session.pop('user')
        kind = 'admin' if user == 'admin' else 'exam'
        _online_clear(kind, user if user != 'admin' else 'admin')
        app.logger.info(f"{user}: déconnecté")
    url = _safe_redirect_url(request.args.get("link_back"))
    return redirect(url or url_for('index'))


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Authentification candidats (élèves)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/login-candidat', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute")
def login_candidat() -> ResponseReturnValue:
    """Connexion d'un candidat par numéro de candidat + mot de passe du papillon."""
    form = LoginCandidatForm()
    if request.method == 'GET':
        message = request.args.get('message', None)
        # Numéro récupéré depuis Redis (usage unique, TTL 5 min)
        _tok = session.pop('_login_tok', '')
        if _tok:
            _r = _get_redis_pub_for_ine()
            _raw = _r.getdel(f"login_ine:{_tok}")
            numero = _raw.decode('utf-8') if _raw else ''
        else:
            numero = request.args.get('numero', '')
        if numero:
            form.numero.data = numero
        return render_template('login_candidat.html', centre=CENTRE_EXAMEN,
                               form=form, message=message)

    numero = form.numero.data.strip() if form.numero.data else ''
    password = form.password.data.strip() if form.password.data else ''
    candidat_info = db_get(db_facility_web.SELECT_CANDIDAT_AUTH,
                           numero, no_list_auto=False)
    if (len(candidat_info) == 1
            and check_password(password, numero, candidat_info[0]['password_hash'])):
        session.clear()
        session['candidat'] = numero
        session['_ts'] = __import__('time').time()
        _online_set('cand', numero)
        app.logger.info(f"Candidat {numero}: connecté")
        return redirect(url_for('candidat', id_candidat=numero))
    app.logger.warning(f"Candidat {numero}: échec connexion")
    _record_auth_failure("candidat", numero)
    return redirect(url_for('login_candidat', message='Numéro ou mot de passe incorrect'))


@app.route('/logout-candidat')
@nocache
def logout_candidat() -> ResponseReturnValue:
    """Déconnexion d'un candidat."""
    if 'candidat' in session:
        numero = session.pop('candidat')
        _online_clear('cand', numero)
        app.logger.info(f"Candidat {numero}: déconnecté")
    return redirect(url_for('index'))


# ── Authentification loges ──────────────────────────────────────────────────

@app.route('/login-loge', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute")
def login_loge() -> ResponseReturnValue:
    """Connexion d'un surveillant de loge."""
    form = LoginLogeForm()
    liste_loges = db_get(db_facility_web.SELECT_LISTE_LOGES, no_list_auto=False)
    form.loge.choices = [(lo['salle'], lo['salle']) for lo in liste_loges]

    if request.method == 'GET':
        loge_param = request.args.get('loge')
        message = request.args.get('message')
        form.loge.data = loge_param
        return render_template(
            'login_loge.html',
            centre=CENTRE_EXAMEN,
            form=form,
            message=message,
        )

    # POST
    loge_nom = form.loge.data
    passwd = form.password.data
    infos = db_get(db_facility_web.SELECT_PASSWORD_CHECK_LOGE,
                   loge_nom, no_list_auto=False)
    if len(infos) == 1 and check_password(passwd, loge_nom, infos[0]['password_hash']):
        session.clear()
        session['loge'] = loge_nom
        session['_ts'] = __import__('time').time()
        _online_set('loge', loge_nom)
        app.logger.info(f"Loge {loge_nom}: connectée")
        return redirect(url_for('loge', id_loge=loge_nom))
    app.logger.warning(f"Loge {loge_nom}: échec connexion")
    _record_auth_failure("loge", loge_nom)
    return redirect(url_for('login_loge', loge=loge_nom,
                             message='Mot de passe incorrect'))


@app.route('/logout-loge')
@nocache
def logout_loge() -> ResponseReturnValue:
    """Déconnexion d'un surveillant de loge."""
    if 'loge' in session:
        loge_nom = session.pop('loge')
        _online_clear('loge', loge_nom)
        app.logger.info(f"Loge {loge_nom}: déconnectée")
    return redirect(url_for('index'))


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Administration (admin uniquement)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/gestion/reload-pages', methods=['POST'])
@admin_required
@nocache
def reload_pages() -> ResponseReturnValue:
    """Force le rechargement de toutes les pages ouvertes (notification SSE)."""
    # #2 — Action mutante : POST + CSRF (un GET aurait été déclenchable par un
    # simple lien, contournant SameSite=Lax sur une navigation top-level).
    link = _safe_redirect_url(request.form.get("link_back"))
    sse.publish(" ", type='reload_page', channel='general')
    app.logger.info("SSE: rechargement de toutes les pages")
    if link is None:
        return "ok", 200
    return redirect(link)


@app.route("/gestion")
@admin_required
@nocache
def index_gestion() -> ResponseReturnValue:
    """Page d'accueil de la gestion : liste des oraux et accès aux outils admin."""
    oraux = db_get(db_facility_web.SELECT_LISTE_ORAUX, no_list_auto=False)
    return render_template(
        "index_gestion.html",
        centre=CENTRE_EXAMEN,
        oraux=oraux,
        url_of_page=request.url,
        authenticated=is_authenticated(),
        username=get_username(),
    )


@app.route('/gestion/liste-examinateurs-json', methods=['GET'])
@admin_required
@nocache
def liste_examinateurs_json() -> ResponseReturnValue:
    """Liste JSON des examinateurs d'une matière (pour le formulaire d'édition d'oral)."""
    matiere = request.args.get('matiere', -1)
    if matiere == -1:
        abort(400)
    liste = db_get(db_facility_web.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE,
                   matiere, no_list_auto=False)
    return jsonify(liste)


def _time_str_to_td(s: str) -> timedelta:
    """Convertit une chaîne HH:MM (formulaire) en timedelta."""
    h, m = s.split(':')[:2]
    return timedelta(hours=int(h), minutes=int(m))


def _td_to_time_str(td: timedelta) -> str:
    """Convertit un timedelta en chaîne HH:MM (colonne TIME)."""
    total_seconds = int(td.total_seconds())
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}"


def _to_td(val: object) -> timedelta:
    """Normalise une valeur TIME en timedelta.

    mysql-connector-python peut retourner les champs TIME soit en timedelta
    (comportement standard) soit en str HH:MM:SS selon la version et le driver.
    Cette fonction gère les deux cas.
    """
    if isinstance(val, timedelta):
        return val
    parts = str(val).split(':')
    h, m = int(parts[0]), int(parts[1])
    s = int(float(parts[2])) if len(parts) > 2 else 0
    return timedelta(hours=h, minutes=m, seconds=s)


def _check_conflits_oral(
    id_oral: int,
    id_candidat: int,
    id_examinateur: int,
    sujet_new: timedelta,
    fin_new: timedelta,
    oral_new: timedelta,
    fin_exam_new: timedelta,
    ecart_mini: int,
) -> tuple[str | None, str | None]:
    """Vérifie les conflits horaires lors du déplacement d'un oral.

    Vérifications dans l'ordre :
    - Chevauchement candidat (bloquant) : basé sur [heure_sujet, heure_fin] — le slot
      complet du candidat, incluant préparation et oral.
    - Écart minimum candidat (avertissement) : écart entre heure_sujet des deux oraux.
    - Chevauchement examinateur (bloquant) : basé sur [heure_oral, heure_fin] seulement —
      la préparation se déroule en loge, pas avec l'examinateur.

    Retourne (erreur_bloquante, avertissement) — au plus un des deux est non-None.
    """
    # ── Candidat (slot complet : heure_sujet → heure_fin) ─────────────────────
    oraux_candidat = db_get(
        db_facility_web.SELECT_LISTE_EDITION_ORAL,
        id_candidat,
        no_list_auto=False,
    )
    for o in oraux_candidat:
        if o['id'] == id_oral:
            continue
        o_debut = _to_td(o['heure_sujet'])
        o_fin   = _to_td(o['heure_fin'])
        if sujet_new < o_fin and fin_new > o_debut:
            return (
                f"Chevauchement candidat avec l'oral de {o['matiere']} "
                f"({o_debut} – {o_fin})",
                None,
            )
        gap_min = abs((sujet_new - o_debut).total_seconds()) / 60
        if gap_min < ecart_mini:
            return (
                None,
                f"Écart insuffisant avec l'oral de {o['matiere']} "
                f"({gap_min:.0f} min < {ecart_mini} min requis)",
            )

    # ── Examinateur (oral seul : heure_oral → heure_fin) ──────────────────────
    oraux_exam = db_get(
        db_facility_web.SELECT_ORAUX_EXAMINATEUR_CONFLITS,
        id_examinateur,
        no_list_auto=False,
    )
    for o in oraux_exam:
        if o['id'] == id_oral:
            continue
        o_oral = _to_td(o['heure_oral'])
        o_fin  = _to_td(o['heure_fin'])
        if oral_new < o_fin and fin_exam_new > o_oral:
            return (
                f"Chevauchement examinateur : {o['candidat']} "
                f"déjà en oral de {o_oral} à {o_fin}",
                None,
            )

    return None, None


def _appliquer_changement_oral(d: dict, numero: str | None) -> None:
    """
    Persiste un changement d'oral (horaires et/ou examinateur) et publie les
    notifications SSE ciblées — logique partagée entre l'édition manuelle
    (`edit_oral`) et le rééquilibrage automatique (`disponibilite_examinateur`),
    pour que les deux voies déclenchent exactement le même comportement côté
    candidat/salle/loge.

    :param d: dict avec les clés id/examinateur/heure_sujet/heure_oral/heure_fin/mis_a_jour
    :param numero: numéro du candidat concerné (pour les canaux ciblés)
    """
    db_update(db_facility_web.UPDATE_INFOS_ORAL, **d)
    if d['mis_a_jour'] != 1:
        return
    # Récupère salle et loge pour publier sur les canaux ciblés
    exam = db_get(
        db_facility_web.SELECT_SALLE_LOGE_FROM_EXAMINATEUR,
        d['examinateur'],
        no_list_auto=False,
    )
    # #3 — Le canal 'general' est ouvert à tous les utilisateurs
    # authentifiés (y compris candidats et loges) : ne jamais y
    # diffuser de donnée personnelle (numéro de candidat). Les destinataires
    # légitimes sont notifiés via les canaux ciblés ci-dessous, qui
    # sont désormais soumis à autorisation (cf. _sse_channel_allowed).
    sse.publish(data='', type="data_updated", channel='general')
    if exam:
        sse.publish(data=numero, type="data_updated",
                    channel=f"salle_{exam[0]['salle']}")
        sse.publish(data=numero, type="data_updated",
                    channel=f"loge_{exam[0]['loge']}")
    sse.publish(data=numero, type="data_updated",
                channel=f"candidat_{numero}")


@app.route("/gestion/edit-oral", methods=["GET", "POST"])
@admin_required
@nocache
def edit_oral() -> ResponseReturnValue:
    """Édition d'un oral (horaires, examinateur) — admin uniquement."""
    if request.method == "POST":
        d = {
            'id': request.form.get('id'),
            'examinateur': request.form.get('examinateur'),
            'heure_sujet': request.form.get('heure_sujet'),
            'mis_a_jour': 1 if request.form.get('mis_a_jour') == 'on' else 0,
        }
        numero = request.form.get('numero')

        # Validation des conflits horaires
        oral_actuel = db_get(db_facility_web.SELECT_INFOS_ORAL, d['id'])
        heure_sujet_str: str = request.form.get('heure_sujet') or ''
        id_oral_int: int = int(request.form.get('id') or 0)
        sujet_new = _time_str_to_td(heure_sujet_str)
        # Durée de préparation (sujet → oral) : préservée pour recalculer heure_oral
        duree_prep = _to_td(oral_actuel['heure_oral']) - _to_td(oral_actuel['heure_sujet'])
        oral_new   = sujet_new + duree_prep
        # Durée totale du slot (sujet → fin) : pour la vérification candidat
        duree_slot = _to_td(oral_actuel['heure_fin']) - _to_td(oral_actuel['heure_sujet'])
        fin_new    = sujet_new + duree_slot
        # Durée de l'oral seul (oral → fin) : pour la vérification examinateur
        duree_oral    = _to_td(oral_actuel['heure_fin']) - _to_td(oral_actuel['heure_oral'])
        fin_exam_new  = oral_new + duree_oral
        # heure_oral et heure_fin recalculées pour préserver les durées d'origine
        d['heure_oral'] = _td_to_time_str(oral_new)
        d['heure_fin'] = _td_to_time_str(fin_exam_new)
        id_examinateur_int: int = int(request.form.get('examinateur') or 0)
        error_msg, warning_msg = _check_conflits_oral(
            id_oral_int,
            oral_actuel['id_candidat'],
            id_examinateur_int,
            sujet_new,
            fin_new,
            oral_new,
            fin_exam_new,
            _load_algo_params()['ecart_mini'],
        )
        if error_msg or (warning_msg and request.form.get('force') != '1'):
            donnees_oral = dict(oral_actuel)
            donnees_oral['heure_sujet'] = d['heure_sujet']
            donnees_oral['heure_oral'] = d['heure_oral']
            donnees_oral['heure_fin'] = d['heure_fin']
            donnees_oral['id_matiere'] = (
                request.form.get('matiere') or donnees_oral['id_matiere']
            )
            donnees_oral['id_examinateur'] = (
                int(d['examinateur']) if d['examinateur'] else donnees_oral['id_examinateur']
            )
            liste_oraux = db_get(
                db_facility_web.SELECT_LISTE_EDITION_ORAL,
                donnees_oral['id_candidat'], no_list_auto=False,
            )
            liste_matieres = db_get(
                db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False,
            )
            liste_examinateurs = db_get(
                db_facility_web.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE,
                donnees_oral['id_matiere'], no_list_auto=False,
            )
            url = _safe_redirect_url(request.form.get("link_back"))
            status = 422 if error_msg else 200
            return render_template(
                "edit_oral.html",
                centre=CENTRE_EXAMEN,
                donnees_oral=donnees_oral,
                liste_oraux=liste_oraux,
                matieres=liste_matieres,
                examinateurs=liste_examinateurs,
                username=get_username(),
                url_of_page=request.url,
                link_back=url,
                authenticated=is_authenticated(),
                error_msg=error_msg,
                warning_msg=warning_msg,
            ), status

        _appliquer_changement_oral(d, numero)
        url = _safe_redirect_url(request.form.get("link_back"))
        return redirect(url or url_for('index_gestion', _anchor=str(d['id'])))

    # GET
    id_oral = request.args.get('oral', None)
    if id_oral is None:
        abort(404, "Pas d'oral avec ce numéro")
    donnees_oral = db_get(db_facility_web.SELECT_INFOS_ORAL, id_oral)
    liste_oraux = db_get(
        db_facility_web.SELECT_LISTE_EDITION_ORAL,
        donnees_oral['id_candidat'], no_list_auto=False,
    )
    liste_matieres = db_get(db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False)
    liste_examinateurs = db_get(
        db_facility_web.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE,
        donnees_oral['id_matiere'], no_list_auto=False,
    )
    url = _safe_redirect_url(request.args.get("link_back"))
    return render_template(
        "edit_oral.html",
        centre=CENTRE_EXAMEN,
        donnees_oral=donnees_oral,
        liste_oraux=liste_oraux,
        matieres=liste_matieres,
        examinateurs=liste_examinateurs,
        username=get_username(),
        url_of_page=request.url,
        link_back=url,
        authenticated=is_authenticated(),
    )


@app.route('/gestion/liste-examinateurs')
@admin_required
@nocache
def liste_examinateurs() -> ResponseReturnValue:
    """Liste des examinateurs avec leurs salles, loges et nombre d'oraux."""
    examinateurs = db_get(db_facility_web.SELECT_LISTE_EXAMINATEURS, no_list_auto=False)
    _raw = request.args.get('new_papillon', '')
    # Valider le nom de fichier (même règle que la route /download — anti path-traversal)
    new_papillon = _raw if re.match(r'^[\w\-. ]+\.pdf$', _raw) else ''
    return render_template(
        'liste_examinateurs.html',
        centre=CENTRE_EXAMEN,
        examinateurs=examinateurs,
        admin=is_admin_user(),
        url_of_page=request.url,
        username=get_username(),
        new_papillon=new_papillon,
    )


@app.route('/gestion/liste-candidats')
@admin_required
@nocache
def liste_candidats() -> ResponseReturnValue:
    """Liste des candidats avec accès à l'édition de leurs informations."""
    candidats = db_get(db_facility_web.SELECT_ALL_CANDIDATS, no_list_auto=False)
    _raw = request.args.get('new_papillon', '')
    # Valider le nom de fichier (même règle que la route /download — anti path-traversal)
    new_papillon = _raw if re.match(r'^[\w\-. ]+\.pdf$', _raw) else ''
    return render_template(
        'liste_candidats.html',
        centre=CENTRE_EXAMEN,
        candidats=candidats,
        url_of_page=request.url,
        username=get_username(),
        authenticated=is_authenticated(),
        new_papillon=new_papillon,
    )


@app.route('/gestion/edit-candidat', methods=['GET', 'POST'])
@admin_required
@nocache
def edit_candidat() -> ResponseReturnValue:
    """Édition des informations d'un candidat (nom, numéro, tiers temps)."""
    if request.method == 'POST':
        d = {
            'id': request.form.get('id'),
            'nom': (request.form.get('nom') or '').strip(),
            'numero': (request.form.get('numero') or '').strip(),
            'tiers_temps': 1 if request.form.get('tiers_temps') == 'on' else 0,
        }
        if not d['nom'] or not d['numero']:
            abort(400, "Nom et numéro sont obligatoires")
        db_update(db_facility_web.UPDATE_CANDIDAT_INFOS, **d)
        url = _safe_redirect_url(request.form.get('link_back'))
        return redirect(url or url_for('liste_candidats'))

    # GET
    id_candidat = request.args.get('id', None)
    if id_candidat is None:
        abort(404, "Pas de candidat avec cet identifiant")
    donnees_candidat = db_get(db_facility_web.SELECT_INFOS_CANDIDAT_BY_ID, id_candidat)
    if donnees_candidat is None:
        abort(404, "Candidat introuvable")
    url = _safe_redirect_url(request.args.get('link_back'))
    return render_template(
        'edit_candidat.html',
        centre=CENTRE_EXAMEN,
        donnees_candidat=donnees_candidat,
        url_of_page=request.url,
        link_back=url,
        username=get_username(),
        authenticated=is_authenticated(),
    )


@app.route("/gestion/edit-examinateur", methods=['GET', 'POST'])
@admin_required
@nocache
def edit_examinateur() -> ResponseReturnValue:
    """Édition des informations d'un examinateur (nom, salle, loge, établissements)."""
    if request.method == "POST":
        d = {
            'id': request.form.get('id'),
            'nom': request.form.get('nom'),
            'salle': request.form.get('salle'),
            'loge': request.form.get('loge'),
            'etablissements': ','.join(request.form.getlist('etablissements')),
        }
        db_update(db_facility_web.UPDATE_EXAMINATEUR_INFOS, **d)
        url = _safe_redirect_url(request.form.get('link_back'))
        return redirect(url or url_for('liste_examinateurs'))

    # GET
    id_examinateur = request.args.get('id_examinateur', None)
    if id_examinateur is None:
        abort(404, "Pas d'examinateur avec ce numéro")
    url = _safe_redirect_url(request.args.get('link_back'))
    donnees_examinateur = db_get(db_facility_web.SELECT_EXAMINATEUR_INFOS, id_examinateur)
    liste_oraux = db_get(
        db_facility_web.SELECT_ORAUX_EXAMINATEUR_CONFLITS, id_examinateur, no_list_auto=False
    )
    etablissements_actuels = set(
        e.strip() for e in (donnees_examinateur.get('etablissements') or '').split(',') if e.strip()
    )
    return render_template(
        "edit_examinateur.html",
        centre=CENTRE_EXAMEN,
        donnees_examinateur=donnees_examinateur,
        etablissements_actuels=etablissements_actuels,
        liste_lycees=_LYCEES_DISPLAY,
        liste_oraux=liste_oraux,
        url_of_page=request.url,
        link_back=url,
        username=get_username(),
    )


def _charger_profs_a_eviter() -> dict[str, list[str]]:
    """Charge le mapping numero -> profs à éviter depuis data/candidats.csv.

    Cette information n'est pas persistée en base (seulement dans le CSV
    source consommé par algo.py) : nécessaire pour respecter la même règle
    d'exclusion lors d'un rééquilibrage en cours de journée.
    """
    from csv_validator import normalize_csv_file
    chemin = _DATA_DIR / "candidats.csv"
    if not chemin.exists():
        return {}
    try:
        rows, _ = normalize_csv_file(chemin)
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    resultat: dict[str, list[str]] = {}
    for row in rows:
        candidat_str = row.get("CANDIDAT", "")
        if "(" not in candidat_str:
            continue
        numero = candidat_str.split("(")[1][:-1].strip()
        resultat[numero] = [p.strip() for p in (row.get("Profs") or "").split(",")]
    return resultat


def _oral_actuel_depuis_ligne(ligne: dict) -> rebalance.OralActuel:
    return rebalance.OralActuel(
        id=ligne['id'], id_candidat=ligne['id_candidat'], numero=ligne['numero'],
        etablissement=ligne['etablissement'] or '', id_examinateur=ligne['id_examinateur'],
        examinateur_nom=ligne['examinateur'],
        heure_sujet=_to_td(ligne['heure_sujet']), heure_oral=_to_td(ligne['heure_oral']),
        heure_fin=_to_td(ligne['heure_fin']),
    )


def _pause_meridienne_params() -> tuple[timedelta | None, timedelta]:
    """Lit la pause méridienne configurée (`/gestion/algo`) et la convertit
    en (heure_pause_meridienne, duree_pause_meridienne) — None si désactivée."""
    algo_params = _load_algo_params()
    pause_debut_str = str(algo_params.get('pause_meridienne_debut', '') or '')
    heure_pause_meridienne = _time_str_to_td(pause_debut_str) if pause_debut_str else None
    duree_pause_meridienne = timedelta(minutes=algo_params.get('pause_meridienne_duree', 0))
    return heure_pause_meridienne, duree_pause_meridienne


def _construire_contexte_disponibilite(info_examinateur: dict) -> dict:
    """
    Charge une seule fois les données nécessaires au calcul du plan de
    rééquilibrage ET, le cas échéant, à la résolution poussée (palier 2/3) —
    évite de refaire les mêmes requêtes deux fois.

    :param info_examinateur: résultat de SELECT_EXAMINATEUR_MATIERE
    """
    id_matiere = info_examinateur['id_matiere']

    lignes = db_get(db_facility_web.SELECT_ORAUX_MATIERE_DU_JOUR, id_matiere, no_list_auto=False)
    oraux = [_oral_actuel_depuis_ligne(l) for l in lignes]
    grille_horaires = [o.heure_sujet for o in oraux]

    examinateurs_lignes = db_get(
        db_facility_web.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE, id_matiere, no_list_auto=False,
    )
    examinateurs_par_id = {
        e['id']: rebalance.ExaminateurCible(
            id=e['id'], nom=e['nom'],
            etablissements=[x.strip() for x in (e['etablissements'] or '').split(',')],
        )
        for e in examinateurs_lignes
    }

    profs_a_eviter = _charger_profs_a_eviter()

    # Heure de l'AUTRE oral (fixe) de chaque candidat concerné, pour l'écart minimum.
    autres_heures_sujet: dict[int, timedelta | None] = {}
    for id_candidat in {o.id_candidat for o in oraux}:
        autres = db_get(db_facility_web.SELECT_LISTE_EDITION_ORAL, id_candidat, no_list_auto=False)
        autre = next((a for a in autres if a['matiere'] != info_examinateur['matiere']), None)
        autres_heures_sujet[id_candidat] = _to_td(autre['heure_sujet']) if autre else None

    heure_pause_meridienne, duree_pause_meridienne = _pause_meridienne_params()

    return {
        'oraux': oraux,
        'grille_horaires': grille_horaires,
        'examinateurs_par_id': examinateurs_par_id,
        'profs_a_eviter': profs_a_eviter,
        'autres_heures_sujet': autres_heures_sujet,
        'ecart_mini_minutes': _load_algo_params()['ecart_mini'],
        'heure_pause_meridienne': heure_pause_meridienne,
        'duree_pause_meridienne': duree_pause_meridienne,
    }


def _calculer_plan_disponibilite(
    id_examinateur: int,
    ctx: dict,
    indispo_debut: timedelta | None,
    dispo_retour: timedelta | None,
) -> rebalance.PlanRebalancement:
    """
    Calcule le plan de rééquilibrage (palier 1, glouton) pour un changement
    de disponibilité.

    Un retard (absent jusqu'à une heure puis de retour) combine les deux
    volets : `planifier_absence` sur la fenêtre [indispo_debut, dispo_retour),
    puis `planifier_renfort` sur la fenêtre [dispo_retour, fin] en tenant
    compte des changements déjà décidés par le premier volet.

    Hypothèse (limite volontaire de cette première version) : un seul
    examinateur change de disponibilité à la fois — pas de gestion
    d'absences/renforts simultanés sur la même matière.

    :param ctx: contexte construit par `_construire_contexte_disponibilite`
    """
    oraux = ctx['oraux']
    grille_horaires = ctx['grille_horaires']
    examinateurs_par_id = ctx['examinateurs_par_id']
    profs_a_eviter = ctx['profs_a_eviter']
    autres_heures_sujet = ctx['autres_heures_sujet']
    ecart_mini_minutes = ctx['ecart_mini_minutes']
    heure_pause_meridienne = ctx['heure_pause_meridienne']
    duree_pause_meridienne = ctx['duree_pause_meridienne']

    plan = rebalance.PlanRebalancement()
    borne_fin = dispo_retour if dispo_retour is not None else timedelta(hours=23, minutes=59)

    if indispo_debut is not None:
        oraux_a_reaffecter = [
            o for o in oraux
            if o.id_examinateur == id_examinateur and indispo_debut <= o.heure_sujet < borne_fin
        ]
        ids_a_reaffecter = {o.id for o in oraux_a_reaffecter}
        occupations: dict[int, list[tuple[timedelta, timedelta]]] = {}
        for o in oraux:
            if o.id in ids_a_reaffecter:
                continue
            occupations.setdefault(o.id_examinateur, []).append((o.heure_oral, o.heure_fin))
        examinateurs_disponibles = [
            e for e in examinateurs_par_id.values() if e.id != id_examinateur
        ]
        plan.etendre(rebalance.planifier_absence(
            oraux_a_reaffecter, examinateurs_disponibles, occupations, grille_horaires,
            autres_heures_sujet, ecart_mini_minutes, profs_a_eviter,
            heure_pause_meridienne, duree_pause_meridienne,
        ))

    if dispo_retour is not None:
        deja_deplaces_ids = {c.id_oral for c in plan.changements}
        occupations2: dict[int, list[tuple[timedelta, timedelta]]] = {}
        for o in oraux:
            if o.id in deja_deplaces_ids:
                continue
            occupations2.setdefault(o.id_examinateur, []).append((o.heure_oral, o.heure_fin))
        for c in plan.changements:
            occupations2.setdefault(c.nouvel_examinateur_id, []).append(
                (c.nouvelle_heure_oral, c.nouvelle_heure_fin)
            )

        oraux_deplacables = [
            o for o in oraux
            if o.id_examinateur != id_examinateur
            and o.id not in deja_deplaces_ids
            and o.heure_sujet >= dispo_retour
        ]
        charge_par_examinateur: dict[int, int] = {id_examinateur: 0}
        for o in oraux_deplacables:
            charge_par_examinateur[o.id_examinateur] = (
                charge_par_examinateur.get(o.id_examinateur, 0) + 1
            )

        plan.etendre(rebalance.planifier_renfort(
            oraux_deplacables, examinateurs_par_id[id_examinateur], occupations2, grille_horaires,
            autres_heures_sujet, ecart_mini_minutes, profs_a_eviter, charge_par_examinateur,
            heure_pause_meridienne, duree_pause_meridienne,
        ))

    return plan


def _tenter_resolution_poussee(
    id_examinateur: int,
    ctx: dict,
    plan: rebalance.PlanRebalancement,
    etendre: bool,
) -> rebalance.PlanRebalancement:
    """
    Palier 2 (etendre=False) ou palier 3 (etendre=True) : ré-essaie par
    CP-SAT les oraux que le glouton (palier 1) n'a pas su replacer
    (`plan.non_replaces`), en tenant compte des changements déjà décidés.

    Ne concerne que les oraux issus de `planifier_absence` — `planifier_renfort`
    n'alimente jamais `non_replaces` (ses déplacements sont optionnels).
    """
    if not plan.non_replaces:
        return plan

    oraux = ctx['oraux']
    examinateurs_disponibles = [
        e for e in ctx['examinateurs_par_id'].values() if e.id != id_examinateur
    ]
    ids_a_resoudre = {o.id for o in plan.non_replaces}
    deja_deplaces_ids = {c.id_oral for c in plan.changements}

    occupations: dict[int, list[tuple[timedelta, timedelta]]] = {}
    for o in oraux:
        if o.id in ids_a_resoudre or o.id in deja_deplaces_ids:
            continue
        occupations.setdefault(o.id_examinateur, []).append((o.heure_oral, o.heure_fin))
    for c in plan.changements:
        occupations.setdefault(c.nouvel_examinateur_id, []).append(
            (c.nouvelle_heure_oral, c.nouvelle_heure_fin)
        )

    grille_initiale = ctx['grille_horaires']
    grille = grille_initiale
    if etendre:
        duree = rebalance.duree_creneau_estimee(grille_initiale, plan.non_replaces)
        grille = rebalance.construire_grille_etendue(
            grille_initiale, duree,
            heure_pause_meridienne=ctx['heure_pause_meridienne'],
            duree_pause_meridienne=ctx['duree_pause_meridienne'],
        )

    resultat = rebalance.resoudre_oraux_difficiles(
        plan.non_replaces, examinateurs_disponibles, occupations, grille,
        ctx['autres_heures_sujet'], ctx['ecart_mini_minutes'], ctx['profs_a_eviter'],
        grille_initiale=grille_initiale,
        heure_pause_meridienne=ctx['heure_pause_meridienne'],
        duree_pause_meridienne=ctx['duree_pause_meridienne'],
    )

    return rebalance.PlanRebalancement(
        changements=plan.changements + resultat.changements,
        non_replaces=resultat.non_replaces,
    )


@app.route("/gestion/examinateur/disponibilite", methods=["GET", "POST"])
@admin_required
@nocache
def disponibilite_examinateur() -> ResponseReturnValue:
    """
    Rééquilibrage des oraux d'une matière suite à un changement de
    disponibilité d'un examinateur (absence, retard, renfort), à partir
    d'heures réglables — cf. docs/workflow_admin.md.
    """
    id_examinateur = request.values.get("id_examinateur", type=int)
    if id_examinateur is None:
        abort(404, "Pas d'examinateur avec ce numéro")
    info = db_get(db_facility_web.SELECT_EXAMINATEUR_MATIERE, id_examinateur)
    if not info:
        abort(404, "Examinateur introuvable")

    if request.method == "GET":
        return render_template(
            "disponibilite_examinateur.html",
            centre=CENTRE_EXAMEN,
            examinateur=info,
            etape="saisie",
            url_of_page=request.url,
            username=get_username(),
            authenticated=is_authenticated(),
        )

    # POST
    indispo_str = (request.form.get("indisponible_a_partir_de") or "").strip()
    retour_str = (request.form.get("disponible_a_nouveau_a_partir_de") or "").strip()
    indispo_debut = _time_str_to_td(indispo_str) if indispo_str else None
    dispo_retour = _time_str_to_td(retour_str) if retour_str else None

    if indispo_debut is None and dispo_retour is None:
        return render_template(
            "disponibilite_examinateur.html",
            centre=CENTRE_EXAMEN,
            examinateur=info,
            etape="saisie",
            url_of_page=request.url,
            username=get_username(),
            authenticated=is_authenticated(),
            error_msg="Renseignez au moins l'une des deux heures.",
        ), 400

    ctx = _construire_contexte_disponibilite(info)
    plan = _calculer_plan_disponibilite(id_examinateur, ctx, indispo_debut, dispo_retour)
    etape = request.form.get("etape", "previsualisation")

    # Le niveau de résolution atteint (glouton seul / +même grille / +extension)
    # est reporté d'une requête à l'autre via un champ caché du formulaire, pour
    # que la confirmation rejoue exactement la même résolution que la dernière
    # prévisualisation affichée — sans ce rejeu, "Confirmer" ne réappliquerait
    # que le plan glouton (palier 1) et perdrait silencieusement les oraux
    # replacés uniquement grâce aux paliers 2/3 (bug corrigé ici).
    niveau_resolution = request.form.get("niveau_resolution", "glouton")
    if etape == "resolution_poussee_grille":
        niveau_resolution = "grille"
    elif etape == "resolution_poussee_etendue":
        niveau_resolution = "etendue"

    if niveau_resolution in ("grille", "etendue"):
        plan = _tenter_resolution_poussee(
            id_examinateur, ctx, plan, etendre=(niveau_resolution == "etendue"),
        )

    if etape in ("resolution_poussee_grille", "resolution_poussee_etendue"):
        etape = "previsualisation"

    if etape == "confirmer":
        for changement in plan.changements:
            d = {
                'id': changement.id_oral,
                'examinateur': changement.nouvel_examinateur_id,
                'heure_sujet': _td_to_time_str(changement.nouvelle_heure_sujet),
                'heure_oral': _td_to_time_str(changement.nouvelle_heure_oral),
                'heure_fin': _td_to_time_str(changement.nouvelle_heure_fin),
                'mis_a_jour': 1,
            }
            _appliquer_changement_oral(d, changement.numero)
        return render_template(
            "disponibilite_examinateur.html",
            centre=CENTRE_EXAMEN,
            examinateur=info,
            etape="termine",
            plan=plan,
            url_of_page=request.url,
            username=get_username(),
            authenticated=is_authenticated(),
        )

    return render_template(
        "disponibilite_examinateur.html",
        centre=CENTRE_EXAMEN,
        examinateur=info,
        etape="previsualisation",
        plan=plan,
        niveau_resolution=niveau_resolution,
        indisponible_a_partir_de=indispo_str,
        disponible_a_nouveau_a_partir_de=retour_str,
        url_of_page=request.url,
        username=get_username(),
        authenticated=is_authenticated(),
    )


def _notifier_salle_loge_examinateur(id_examinateur: int, numero: str | None) -> None:
    """Publie une notification SSE ciblée pour la salle/loge d'un examinateur
    donné, sans mise à jour d'oral associée — utilisé pour avertir l'ANCIEN
    examinateur d'un candidat qui change de matière (contrairement à la
    disponibilité examinateur, ici l'ancien examinateur n'est pas à l'origine
    du changement et ne le sait pas déjà)."""
    exam = db_get(
        db_facility_web.SELECT_SALLE_LOGE_FROM_EXAMINATEUR, id_examinateur, no_list_auto=False,
    )
    if exam:
        sse.publish(data=numero, type="data_updated", channel=f"salle_{exam[0]['salle']}")
        sse.publish(data=numero, type="data_updated", channel=f"loge_{exam[0]['loge']}")


def _calculer_plan_changement_matiere(
    id_candidat: int,
    oral_actuel: rebalance.OralActuel,
    id_nouvelle_matiere: int,
    nom_nouvelle_matiere: str,
) -> tuple[rebalance.Changement | None, dict]:
    """
    Calcule le remplacement (palier 1, glouton) de l'oral d'un candidat qui
    change de matière — même heure d'abord, repli sur la grille sinon.

    :return: (changement_ou_None, ctx) — `ctx` est le contexte de la NOUVELLE
        matière, réutilisable pour la résolution poussée en cas d'échec.
    """
    ctx = _construire_contexte_disponibilite(
        {'id_matiere': id_nouvelle_matiere, 'matiere': nom_nouvelle_matiere},
    )

    occupations: dict[int, list[tuple[timedelta, timedelta]]] = {}
    for o in ctx['oraux']:
        occupations.setdefault(o.id_examinateur, []).append((o.heure_oral, o.heure_fin))

    autres = db_get(db_facility_web.SELECT_LISTE_EDITION_ORAL, id_candidat, no_list_auto=False)
    autre = next((a for a in autres if a['id'] != oral_actuel.id), None)
    autre_heure_sujet = _to_td(autre['heure_sujet']) if autre else None

    examinateurs_disponibles = list(ctx['examinateurs_par_id'].values())
    changement = rebalance.planifier_changement_matiere(
        oral_actuel, examinateurs_disponibles, occupations, ctx['grille_horaires'],
        autre_heure_sujet, ctx['ecart_mini_minutes'], ctx['profs_a_eviter'],
        ctx['heure_pause_meridienne'], ctx['duree_pause_meridienne'],
    )
    ctx['occupations'] = occupations
    ctx['autre_heure_sujet'] = autre_heure_sujet
    return changement, ctx


def _tenter_resolution_poussee_matiere(
    oral_actuel: rebalance.OralActuel, ctx: dict, etendre: bool,
) -> rebalance.Changement | None:
    """Paliers 2/3 pour le changement de matière — même principe que
    `_tenter_resolution_poussee`, mais pour un seul oral à replacer."""
    examinateurs = list(ctx['examinateurs_par_id'].values())
    grille_initiale = ctx['grille_horaires']
    grille = grille_initiale
    if etendre:
        duree = rebalance.duree_creneau_estimee(grille_initiale, [oral_actuel])
        grille = rebalance.construire_grille_etendue(
            grille_initiale, duree,
            heure_pause_meridienne=ctx['heure_pause_meridienne'],
            duree_pause_meridienne=ctx['duree_pause_meridienne'],
        )
    resultat = rebalance.resoudre_oraux_difficiles(
        [oral_actuel], examinateurs, ctx['occupations'], grille,
        {oral_actuel.id_candidat: ctx['autre_heure_sujet']}, ctx['ecart_mini_minutes'],
        ctx['profs_a_eviter'], grille_initiale=grille_initiale,
        heure_pause_meridienne=ctx['heure_pause_meridienne'],
        duree_pause_meridienne=ctx['duree_pause_meridienne'],
    )
    return resultat.changements[0] if resultat.changements else None


def _proposer_compaction(
    oral_actuel: rebalance.OralActuel, id_ancienne_matiere: int,
) -> rebalance.Changement | None:
    """
    Suggestion optionnelle (jamais appliquée automatiquement) : une fois le
    créneau de `oral_actuel` libéré chez son examinateur (l'ancienne matière),
    propose de déplacer l'oral le plus tardif de ce même examinateur dans ce
    créneau — compacte son planning et libère du temps en fin de journée.
    """
    lignes = db_get(
        db_facility_web.SELECT_ORAUX_MATIERE_DU_JOUR, id_ancienne_matiere, no_list_auto=False,
    )
    oraux_meme_examinateur = [
        _oral_actuel_depuis_ligne(l) for l in lignes
        if l['id_examinateur'] == oral_actuel.id_examinateur and l['id'] != oral_actuel.id
    ]
    if not oraux_meme_examinateur:
        return None
    ecart_mini_minutes = _load_algo_params()['ecart_mini']
    autres_heures_sujet: dict[int, timedelta | None] = {}
    for o in oraux_meme_examinateur:
        autres = db_get(
            db_facility_web.SELECT_LISTE_EDITION_ORAL, o.id_candidat, no_list_auto=False,
        )
        autre = next((a for a in autres if a['id'] != o.id), None)
        autres_heures_sujet[o.id_candidat] = _to_td(autre['heure_sujet']) if autre else None
    heure_pause_meridienne, duree_pause_meridienne = _pause_meridienne_params()
    return rebalance.proposer_compaction(
        oraux_meme_examinateur, oral_actuel.heure_sujet, autres_heures_sujet, ecart_mini_minutes,
        heure_pause_meridienne, duree_pause_meridienne,
    )


@app.route("/gestion/candidat/changer-matiere", methods=["GET", "POST"])
@admin_required
@nocache
def changer_matiere_candidat() -> ResponseReturnValue:
    """
    Change la matière (choix1 ou choix2) d'un candidat en cours de journée :
    remplace son oral de l'ancienne matière par un nouveau dans la matière
    choisie — cf. docs/workflow_admin.md.
    """
    id_candidat = request.values.get("id_candidat", type=int)
    if id_candidat is None:
        abort(404, "Pas de candidat avec ce numéro")
    candidat_info = db_get(db_facility_web.SELECT_CANDIDAT_CHANGEMENT_MATIERE, id_candidat)
    if not candidat_info:
        abort(404, "Candidat introuvable")

    oraux_candidat = db_get(
        db_facility_web.SELECT_LISTE_EDITION_ORAL, id_candidat, no_list_auto=False,
    )
    liste_matieres = db_get(db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False)

    if request.method == "GET":
        return render_template(
            "changer_matiere_candidat.html",
            centre=CENTRE_EXAMEN,
            candidat=candidat_info,
            oraux=oraux_candidat,
            matieres=liste_matieres,
            etape="saisie",
            url_of_page=request.url,
            username=get_username(),
            authenticated=is_authenticated(),
        )

    # POST
    id_oral_a_remplacer = request.form.get("id_oral_a_remplacer", type=int)
    id_nouvelle_matiere = request.form.get("nouvelle_matiere", type=int)
    if not id_oral_a_remplacer or not id_nouvelle_matiere:
        return render_template(
            "changer_matiere_candidat.html",
            centre=CENTRE_EXAMEN, candidat=candidat_info, oraux=oraux_candidat,
            matieres=liste_matieres, etape="saisie",
            url_of_page=request.url, username=get_username(), authenticated=is_authenticated(),
            error_msg="Sélectionnez l'oral à remplacer et la nouvelle matière.",
        ), 400
    if id_nouvelle_matiere in (candidat_info['choix1'], candidat_info['choix2']):
        return render_template(
            "changer_matiere_candidat.html",
            centre=CENTRE_EXAMEN, candidat=candidat_info, oraux=oraux_candidat,
            matieres=liste_matieres, etape="saisie",
            url_of_page=request.url, username=get_username(), authenticated=is_authenticated(),
            error_msg="La nouvelle matière doit être différente des deux matières déjà choisies.",
        ), 400

    oral_ligne = db_get(db_facility_web.SELECT_ORAL_POUR_CHANGEMENT_MATIERE, id_oral_a_remplacer)
    if not oral_ligne or oral_ligne['id_candidat'] != id_candidat:
        abort(404, "Oral introuvable pour ce candidat")
    oral_actuel = _oral_actuel_depuis_ligne(oral_ligne)
    nouvelle_matiere_nom = next(
        (m['nom'] for m in liste_matieres if m['id'] == id_nouvelle_matiere), None,
    )
    if nouvelle_matiere_nom is None:
        abort(400, "Matière introuvable")

    niveau_resolution = request.form.get("niveau_resolution", "glouton")
    etape = request.form.get("etape", "previsualisation")
    if etape == "resolution_poussee_grille":
        niveau_resolution = "grille"
    elif etape == "resolution_poussee_etendue":
        niveau_resolution = "etendue"

    changement, ctx = _calculer_plan_changement_matiere(
        id_candidat, oral_actuel, id_nouvelle_matiere, nouvelle_matiere_nom,
    )
    if changement is None and niveau_resolution in ("grille", "etendue"):
        changement = _tenter_resolution_poussee_matiere(
            oral_actuel, ctx, etendre=(niveau_resolution == "etendue"),
        )

    inclure_compaction = request.form.get("inclure_compaction") == "on"
    compaction = _proposer_compaction(oral_actuel, oral_ligne['id_matiere'])

    if etape in ("resolution_poussee_grille", "resolution_poussee_etendue"):
        etape = "previsualisation"

    if etape == "confirmer":
        if changement is None:
            abort(400, "Aucun placement disponible à confirmer.")
        d = {
            'id': changement.id_oral,
            'examinateur': changement.nouvel_examinateur_id,
            'heure_sujet': _td_to_time_str(changement.nouvelle_heure_sujet),
            'heure_oral': _td_to_time_str(changement.nouvelle_heure_oral),
            'heure_fin': _td_to_time_str(changement.nouvelle_heure_fin),
            'mis_a_jour': 1,
        }
        # L'ancien examinateur n'est pas la cible de cette mise à jour (donc
        # pas notifié par _appliquer_changement_oral) mais doit être informé
        # qu'un candidat a disparu de son planning.
        _notifier_salle_loge_examinateur(oral_actuel.id_examinateur, changement.numero)
        _appliquer_changement_oral(d, changement.numero)

        ancienne_matiere_id = oral_ligne['id_matiere']
        requete_choix = (
            db_facility_web.UPDATE_CANDIDAT_CHOIX1
            if candidat_info['choix1'] == ancienne_matiere_id
            else db_facility_web.UPDATE_CANDIDAT_CHOIX2
        )
        db_update(requete_choix, id_candidat=id_candidat, nouvelle_matiere=id_nouvelle_matiere)

        compaction_appliquee = None
        if inclure_compaction and compaction is not None:
            d_compaction = {
                'id': compaction.id_oral,
                'examinateur': compaction.nouvel_examinateur_id,
                'heure_sujet': _td_to_time_str(compaction.nouvelle_heure_sujet),
                'heure_oral': _td_to_time_str(compaction.nouvelle_heure_oral),
                'heure_fin': _td_to_time_str(compaction.nouvelle_heure_fin),
                'mis_a_jour': 1,
            }
            _appliquer_changement_oral(d_compaction, compaction.numero)
            compaction_appliquee = compaction

        return render_template(
            "changer_matiere_candidat.html",
            centre=CENTRE_EXAMEN,
            candidat=candidat_info,
            etape="termine",
            changement=changement,
            compaction=compaction_appliquee,
            url_of_page=request.url,
            username=get_username(),
            authenticated=is_authenticated(),
        )

    return render_template(
        "changer_matiere_candidat.html",
        centre=CENTRE_EXAMEN,
        candidat=candidat_info,
        etape="previsualisation",
        changement=changement,
        compaction=compaction,
        id_oral_a_remplacer=id_oral_a_remplacer,
        nouvelle_matiere=id_nouvelle_matiere,
        niveau_resolution=niveau_resolution,
        url_of_page=request.url,
        username=get_username(),
        authenticated=is_authenticated(),
    )


@app.route('/gestion/delete-examinateur', methods=['POST'])
@admin_required
@nocache
def delete_examinateur() -> ResponseReturnValue:
    """Suppression d'un examinateur."""
    # #2 — Action mutante : POST + CSRF (cf. reload_pages — même raisonnement,
    # une suppression ne doit jamais être déclenchable par un simple lien).
    id_examinateur = request.form.get('id_examinateur', None)
    if id_examinateur is None:
        abort(404, "Pas d'examinateur avec ce numéro")
    db_update(db_facility_web.DELETE_EXAMINATEUR, id=id_examinateur)
    return redirect(url_for('liste_examinateurs'))


@app.route('/gestion/add-examinateur', methods=['GET', 'POST'])
@admin_required
@nocache
def add_examinateur() -> ResponseReturnValue:
    """Ajout d'un nouvel examinateur."""
    if request.method == "POST":
        nom   = (request.form.get('nom') or '').strip()
        salle = (request.form.get('salle') or '').strip()
        if not nom or not salle:
            liste_matieres = db_get(db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False)
            return render_template(
                "add_examinateur.html",
                centre=CENTRE_EXAMEN,
                liste_matieres=liste_matieres,
                liste_lycees=_LYCEES_DISPLAY,
                url_of_page=request.url,
                username=get_username(),
                erreur="Le nom et le numéro de salle sont obligatoires.",
            ), 400

        password = generate_password()
        d = {
            'nom': nom,
            'salle': salle,
            'matiere': request.form.get('matiere'),
            'loge': request.form.get('loge'),
            'etablissements': ','.join(request.form.getlist('etablissements')),
            'password_hash': hash_password(password, salle),
        }
        db_update(db_facility_web.INSERT_EXAMINATEUR, **d)

        # Stocker le nouveau mot de passe dans credentials.enc
        creds = _load_credentials()
        creds.setdefault("examinateurs", {})[salle] = password
        _save_credentials(creds)

        base_url = request.host_url.rstrip('/')
        papillon_filename = 'papillons_examinateurs.pdf'
        _regenerer_papillons_examinateurs(base_url)
        return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))

    # GET
    liste_matieres = db_get(db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False)
    return render_template(
        "add_examinateur.html",
        centre=CENTRE_EXAMEN,
        liste_matieres=liste_matieres,
        liste_lycees=_LYCEES_DISPLAY,
        url_of_page=request.url,
        username=get_username(),
    )


@app.route('/gestion/verify-logs')
@admin_required
@nocache
def verify_logs() -> ResponseReturnValue:
    """Vérifie l'intégrité de la chaîne de hash des logs d'audit et l'affiche."""
    logs = db_get(db_facility_web.SELECT_ALL_LOGS, no_list_auto=False)
    previous_hash = ''
    ok = True
    for log in logs:
        hash_ok = verify_log_item(log, previous_hash)
        log['ok'] = hash_ok
        if not hash_ok:
            ok = False
        previous_hash = log['hash']
        if 'action_data' in log:
            log['action_data'] = json.loads(log['action_data'])
            action = log['action_data']
            if 'data_new' in action and action['data_new'].get('emargement', ''):
                log["image_new"] = action['data_new']['emargement']
            if 'data_old' in action and action['data_old'].get('emargement', ''):
                log["image_old"] = action['data_old']['emargement']
    app.logger.info(
        f"Logs: {len(logs)} entrées — {'OK' if ok else 'COMPROMIS'}"
    )
    return render_template(
        "verify_logs.html",
        centre=CENTRE_EXAMEN,
        logs_integrity_ok=ok,
        logs=logs,
        username=get_username(),
        url_of_page=request.url,
    )


@app.route('/loge/timer-state', methods=['GET', 'POST'])
@limiter.exempt   # appelé automatiquement toutes les secondes par oral actif — auth suffit
@nocache
def timer_state() -> ResponseReturnValue:
    """API état des timers de loge — lecture/écriture dans Redis."""
    if not (is_authenticated() or is_loge_user()):
        abort(403)
    loge = request.args.get('loge') if request.method == 'GET' else (request.json or {}).get('loge')
    if not loge:
        abort(400)
    # Lecture (GET) : tout utilisateur authentifié peut voir les états de minuteur
    # Écriture (POST) : réservé à la loge concernée et à l'admin
    if request.method == 'POST' and not is_admin_user() and not is_loge_user(loge):
        abort(403)
    try:
        r = _redis()
        if request.method == 'GET':
            # Renvoyer tous les états pour cette loge en un seul appel
            states: dict = {}
            for raw_key in r.scan_iter(f'timer:{loge}:*'):
                key = raw_key.decode()
                parts = key.split(':', 3)          # timer:<loge>:<numero>:<sujet>
                if len(parts) == 4:
                    slot = f"{parts[2]}_{parts[3]}"
                    raw = r.get(raw_key)
                    if raw:
                        import json as _json
                        states[slot] = _json.loads(raw)
            return jsonify(states)
        # POST — sauvegarder un état
        import json as _json
        data = request.json or {}
        numero = data.get('numero', '')
        sujet  = data.get('sujet', '')
        state  = {k: data[k] for k in ('elapsed', 'running', 'startedAt') if k in data}
        if not numero or not sujet:
            abort(400)
        r.set(f'timer:{loge}:{numero}:{sujet}', _json.dumps(state), ex=24 * 3600)
        return jsonify({'ok': True})
    except (OSError, ValueError, KeyError) as e:
        app.logger.warning(f"timer_state erreur : {e}")
        abort(500)


def _monitoring_redis_stats() -> tuple[bool, dict]:
    """Lit les stats Redis pour le monitoring. Retourne (redis_ok, data)."""
    try:
        r = _redis()
        total = int(r.get('stats:req:total') or 0)
        by_status = {b: int(r.get(f'stats:req:status:{b}') or 0)
                     for b in ('2xx', '3xx', '4xx', '5xx')}
        now = datetime.now(TIMEZONE)
        hourly = []
        for i in range(23, -1, -1):
            h = now - timedelta(hours=i)
            hourly.append({'label': h.strftime('%Hh'),
                           'count': int(r.get(f"stats:req:h:{h.strftime('%Y%m%d%H')}") or 0)})
        online: dict = {'admin': 0, 'exam': 0, 'cand': 0, 'loge': 0}
        online_detail: dict = {'admin': [], 'exam': [], 'cand': [], 'loge': []}
        for raw_key in r.scan_iter('stats:online:*'):
            parts = raw_key.decode().split(':')
            if len(parts) >= 4:
                kind, ident = parts[2], ':'.join(parts[3:])
                ip = (r.get(raw_key) or b'').decode() or '?'
                if kind in online:
                    online[kind] += 1
                if kind in online_detail:
                    online_detail[kind].append({'id': ident, 'ip': ip})
        return True, {'total': total, 'by_status': by_status,
                      'hourly': hourly, 'online': online, 'online_detail': online_detail}
    except Exception as e:
        app.logger.debug(f"monitoring Redis indisponible : {e}")
        return False, {}


def _monitoring_recent_failures() -> list[dict]:
    """Retourne les IPs avec échecs d'auth dans les 5 dernières minutes."""
    import time as _time
    now_ts = _time.time()
    return sorted(
        [{'ip': ip, 'count': len([t for t in ts if now_ts - t < 300])}
         for ip, ts in _auth_failures.items()
         if any(now_ts - t < 300 for t in ts)],
        key=lambda x: -x['count'],
    )


@app.route('/gestion/monitoring/data')
@admin_required
@nocache
def monitoring_data() -> ResponseReturnValue:
    """Données de monitoring en JSON (polling AJAX)."""
    redis_ok, stats = _monitoring_redis_stats()
    recent_failures = _monitoring_recent_failures()
    return jsonify(
        redis_ok=redis_ok,
        total=stats.get('total'),
        by_status=stats.get('by_status'),
        hourly=stats.get('hourly'),
        online=stats.get('online'),
        online_detail=stats.get('online_detail'),
        recent_failures=recent_failures,
    )


@app.route('/gestion/monitoring')
@admin_required
@nocache
def monitoring() -> ResponseReturnValue:
    """Tableau de bord de monitoring — admin uniquement."""
    redis_ok, stats = _monitoring_redis_stats()
    recent_failures = _monitoring_recent_failures()
    return render_template(
        'monitoring.html',
        centre=CENTRE_EXAMEN,
        username=get_username(),
        url_of_page=request.url,
        redis_ok=redis_ok,
        total=stats.get('total'),
        by_status=stats.get('by_status'),
        hourly=stats.get('hourly'),
        online=stats.get('online'),
        online_detail=stats.get('online_detail'),
        recent_failures=recent_failures,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Archive de fin de session (RGPD : export des données à conserver)
# ──────────────────────────────────────────────────────────────────────────────

def _csv_bytes(fieldnames: list[str], rows: list[dict]) -> bytes:
    """Sérialise une liste de dicts en CSV (séparateur ';', BOM UTF-8 pour Excel)."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=';', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return ('﻿' + buf.getvalue()).encode('utf-8')


@app.route('/gestion/archive')
@admin_required
@nocache
def archive_page() -> ResponseReturnValue:
    """
    Page de confirmation avant la génération de l'archive de fin de session.
    Liste précisément ce que contiendra le zip pour validation par l'admin
    avant téléchargement (RGPD : minimisation — ni mots de passe, ni CSV bruts).
    """
    # Les fiches de salle sont (re)générées au moment du téléchargement — on
    # liste donc les salles concernées plutôt que les fichiers déjà présents
    # dans static/docs (qui pourraient être incomplets ou obsolètes).
    salles = sorted(
        s['salle'] for s in db_get(db_facility_web.SELECT_DOC_LISTE_SALLES, no_list_auto=False)
    )
    return render_template(
        'archive.html',
        centre=CENTRE_EXAMEN,
        salles=salles,
        username=get_username(),
        url_of_page=request.url,
    )


def _archive_regenerate_fiches_salle() -> list[Path]:
    """Régénère toutes les fiches de salle et retourne la liste des PDFs produits."""
    salles = db_get(db_facility_web.SELECT_DOC_LISTE_SALLES, no_list_auto=False)
    for s in salles:
        s['oraux'] = db_get(
            db_facility_web.SELECT_DOC_LISTE_SALLES_ORAUX, s['id'], no_list_auto=False
        )
    reports.liste_salle_oraux(salles, 'static/docs', 'salle', centre_examen=CENTRE_EXAMEN)
    docs_dir = Path(app.root_path) / 'static' / 'docs'
    if not docs_dir.is_dir():
        return []
    return sorted(list(docs_dir.glob('salle-*.pdf')) + list(docs_dir.glob('liste_salles.pdf')))


def _archive_build_zip(fiches_salles: list[Path], now: datetime) -> BytesIO:
    """Construit le ZIP d'archive et retourne un BytesIO prêt à envoyer."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        planning = db_get(db_facility_web.SELECT_DOC_ARCHIVE_PLANNING, no_list_auto=False)
        zf.writestr('planning_oraux.csv', _csv_bytes(
            ['candidat', 'numero', 'matiere', 'examinateur', 'salle',
             'heure_sujet', 'heure_oral', 'heure_fin', 'modifie'], planning,
        ))
        emargements = db_get(db_facility_web.SELECT_DOC_ARCHIVE_EMARGEMENTS, no_list_auto=False)
        zf.writestr('emargements.csv', _csv_bytes(
            ['candidat', 'numero', 'examinateur', 'salle', 'heure_oral',
             'signe', 'heure_emargement', 'hash_emargement'], emargements,
        ))
        logs = db_get(db_facility_web.SELECT_ALL_LOGS, no_list_auto=False)
        zf.writestr('journal_audit.json',
                    json.dumps(logs, ensure_ascii=False, indent=2, default=str).encode('utf-8'))
        for pdf in fiches_salles:
            zf.write(pdf, arcname=f'documents/{pdf.name}')
        manifest = (
            f"Archive de fin de session — {CENTRE_EXAMEN}\n"
            f"Générée le {now:%d/%m/%Y à %H:%M} par {get_username() or 'admin'}\n\n"
            "Contenu :\n"
            "  - planning_oraux.csv  : planning final des oraux\n"
            "  - emargements.csv     : preuves de signature des examinateurs\n"
            "  - journal_audit.json  : journal d'audit chaîné par hash\n"
            "  - documents/          : fiches d'émargement de toutes les salles\n\n"
            "Volontairement absents (minimisation RGPD) :\n"
            "  - mots de passe et clés de connexion\n"
            "  - fichiers CSV bruts d'inscription\n"
            "  - autres PDF (papillons, fiches candidats/loges, liste générale)\n"
        )
        zf.writestr('LISEZMOI.txt', manifest.encode('utf-8'))
    buf.seek(0)
    return buf


@app.route('/gestion/archive/download')
@admin_required
@nocache
def archive_download() -> ResponseReturnValue:
    """Génère et sert l'archive zip de fin de session (RGPD — données minimisées)."""
    now = datetime.now()
    fiches_salles = _archive_regenerate_fiches_salle()
    buf = _archive_build_zip(fiches_salles, now)
    filename = f"archive_{secure_filename(CENTRE_EXAMEN)}_{now:%Y%m%d}.zip"
    app.logger.info(f"Archive de fin de session générée par {get_username()}")
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=filename)


@app.route('/generate-screen-batch', methods=['GET'])
@admin_required
@nocache
def generate_screen_batch() -> ResponseReturnValue:
    """Affiche l'écran d'attente pendant la génération d'un document PDF groupé."""
    type_doc = request.args.get('type_doc')
    id_doc = request.args.get('id_doc')
    return render_template(
        'generate_screen.html',
        centre=CENTRE_EXAMEN,
        type_doc=type_doc,
        id_doc=id_doc,
        generation_url=url_for('generate_doc_batch', type_doc=type_doc,
                               id_doc=id_doc, _external=True),
    )


@app.route('/generate-doc-batch/<type_doc>-<id_doc>', methods=['GET'])
@admin_required
@nocache
def generate_doc_batch(type_doc: str, id_doc: str | None = None) -> ResponseReturnValue:
    """Génère un PDF groupé (tous candidats, toutes salles ou toutes loges)."""
    if type_doc == 'liste_oraux':
        infos = db_get(db_facility_web.SELECT_DOC_LISTE_ORAUX, no_list_auto=False)
        reports.liste_generale_oraux(infos, filename='static/docs/liste_oraux.pdf',
                                     centre_examen=CENTRE_EXAMEN)
        return jsonify({"url": url_for('download', filename='liste_oraux.pdf')})

    if type_doc == 'fiches_candidats':
        infos = db_get(db_facility_web.SELECT_DOC_LISTE_CANDIDATS, no_list_auto=False)
        for c in infos:
            c['oraux'] = db_get(
                db_facility_web.SELECT_DOC_LISTE_CANDIDATS_ORAUX,
                c['id'], no_list_auto=False,
            )
        reports.liste_fiches_candidats(infos, file_dir='static/docs',
                                       filename_root='candidat_',
                                       centre_examen=CENTRE_EXAMEN)
        return jsonify({"url": url_for('download', filename='liste_candidats.pdf')})

    if type_doc == 'fiches_salles':
        infos = db_get(db_facility_web.SELECT_DOC_LISTE_SALLES, no_list_auto=False)
        for s in infos:
            s['oraux'] = db_get(
                db_facility_web.SELECT_DOC_LISTE_SALLES_ORAUX,
                s['id'], no_list_auto=False,
            )
        reports.liste_salle_oraux(infos, 'static/docs', 'salle',
                                  centre_examen=CENTRE_EXAMEN)
        return jsonify({"url": url_for('download', filename='liste_salles.pdf')})

    if type_doc == 'fiches_loges':
        infos = db_get(db_facility_web.SELECT_LISTE_LOGES, no_list_auto=False)
        for loge_item in infos:
            loge_item['oraux'] = db_get(
                db_facility_web.SELECT_ORAUX_LOGE,
                loge_item['salle'], no_list_auto=False,
            )
        reports.liste_loge_oraux(infos, 'static/docs', 'loge',
                                 centre_examen=CENTRE_EXAMEN)
        return jsonify({"url": url_for('download', filename='liste_loges.pdf')})

    if type_doc == 'papillons_candidats':
        infos = db_get(db_facility_web.SELECT_ALL_CANDIDATS_PAPILLONS, no_list_auto=False)
        base_url = request.host_url.rstrip('/')
        reports.liste_papillons_candidats(
            infos,
            filename='static/docs/papillons_candidats.pdf',
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )
        return jsonify({"url": url_for('download', filename='papillons_candidats.pdf')})

    abort(404)


# ── Vérification d'existence d'un PDF généré (pour la page gestion/algo) ─────

@app.route('/gestion/algo/doc-exists/<filename>')
@admin_required
def algo_doc_exists(filename: str) -> ResponseReturnValue:
    """Indique si un PDF dans static/docs/ existe (utilisé par la page algo)."""
    if not re.match(r'^[\w\-. ]+\.pdf$', filename):
        abort(400)
    exists = (Path(app.root_path) / 'static' / 'docs' / filename).exists()
    return jsonify({"exists": exists,
                    "url": url_for('download', filename=filename) if exists else None})


@app.route('/download')
@nocache
def download() -> ResponseReturnValue:
    """
    Sert les fichiers PDF générés.
    - Les papillons (papillons_*) nécessitent d'être admin.
    - Les fiches candidats (candidat_*) nécessitent d'être authentifié (personnel ou candidat).
    - Tous les autres documents nécessitent d'être authentifié (personnel).
    """
    filename = request.args.get('filename', '')
    # Anti path-traversal : nom de fichier simple uniquement
    if not re.match(r'^[\w\-. ]+\.pdf$', filename):
        abort(400, "Nom de fichier invalide")

    if filename.startswith('papillons_'):
        if not is_admin_user():
            abort(403)
    elif filename.startswith('candidat_'):
        if not is_any_authenticated():
            abort(403)
    else:
        if not is_authenticated():
            return redirect(url_for('login', link_back=request.url))

    app.logger.info(f"Téléchargement: {filename}")
    return send_from_directory('static/docs', filename)


@app.route('/theme.css')
def theme_css() -> ResponseReturnValue:
    """Feuille de style dynamique surchargeant la palette CSS selon ACCENT_COLOR.

    Surchage les variables CSS de main.css avec la palette dérivée de ACCENT_COLOR
    (défini dans app_secrets.py au moment du setup). Mis en cache 24h côté client.
    """
    pal = _derive_palette(ACCENT_COLOR)
    css = (
        ":root{{\n"
        "  --primary:     {primary};\n"
        "  --primary-dk:  {primary_dk};\n"
        "  --primary-mid: {primary_mid};\n"
        "  --primary-lt:  {primary_lt};\n"
        "  --surface:     {surface};\n"
        "  --surface-2:   {surface_2};\n"
        "  --border:      {border};\n"
        "  --text:        {text};\n"
        "  --text-md:     {text_md};\n"
        "  --text-sm:     {text_sm};\n"
        "  --modified:    {primary};\n"
        "}}"
    ).format(**pal)
    resp = make_response(css)
    resp.headers['Content-Type']  = 'text/css; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@app.route('/about')
def about() -> ResponseReturnValue:
    """Page « à propos » — crédits et description du projet."""
    resp = make_response(render_template("about.html", centre=CENTRE_EXAMEN,
                                        hostname=HOSTNAME, username=get_username()))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@app.route('/mentions-legales')
def mentions_legales() -> ResponseReturnValue:
    """Mentions légales et politique de confidentialité (RGPD)."""
    resp = make_response(render_template(
        "mentions_legales.html",
        centre=CENTRE_EXAMEN,
        fqdn=FQDN,
        director_name=DIRECTOR_NAME,
        centre_address=CENTRE_ADDRESS,
        academie=ACADEMIE,
        hebergeur=HEBERGEUR,
        dpd_email=DPD_EMAIL,
        username=get_username(),
    ))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


# ── Gestion de algo.py (admin) ────────────────────────────────────────────────

_DATA_DIR = Path(app.root_path).parent / "data"
_CREDENTIALS_FILE     = _DATA_DIR / "credentials.enc"
_shm = Path('/dev/shm')
_CREDENTIALS_TMP_FILE = (
    _shm / 'second_oral_creds_new.json'
    if _shm.exists() and _shm.is_dir()
    else _DATA_DIR / 'credentials_new.json'
)
_ALLOWED_CSV = {
    "candidats":    "candidats.csv",
    "examinateurs": "examinateurs.csv",
    "preps":        "preps.csv",
}
_ALGO_PARAMS_FILE = _DATA_DIR / "algo_params.json"
_ALGO_PARAMS_DEFAULTS = {
    "heure_debut":      "08:10",
    "creneaux":         13,
    "n_run":            1000,
    "ecart_mini":       80,
    "debug":            False,
    "engine":           "monte_carlo",
    "cp_timeout":       60,
    "pause_meridienne_debut": "",
    "pause_meridienne_duree": 60,
}

def _load_algo_params() -> dict:
    """Charge les paramètres de l'algorithme depuis le fichier JSON, ou retourne les défauts."""
    try:
        import json as _json
        return {**_ALGO_PARAMS_DEFAULTS, **_json.loads(_ALGO_PARAMS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(_ALGO_PARAMS_DEFAULTS)


# ── Store chiffré des credentials — délégué à credential_store.py ─────────────

def _load_credentials() -> dict:
    """Charge et déchiffre le store de credentials depuis data/credentials.enc.

    Délègue à credential_store.load_credentials (AES-256-GCM, HKDF-SHA256).
    Retourne un dict vide si le fichier n'existe pas ou si le déchiffrement échoue.
    """
    return _cstore_load(_CREDENTIALS_FILE, APP_SECRET_KEY)


def _save_credentials(creds: dict) -> None:
    """Chiffre et persiste le store de credentials dans data/credentials.enc.

    Délègue à credential_store.save_credentials. Nonce aléatoire 96 bits,
    fichier créé avec les permissions 0o600.
    """
    _cstore_save(_CREDENTIALS_FILE, APP_SECRET_KEY, creds)


def _absorb_credentials_file(rc: int) -> None:
    """Callback appelé par algo_bg après la fin d'algo.py.

    Si l'algo a réussi (rc == 0), lit data/credentials_new.json (plaintext temporaire
    écrit par algo.py), l'intègre dans le store chiffré (AES-256-GCM), puis supprime
    le fichier temporaire pour ne pas laisser de credentials en clair sur le disque.

    Le fichier temporaire est toujours supprimé (succès ou échec du chiffrement) afin
    d'éviter que des credentials en clair persistent. En cas d'échec, l'erreur est
    journalisée pour permettre un diagnostic.
    """
    if rc != 0:
        return
    if not _CREDENTIALS_TMP_FILE.exists():
        app.logger.warning("credentials_new.json introuvable après algo.py")
        return
    encryption_ok = False
    try:
        new_creds = json.loads(_CREDENTIALS_TMP_FILE.read_text())
        _save_credentials(new_creds)
        encryption_ok = True
        app.logger.info("Credentials chiffrés et stockés dans credentials.enc")
    except Exception as exc:
        app.logger.error(f"Échec du chiffrement des credentials post-algo : {exc}")
    finally:
        _CREDENTIALS_TMP_FILE.unlink(missing_ok=True)
        if not encryption_ok:
            app.logger.error(
                "credentials_new.json supprimé sans chiffrement réussi — "
                "renouveler les identifiants manuellement via /gestion/credentials"
            )


@app.route("/gestion/documents")
@admin_required
@nocache
def gestion_documents() -> ResponseReturnValue:
    """Page de téléchargement des documents générés (papillons, fiches, liste générale)."""
    return render_template(
        "gestion_documents.html",
        centre=CENTRE_EXAMEN,
        username=get_username(),
        csrf_token=generate_csrf(),
    )


@app.route("/gestion/algo")
@admin_required
@nocache
def gestion_algo() -> ResponseReturnValue:
    """Page d'admin de algo.py : upload CSV, lancement, log en direct."""
    from algo_bg import is_running as _is_running
    csv_status = {
        key: (_DATA_DIR / fname).exists()
        for key, fname in _ALLOWED_CSV.items()
    }
    return render_template(
        "gestion_algo.html",
        centre=CENTRE_EXAMEN,
        username=get_username(),
        is_running=_is_running(),
        csv_status=csv_status,
        algo_params=_load_algo_params(),
        csrf_token=generate_csrf(),
    )


@app.route("/gestion/algo/upload", methods=["POST"])
@admin_required
def algo_upload_csv() -> ResponseReturnValue:
    """Upload des fichiers CSV (individuels) ou d'un fichier ODS unique vers data/."""
    from csv_validator import normalize_csv, validate_all
    import io as _io
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    uploaded, upload_errors = [], []

    # ── Cas ODS : un fichier unique → 3 CSV ───────────────────────────────────
    ods_file = request.files.get("ods_file")
    if ods_file and ods_file.filename:
        if not ods_file.filename.lower().endswith(".ods"):
            return jsonify({"ok": False, "uploaded": [],
                            "errors": ["ods_file : extension .ods requise"],
                            "validation": {}})
        from ods_handler import parse_ods
        try:
            sheets = parse_ods(ods_file.read())
        except ValueError as e:
            return jsonify({"ok": False, "uploaded": [], "errors": [str(e)], "validation": {}})

        sheet_map = {
            "candidats":    ("candidats.csv",    normalize_csv),
            "examinateurs": ("examinateurs.csv",  normalize_csv),
            "preps":        ("preps.csv",         normalize_csv),
        }
        for sheet_key, (target_name, _) in sheet_map.items():
            rows = sheets.get(sheet_key)
            if rows is None:
                upload_errors.append(f"Feuille '{sheet_key}' absente du fichier ODS.")
                continue
            # Reconstruire des bytes CSV depuis les rows
            import csv as _csv
            if not rows:
                upload_errors.append(f"Feuille '{sheet_key}' vide dans le fichier ODS.")
                continue
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";",
                                     lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            raw = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM UTF-8
            dest = _DATA_DIR / target_name
            if dest.exists():
                dest.rename(dest.with_suffix(".csv.bak"))
            dest.write_bytes(raw)
            uploaded.append(target_name)
            app.logger.info(f"ODS→CSV: {target_name} ({len(rows)} lignes)")

    else:
        # ── Cas CSV individuels ────────────────────────────────────────────────
        for field, target_name in _ALLOWED_CSV.items():
            f = request.files.get(field)
            if not f or not f.filename:
                continue
            if not f.filename.lower().endswith(".csv"):
                upload_errors.append(f"{target_name} : extension .csv requise")
                continue
            raw = f.read()
            try:
                rows, delim = normalize_csv(raw)
            except Exception as e:
                upload_errors.append(f"{target_name} : impossible de lire le fichier ({e})")
                continue
            if not rows:
                upload_errors.append(f"{target_name} : fichier vide")
                continue
            dest = _DATA_DIR / target_name
            if dest.exists():
                dest.rename(dest.with_suffix(".csv.bak"))
            dest.write_bytes(raw)
            uploaded.append(target_name)
            app.logger.info(f"CSV upload: {target_name} ({len(rows)} lignes, sep='{delim}')")

    # Validation croisée sur les fichiers présents après upload
    report = validate_all(
        _DATA_DIR / "candidats.csv"    if (_DATA_DIR / "candidats.csv").exists()    else None,
        _DATA_DIR / "examinateurs.csv" if (_DATA_DIR / "examinateurs.csv").exists() else None,
        _DATA_DIR / "preps.csv"        if (_DATA_DIR / "preps.csv").exists()        else None,
    )
    return jsonify({
        "ok":       not upload_errors,
        "uploaded": uploaded,
        "errors":   upload_errors,
        "validation": report,
    })


@app.route("/gestion/algo/validate")
@admin_required
def algo_validate_csv() -> ResponseReturnValue:
    """Rapport de validation complet des CSV existants (pré-lancement)."""
    from csv_validator import validate_all
    report = validate_all(
        _DATA_DIR / "candidats.csv"    if (_DATA_DIR / "candidats.csv").exists()    else None,
        _DATA_DIR / "examinateurs.csv" if (_DATA_DIR / "examinateurs.csv").exists() else None,
        _DATA_DIR / "preps.csv"        if (_DATA_DIR / "preps.csv").exists()        else None,
    )
    return jsonify(report)


@app.route("/gestion/algo/params", methods=["POST"])
@admin_required
def algo_save_params() -> ResponseReturnValue:
    """Sauvegarde les paramètres de l'algorithme dans data/algo_params.json."""
    import json as _json
    data = request.get_json(silent=True) or {}
    params = dict(_ALGO_PARAMS_DEFAULTS)
    try:
        h, m   = str(data.get("heure_debut", "08:10")).split(":")
        params["heure_debut"] = f"{int(h):02d}:{int(m):02d}"
        params["creneaux"]    = max(1, min(30, int(data.get("creneaux",   13))))
        params["n_run"]       = max(1, min(100_000, int(data.get("n_run", 1000))))
        params["ecart_mini"]  = max(10, min(240, int(data.get("ecart_mini", 80))))
        params["debug"]       = bool(data.get("debug", False))
        engine = str(data.get("engine", "monte_carlo")).strip().lower()
        engines_valides = ("monte_carlo", "cpsat")
        params["engine"]     = engine if engine in engines_valides else "monte_carlo"
        params["cp_timeout"] = max(5, min(600, int(data.get("cp_timeout", 60))))
        pause_debut = str(data.get("pause_meridienne_debut", "")).strip()
        if pause_debut:
            h, m = pause_debut.split(":")
            params["pause_meridienne_debut"] = f"{int(h):02d}:{int(m):02d}"
        else:
            params["pause_meridienne_debut"] = ""
        params["pause_meridienne_duree"] = max(
            0, min(240, int(data.get("pause_meridienne_duree", 60))),
        )
    except (ValueError, KeyError):
        return jsonify({"ok": False, "reason": "invalid_params"}), 400
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ALGO_PARAMS_FILE.write_text(_json.dumps(params))
    app.logger.info(f"algo params saved: {params}")
    return jsonify({"ok": True, "params": params})


@app.route("/gestion/algo/download-csv/<key>")
@admin_required
def algo_download_csv(key: str) -> ResponseReturnValue:
    """Télécharge un des fichiers CSV de data/."""
    if key not in _ALLOWED_CSV:
        abort(404)
    path = _DATA_DIR / _ALLOWED_CSV[key]
    if not path.exists():
        abort(404)
    return send_from_directory(str(_DATA_DIR), _ALLOWED_CSV[key], as_attachment=True)


@app.route("/gestion/algo/download-modele-ods")
@admin_required
def algo_download_modele_ods() -> ResponseReturnValue:
    """Génère et télécharge le fichier ODS modèle (disciplines depuis preps.csv si présent)."""
    from ods_handler import generate_ods_modele
    from csv_validator import normalize_csv
    import io as _io
    preps_path = _DATA_DIR / "preps.csv"
    preps_rows: list[dict] | None = None
    if preps_path.exists():
        try:
            preps_rows, _ = normalize_csv(preps_path.read_bytes())
        except (OSError, UnicodeDecodeError, ValueError):
            pass
    buf = generate_ods_modele(preps_rows)
    return send_file(
        _io.BytesIO(buf),
        mimetype="application/vnd.oasis.opendocument.spreadsheet",
        as_attachment=True,
        download_name="modele_oral.ods",
    )


@app.route("/gestion/algo/run", methods=["POST"])
@admin_required
def algo_run() -> ResponseReturnValue:
    """Lance algo.py en tâche de fond; la sortie est streamée via SSE."""
    from algo_bg import run_algo as _run, is_running as _is_running
    from flask_sse import Message, _get_redis_pub
    if _is_running():
        return jsonify({"ok": False, "reason": "already_running"})

    # Résoudre l'URL Redis ICI (dans le contexte Flask) pour éviter
    # RuntimeError "Working outside of application context" dans le thread.
    redis_url = app.config.get("SSE_REDIS_URL") or app.config.get("REDIS_URL")
    assert isinstance(redis_url, str)
    redis_client = _get_redis_pub(redis_url)

    def _publish(data: str) -> None:
        msg = Message(data, type="algo_line")
        redis_client.publish(channel="algo_output",
                             message=json.dumps(msg.to_dict()))

    started = _run(_publish, db_host=os.environ.get("DB_HOST", "localhost"),
                   params=_load_algo_params(), on_done=_absorb_credentials_file)
    app.logger.info(f"algo.py: {'démarré' if started else 'déjà en cours'}")
    return jsonify({"ok": started})


@app.route("/gestion/algo/status")
@admin_required
def algo_status() -> ResponseReturnValue:
    """Statut JSON de algo.py."""
    from algo_bg import is_running as _is_running
    return jsonify({"running": _is_running()})


@app.route("/gestion/algo/stop", methods=["POST"])
@admin_required
def algo_stop() -> ResponseReturnValue:
    """Arrête algo.py s'il est en cours d'exécution (ex. l'utilisateur quitte la page)."""
    from algo_bg import stop_algo as _stop
    stopped = _stop()
    app.logger.info(f"algo.py: {'arrêté' if stopped else 'aucun run en cours'}")
    return jsonify({"ok": stopped})


# ──────────────────────────────────────────────────────────────────────────────
# Renouvellement des identifiants (découplé de l'algo)
# ──────────────────────────────────────────────────────────────────────────────

def _renew_candidat(candidat_id: int) -> str:
    """Génère de nouveaux identifiants pour un candidat et met à jour la DB.

    Le login_key est stocké en clair dans la DB (utilisé comme mot de passe candidat)
    et son hash est mis à jour simultanément.

    :param candidat_id: Identifiant DB du candidat.
    :returns: Nouvelle login_key en clair (pour regénération du papillon si besoin).
    """
    new_key = generate_password()
    candidat = db_get(db_facility_web.SELECT_INFOS_CANDIDAT_BY_ID, candidat_id)
    new_hash = hash_password(new_key, str(candidat['numero']))
    db_update(db_facility_web.UPDATE_CANDIDAT_CREDENTIALS,
              id=candidat_id, login_key=new_key, password_hash=new_hash)
    return new_key


def _renew_examinateur(exam_id: int) -> tuple[str, str, str]:
    """Génère un nouveau mot de passe pour un examinateur, met à jour DB et store chiffré.

    La salle est l'identifiant de connexion de l'examinateur ; elle est utilisée comme
    clé dans le store chiffré (credentials.enc) pour permettre la regénération du papillon.

    :param exam_id: Identifiant DB de l'examinateur.
    :returns: Tuple (salle, nom, password_plaintext) pour la génération du papillon.
    """
    exam = db_get(db_facility_web.SELECT_EXAMINATEUR_FOR_RENEWAL, exam_id)
    new_password = generate_password()
    new_hash = hash_password(new_password, exam['salle'])
    db_update(db_facility_web.UPDATE_EXAMINATEUR_PASSWORD,
              id=exam_id, password_hash=new_hash)
    creds = _load_credentials()
    creds.setdefault("examinateurs", {})[exam['salle']] = new_password
    _save_credentials(creds)
    return exam['salle'], exam['nom'], new_password


def _renew_loge(nom_loge: str) -> str:
    """Génère un nouveau mot de passe pour une loge, met à jour DB et store chiffré.

    Le mot de passe d'une loge est partagé entre tous les agents de surveillance
    de cette loge. La clé dans le store chiffré est le nom de la loge.

    :param nom_loge: Nom de la loge (clé primaire de la table Loge).
    :returns: Nouveau mot de passe en clair (pour génération du papillon).
    """
    new_password = generate_password()
    new_hash = hash_password(new_password, nom_loge)
    db_update(db_facility_web.UPDATE_LOGE_PASSWORD, nom=nom_loge, password_hash=new_hash)
    creds = _load_credentials()
    creds.setdefault("loges", {})[nom_loge] = new_password
    _save_credentials(creds)
    return new_password


def _regenerer_papillons_examinateurs(base_url: str) -> None:
    """Regénère papillons_examinateurs.pdf avec tous les mots de passe actuels du store.

    Récupère la liste des examinateurs en DB et associe chaque salle au mot de passe
    stocké dans credentials.enc. Les examinateurs absents du store sont ignorés
    (leur mot de passe plaintext n'est plus disponible).
    """
    creds = _load_credentials()
    store_exams = creds.get("examinateurs", {})
    tous = db_get(db_facility_web.SELECT_ALL_EXAMINATEURS_FOR_RENEWAL, no_list_auto=False)
    connexions = [
        (ex['salle'], ex['nom'], store_exams[ex['salle']])
        for ex in tous
        if ex['salle'] in store_exams
    ]
    if connexions:
        reports.liste_papillons_connexion(
            connexions,
            filename=str(Path(app.root_path) / 'static' / 'docs' / 'papillons_examinateurs.pdf'),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )


def _regenerer_papillons_loges(base_url: str) -> None:
    """Regénère papillons_loges.pdf avec tous les mots de passe actuels du store.

    Récupère la liste des loges en DB et associe chaque nom au mot de passe
    stocké dans credentials.enc. Les loges absentes du store sont ignorées.
    """
    creds = _load_credentials()
    store_loges = creds.get("loges", {})
    toutes = db_get(db_facility_web.SELECT_ALL_LOGES_FOR_RENEWAL, no_list_auto=False)
    loges_data = [
        (lg['nom'], store_loges[lg['nom']])
        for lg in toutes
        if lg['nom'] in store_loges
    ]
    if loges_data:
        reports.liste_papillons_loges(
            loges_data,
            filename=str(Path(app.root_path) / 'static' / 'docs' / 'papillons_loges.pdf'),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )


def _regenerer_papillons_candidats(base_url: str) -> None:
    """Regénère papillons_candidats.pdf avec tous les candidats en DB.

    Le login_key des candidats est stocké en clair dans la DB, donc aucun
    accès au store chiffré n'est nécessaire.
    """
    candidats = db_get(db_facility_web.SELECT_ALL_CANDIDATS_PAPILLONS, no_list_auto=False)
    if candidats:
        reports.liste_papillons_candidats(
            candidats,
            filename=str(Path(app.root_path) / 'static' / 'docs' / 'papillons_candidats.pdf'),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )


@app.route("/gestion/credentials")
@admin_required
@nocache
def gestion_credentials() -> ResponseReturnValue:
    """Page de gestion du renouvellement des identifiants (sans relancer l'algo)."""
    candidats = db_get(db_facility_web.SELECT_ALL_CANDIDATS_FOR_RENEWAL, no_list_auto=False)
    examinateurs = db_get(db_facility_web.SELECT_ALL_EXAMINATEURS_FOR_RENEWAL, no_list_auto=False)
    loges = db_get(db_facility_web.SELECT_ALL_LOGES_FOR_RENEWAL, no_list_auto=False)
    store_ok = _CREDENTIALS_FILE.exists()
    _raw_papillon = request.args.get('new_papillon', '')
    # Valider le nom de fichier (même règle que la route /download — anti path-traversal)
    new_papillon = _raw_papillon if re.match(r'^[\w\-. ]+\.pdf$', _raw_papillon) else None
    return render_template(
        "credentials.html",
        centre=CENTRE_EXAMEN,
        username=get_username(),
        url_of_page=request.url,
        authenticated=is_authenticated(),
        candidats=candidats,
        examinateurs=examinateurs,
        loges=loges,
        store_ok=store_ok,
        new_papillon=new_papillon,
    )


@app.route("/gestion/credentials/candidat/<int:id>", methods=["POST"])
@admin_required
@nocache
def renew_candidat(id: int) -> ResponseReturnValue:
    """Renouvelle les identifiants d'un candidat (login_key + password_hash)
    et regénère le PDF groupé papillons_candidats.pdf.

    :param id: Identifiant DB du candidat.
    :returns: Redirection vers link_back si fourni, sinon /gestion/credentials.
    """
    _renew_candidat(id)
    base_url = request.host_url.rstrip('/')
    papillon_filename = 'papillons_candidats.pdf'
    _regenerer_papillons_candidats(base_url)
    url = _safe_redirect_url(request.form.get('link_back'))
    if url:
        return redirect(_add_query_param(url, new_papillon=papillon_filename))
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


@app.route("/gestion/credentials/candidats", methods=["POST"])
@admin_required
@nocache
def renew_candidats() -> ResponseReturnValue:
    """Renouvelle les identifiants de tous les candidats en base.

    :returns: Redirection vers /gestion/credentials.
    """
    tous = db_get(db_facility_web.SELECT_ALL_CANDIDATS_FOR_RENEWAL, no_list_auto=False)
    base_url = request.host_url.rstrip('/')
    for c in tous:
        _renew_candidat(c['id'])
    papillon_filename = 'papillons_candidats.pdf'
    _regenerer_papillons_candidats(base_url)
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


@app.route("/gestion/credentials/examinateur/<int:id>", methods=["POST"])
@admin_required
@nocache
def renew_examinateur(id: int) -> ResponseReturnValue:
    """Renouvelle le mot de passe d'un examinateur, regénère son papillon individuel
    et met à jour le PDF groupé papillons_examinateurs.pdf.

    :param id: Identifiant DB de l'examinateur.
    :returns: Redirection vers /gestion/credentials avec lien vers le papillon groupé.
    """
    _renew_examinateur(id)
    base_url = request.host_url.rstrip('/')
    papillon_filename = 'papillons_examinateurs.pdf'
    _regenerer_papillons_examinateurs(base_url)
    url = _safe_redirect_url(request.form.get('link_back'))
    if url:
        return redirect(_add_query_param(url, new_papillon=papillon_filename))
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


@app.route("/gestion/credentials/examinateurs", methods=["POST"])
@admin_required
@nocache
def renew_examinateurs() -> ResponseReturnValue:
    """Renouvelle les mots de passe de tous les examinateurs et regénère leurs papillons.

    :returns: Redirection vers /gestion/credentials avec lien vers le papillon groupé.
    """
    tous = db_get(db_facility_web.SELECT_ALL_EXAMINATEURS_FOR_RENEWAL, no_list_auto=False)
    connexions = []
    base_url = request.host_url.rstrip('/')
    for exam in tous:
        salle, nom, password = _renew_examinateur(exam['id'])
        connexions.append((salle, nom, password))
    papillon_filename = 'papillons_examinateurs.pdf'
    if connexions:
        reports.liste_papillons_connexion(
            connexions,
            filename=str(Path(app.root_path) / 'static' / 'docs' / papillon_filename),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


@app.route("/gestion/credentials/loge/<nom>", methods=["POST"])
@admin_required
@nocache
def renew_loge(nom: str) -> ResponseReturnValue:
    """Renouvelle le mot de passe d'une loge, regénère son papillon individuel
    et met à jour le PDF groupé papillons_loges.pdf pour la cohérence.

    Retourne 404 si la loge n'existe pas en base (table Loge).

    :param nom: Nom de la loge (clé primaire de la table Loge).
    :returns: Redirection vers /gestion/credentials avec lien vers le papillon groupé, ou 404.
    """
    if not db_get(db_facility_web.SELECT_LOGE_BY_NOM, nom, no_list_auto=False):
        abort(404, f"Loge inconnue : {nom!r}")
    _renew_loge(nom)
    base_url = request.host_url.rstrip('/')
    papillon_filename = 'papillons_loges.pdf'
    _regenerer_papillons_loges(base_url)
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


@app.route("/gestion/credentials/loges", methods=["POST"])
@admin_required
@nocache
def renew_loges() -> ResponseReturnValue:
    """Renouvelle les mots de passe de toutes les loges et regénère leurs papillons.

    :returns: Redirection vers /gestion/credentials avec lien vers le papillon groupé.
    """
    toutes = db_get(db_facility_web.SELECT_ALL_LOGES_FOR_RENEWAL, no_list_auto=False)
    loges_data = []
    base_url = request.host_url.rstrip('/')
    for loge in toutes:
        nom = loge['nom']
        password = _renew_loge(nom)
        loges_data.append((nom, password))
    papillon_filename = 'papillons_loges.pdf'
    if loges_data:
        reports.liste_papillons_loges(
            loges_data,
            filename=str(Path(app.root_path) / 'static' / 'docs' / papillon_filename),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )
    return redirect(url_for('gestion_credentials', new_papillon=papillon_filename))


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée développement
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=dev_on)
