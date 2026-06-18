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
from urllib.parse import unquote, quote, urlparse

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
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import db_facility_web
import reports
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
limiter.limit("30 per minute", override_defaults=True)(sse)


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
            'heure_oral': request.form.get('heure_oral'),
            'mis_a_jour': 1 if request.form.get('mis_a_jour') == 'on' else 0,
        }
        numero = request.form.get('numero')
        db_update(db_facility_web.UPDATE_INFOS_ORAL, **d)
        if d['mis_a_jour'] == 1:
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
    new_papillon = _raw if re.match(r'^papillons_salle_[\w\-]+\.pdf$', _raw) else ''
    return render_template(
        'liste_examinateurs.html',
        centre=CENTRE_EXAMEN,
        examinateurs=examinateurs,
        admin=is_admin_user(),
        url_of_page=request.url,
        username=get_username(),
        new_papillon=new_papillon,
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
            'etablissements': request.form.get('etablissements'),
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
        db_facility_web.SELECT_ORAUX_EXAMINATEUR, id_examinateur, no_list_auto=False
    )
    return render_template(
        "edit_examinateur.html",
        centre=CENTRE_EXAMEN,
        donnees_examinateur=donnees_examinateur,
        liste_oraux=liste_oraux,
        url_of_page=request.url,
        link_back=url,
        username=get_username(),
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

        papillon_filename = f'papillons_salle_{secure_filename(salle)}.pdf'
        base_url = request.host_url.rstrip('/')
        reports.liste_papillons_connexion(
            [(salle, nom, password)],
            filename=str(Path(app.root_path) / 'static' / 'docs' / papillon_filename),
            base_url=base_url,
            centre_examen=CENTRE_EXAMEN,
        )
        return redirect(url_for('liste_examinateurs', new_papillon=papillon_filename))

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
@nocache
def timer_state() -> ResponseReturnValue:
    """API état des timers de loge — lecture/écriture dans Redis."""
    if not (is_authenticated() or is_loge_user()):
        abort(403)
    loge = request.args.get('loge') if request.method == 'GET' else (request.json or {}).get('loge')
    if not loge:
        abort(400)
    # Vérifier que l'utilisateur a accès à cette loge
    if not is_admin_user() and not is_loge_user(loge):
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
_ALLOWED_CSV = {
    "candidats":    "candidats.csv",
    "examinateurs": "examinateurs.csv",
    "preps":        "preps.csv",
}
_ALGO_PARAMS_FILE = _DATA_DIR / "algo_params.json"
_ALGO_PARAMS_DEFAULTS = {
    "heure_debut": "08:10",
    "creneaux":    13,
    "n_run":       1000,
    "ecart_mini":  80,
}

def _load_algo_params() -> dict:
    try:
        import json as _json
        return {**_ALGO_PARAMS_DEFAULTS, **_json.loads(_ALGO_PARAMS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(_ALGO_PARAMS_DEFAULTS)


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
                   params=_load_algo_params())
    app.logger.info(f"algo.py: {'démarré' if started else 'déjà en cours'}")
    return jsonify({"ok": started})


@app.route("/gestion/algo/status")
@admin_required
def algo_status() -> ResponseReturnValue:
    """Statut JSON de algo.py."""
    from algo_bg import is_running as _is_running
    return jsonify({"running": _is_running()})


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée développement
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=dev_on)
