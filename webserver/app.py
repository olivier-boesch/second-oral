"""
Second Oral — Application Web Flask

Gestion des oraux de second groupe :
  - Consultation publique (candidats, salles, loges)
  - Authentification examinateurs (mot de passe par salle)
  - Authentification admin (TOTP)
  - Authentification candidats (INE + papillon)
  - Signature dématérialisée
  - Génération de documents PDF
"""
import json
import os
import re
import tempfile
from pathlib import Path
from datetime import datetime
from functools import wraps
from urllib.parse import unquote, quote, urlparse

import logging
import pyotp
import segno
from flask import (
    Flask, render_template, request, redirect, abort,
    session, jsonify, url_for, make_response, send_from_directory, g,
)
from flask_compress import Compress
from flask_limiter import Limiter, ExemptionScope
from flask_limiter.util import get_remote_address
from wtforms.fields.choices import SelectField
from wtforms.fields.simple import PasswordField, SubmitField, StringField, HiddenField
from flask_sse import sse
from flask_talisman import Talisman
from flask_wtf import FlaskForm
from flask_wtf.csrf import generate_csrf
from wtforms.validators import DataRequired
from PIL import Image
from io import BytesIO
from base64 import b64encode, b64decode
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import db_facility_web
import reports
from app_secrets import (
    CENTRE_EXAMEN, DIGITAL_SIGN, LOGIN_KEY, APP_SECRET_KEY, HOSTNAME,
    TIMEZONE, hash_password, check_password, verify_log_item,
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
        SEND_FILE_MAX_AGE_DEFAULT=0,
        TEMPLATES_AUTO_RELOAD=True,
        PREFERRED_URL_SCHEME='https',
        SERVER_NAME=FQDN,
        APPLICATION_ROOT='/',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["1000 per day", "200 per hour"],
        storage_uri=_REDIS_URL,
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

    # Note : 'unsafe-inline' est nécessaire pour les handlers inline HTML
    # (onclick, onchange, onload). Les nonces CSP bloquent ces handlers dans les
    # navigateurs modernes, ce qui casse les fonctionnalités (PDF, signature…).
    # Protection principale : default-src 'self' bloque tout script externe,
    # form-action et base-uri bloquent les vecteurs d'injection les plus courants.
    csp = {
        'default-src': ["'self'"],
        'script-src':  ["'self'", "'unsafe-inline'"],
        'style-src':   ["'self'", "'unsafe-inline'"],
        'img-src':     ["'self'", "data:"],
        'base-uri':    ["'self'"],
        'form-action': ["'self'"],
    }
    Talisman(
        app,
        content_security_policy=csp,
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

app.register_blueprint(sse, url_prefix='/stream')
limiter.exempt(
    sse,
    flags=ExemptionScope.DEFAULT | ExemptionScope.APPLICATION | ExemptionScope.ANCESTORS,
)


@app.before_request
def protect_sse():
    """Le flux SSE est réservé aux utilisateurs authentifiés."""
    if request.path.startswith('/stream'):
        if ('user' not in session
                and 'candidat' not in session
                and 'loge' not in session):
            abort(401)

app._db = db_facility_web.DbInterface()
app._otp = pyotp.TOTP(LOGIN_KEY)


def _get_redis_pub_for_ine():
    """Client Redis (court-circuit) pour stocker les tokens INE temporaires."""
    from flask_sse import _get_redis_pub
    return _get_redis_pub(app.config.get('REDIS_URL', 'redis://localhost'))


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
    Renvoie None si l'URL est externe ou malformée.
    """
    if not url or url in ('None', ''):
        return None
    parsed = urlparse(url)
    # Accepte les chemins relatifs et les URLs du même hôte
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


def is_student_user(ine=None):
    """
    Renvoie True si un candidat est connecté.
    Si ine est fourni, vérifie que le candidat connecté est bien celui-là.
    """
    if 'candidat' not in session:
        return False
    if ine is not None:
        return session['candidat'] == str(ine)
    return True


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

def generate_token(id_oral):
    """Génère un token de signature et le stocke en base."""
    tk = token_urlsafe(16)
    d = {
        'token': tk,
        'time_limit': (datetime.now() + timedelta(minutes=5)).isoformat(),
        'oral': id_oral,
    }
    db_update(db_facility_web.INSERT_TOKEN_SIGNATURE, **d)
    app.logger.info(f"Token: généré ({tk}) pour l'oral {id_oral}")
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
    app.logger.info(f"Token: vérifié et supprimé ({token}), valide={valid}")
    return valid


def clear_outdated_tokens(id_oral=None):
    """Supprime les tokens expirés ou liés à un oral spécifique."""
    results = db_get(db_facility_web.SELECT_TOKEN_SIGNATURE_ALL, no_list_auto=False)
    for res in results:
        if datetime.fromisoformat(res['time_limit']) <= datetime.now():
            db_update(db_facility_web.DELETE_TOKEN_SIGNATURE, token=res['token'])
            app.logger.info(f"Token: expiré supprimé ({res['token']})")
        elif id_oral is not None and str(res['oral']) == str(id_oral):
            db_update(db_facility_web.DELETE_TOKEN_SIGNATURE, token=res['token'])
            app.logger.info(f"Token: oral {id_oral} supprimé ({res['token']})")


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
    ine = StringField(
        'INE',
        render_kw={"autofocus": True, "placeholder": "Numéro INE"},
        validators=[DataRequired(message="Entrez votre INE")],
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
def index():
    return render_template(
        "index.html",
        url_of_page=request.url,
        centre=CENTRE_EXAMEN,
        authenticated=is_authenticated(),
        username=get_username(),
        admin=is_admin_user(),
        centre_examen=CENTRE_EXAMEN,
    )


@app.route('/c')
@nocache
def candidat_form_court():
    return redirect(url_for('candidat_form'))


@app.route("/candidat", methods=["GET"])
@nocache
def candidat_form():
    num = request.args.get("num", type=int, default=0)
    if num != 0:
        return redirect(url_for('candidat', id_candidat=num))
    return render_template("candidat_form.html", url_of_page=request.url,
                           centre=CENTRE_EXAMEN)


@app.route('/c/<id_candidat>')
@nocache
def candidat_court(id_candidat):
    return redirect(url_for('candidat', id_candidat=id_candidat))


@app.route("/candidat/<id_candidat>")
@nocache
def candidat(id_candidat=None):
    """Fiche candidat — RGPD : authentification requise."""
    # Candidat connecté : seulement sa propre fiche
    if is_student_user() and not is_student_user(id_candidat):
        abort(403)
    # Toute autre personne non authentifiée
    if not is_any_authenticated():
        # Stocker l'INE dans Redis (TTL 5 min, token aléatoire) pour éviter
        # qu'il apparaisse dans l'URL ET pour éviter qu'il soit lisible en
        # base64 dans le payload du cookie Flask (non chiffré).
        _ine_token = token_urlsafe(16)
        _r = _get_redis_pub_for_ine()
        _r.setex(f"login_ine:{_ine_token}", 300, str(id_candidat))
        session['_login_tok'] = _ine_token
        return redirect(url_for('login_candidat'))
    donnees_candidat = db_get(db_facility_web.SELECT_INFOS_CANDIDAT,
                              id_candidat, no_list_auto=False)
    if not donnees_candidat:
        abort(404, "Pas de candidat avec ce numéro")
    donnees_candidat = donnees_candidat[0]
    donnees_candidat['oraux'] = db_get(
        db_facility_web.SELECT_ORAUX_CANDIDAT, id_candidat, no_list_auto=False
    )
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
def loge_form():
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
def loge_court(id_loge):
    return redirect(url_for('loge', id_loge=id_loge))


@app.route("/loge/<id_loge>")
@nocache
def loge(id_loge):
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
    donnees_loge = db_get(db_facility_web.SELECT_INFOS_LOGE,
                          id_loge, no_list_auto=False)
    if not donnees_loge:
        abort(404, "Cette loge n'est pas dans la liste des loges utilisées")
    donnees_loge = donnees_loge[0]
    donnees_loge['oraux'] = db_get(
        db_facility_web.SELECT_ORAUX_LOGE, id_loge, no_list_auto=False
    )
    students_ine_list = (
        "[" + ",".join([repr(item['ine']) for item in donnees_loge['oraux']]) + "]"
    )
    return render_template(
        "loge.html",
        data=donnees_loge,
        students_ine=students_ine_list,
        authenticated=is_authenticated(),
        username=get_username(),
        centre=CENTRE_EXAMEN,
        sse_channel=f"loge_{id_loge}",
    )


@app.route("/liste", methods=["GET"])
@nocache
def liste():
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
def generate_screen_one():
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
def generate_doc_one(type_doc, id_doc=None):
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
        info_salle = db_get(db_facility_web.SELECT_INFOS_SALLE,
                            id_doc, no_list_auto=False)
        if not info_salle:
            abort(404)
        info_salle = info_salle[0]
        info_salle['oraux'] = db_get(
            db_facility_web.SELECT_ORAUX_SALLE, info_salle['id'], no_list_auto=False
        )
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
        donnees_loge = db_get(db_facility_web.SELECT_INFOS_LOGE,
                              id_doc, no_list_auto=False)
        if not donnees_loge:
            abort(404)
        donnees_loge = donnees_loge[0]
        donnees_loge['oraux'] = db_get(
            db_facility_web.SELECT_ORAUX_LOGE, id_doc, no_list_auto=False
        )
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
def login_examinateur():
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
    if len(infos) == 1 and check_password(passwd, infos[0]['password_hash']):
        session['user'] = salle
        app.logger.info(f"{salle}: connecté")
        return redirect(url_for('salle', id_salle=salle))
    app.logger.warning(f"{salle}: échec connexion")
    return redirect(url_for('login_examinateur', salle=salle,
                             message='Mot de passe incorrect'))


@app.route("/salle")
@nocache
def salle_form():
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
def salle_court(id_salle):
    return redirect(url_for('salle', id_salle=id_salle))


@app.route("/salle/<id_salle>")
@nocache
def salle(id_salle):
    """Fiche salle — RGPD : personnel uniquement."""
    if not is_authenticated():
        return redirect(url_for('login_examinateur', salle=id_salle))
    # Supprime les tokens de signature en attente pour cette salle
    # uniquement si c'est le propre examinateur de cette salle qui consulte
    if 'token_emargement' in session:
        session.pop('token_emargement')
    if 'user' in session and session['user'] == id_salle:
        db_update(db_facility_web.DELETE_SALLE_TOKEN_SIGNATURE, id_salle=id_salle)

    donnees_salle = db_get(db_facility_web.SELECT_INFOS_SALLE,
                           id_salle, no_list_auto=False)
    if not donnees_salle:
        abort(404, "Cette salle n'est pas dans la liste des salles utilisées")
    if len(donnees_salle) > 1:
        abort(500, "Contactez le secrétariat, il y a un problème de configuration")
    donnees_salle = donnees_salle[0]
    donnees_salle['oraux'] = db_get(
        db_facility_web.SELECT_ORAUX_SALLE, donnees_salle['id'], no_list_auto=False
    )
    students_ine_list = (
        "[" + ",".join([repr(item['ine']) for item in donnees_salle['oraux']]) + "]"
    )
    # L'admin peut émarger sur n'importe quelle salle
    is_userpage = is_admin_user() or (session.get('user') == donnees_salle['salle'])
    return render_template(
        "salle.html",
        centre=CENTRE_EXAMEN,
        data=donnees_salle,
        students_ine=students_ine_list,
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
def sign():
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
        session['token_emargement'] = hash_password(token_key + str(form.id_oral.data))
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
    expected_token = hash_password(token_key + id_oral) if token_key and id_oral else None
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
def request_token(id_oral):
    if 'token_emargement' not in session:
        return abort(403)
    token = generate_token(id_oral)
    image = qr(url_for('sign_other_device', token=token, _external=True), scale=5)
    app.logger.info(f"Token: demandé pour signature autre appareil ({token})")
    return jsonify({'token': token, 'image': image})


@app.route("/sign-other-device/<token>", methods=['GET', 'POST'])
@nocache
def sign_other_device(token):
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
def login():
    form = LoginAdminForm()
    if request.method == "GET":
        # Flask décode déjà les query params — pas besoin de unquote() supplémentaire
        form.link_back.data = request.args.get("link_back", '')
        return render_template('login.html', centre=CENTRE_EXAMEN,
                               hide_admin=True, username=get_username(), form=form)

    url = _safe_redirect_url(form.link_back.data)
    passcode = form.key.data
    if app._otp.verify(otp=passcode, valid_window=1):
        session['user'] = 'admin'
        app.logger.info("admin: connecté")
        return redirect(url or url_for("index"))

    app.logger.warning(f"admin: échec connexion code={passcode}")
    if url:
        return redirect(url_for('login', link_back=quote(url)))
    return redirect(url_for("login"))


@app.route("/logout")
@nocache
def logout():
    if 'user' in session:
        user = session.pop('user')
        app.logger.info(f"{user}: déconnecté")
    url = _safe_redirect_url(request.args.get("link_back"))
    return redirect(url or url_for('index'))


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Authentification candidats (élèves)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/login-candidat', methods=['GET', 'POST'])
@nocache
def login_candidat():
    """Connexion d'un candidat par INE + mot de passe du papillon."""
    form = LoginCandidatForm()
    if request.method == 'GET':
        message = request.args.get('message', None)
        # INE récupéré depuis Redis (usage unique, TTL 5 min)
        _tok = session.pop('_login_tok', '')
        if _tok:
            _r = _get_redis_pub_for_ine()
            _raw = _r.getdel(f"login_ine:{_tok}")
            ine = _raw.decode('utf-8') if _raw else ''
        else:
            ine = request.args.get('ine', '')
        if ine:
            form.ine.data = ine
        return render_template('login_candidat.html', centre=CENTRE_EXAMEN,
                               form=form, message=message)

    ine = form.ine.data.strip() if form.ine.data else ''
    password = form.password.data.strip() if form.password.data else ''
    candidat_info = db_get(db_facility_web.SELECT_CANDIDAT_AUTH,
                           ine, no_list_auto=False)
    if (len(candidat_info) == 1
            and check_password(password, candidat_info[0]['password_hash'])):
        session['candidat'] = ine
        app.logger.info(f"Candidat {ine}: connecté")
        return redirect(url_for('candidat', id_candidat=ine))
    app.logger.warning(f"Candidat {ine}: échec connexion")
    return redirect(url_for('login_candidat', message='INE ou mot de passe incorrect'))


@app.route('/logout-candidat')
@nocache
def logout_candidat():
    """Déconnexion d'un candidat."""
    if 'candidat' in session:
        ine = session.pop('candidat')
        app.logger.info(f"Candidat {ine}: déconnecté")
    return redirect(url_for('index'))


# ── Authentification loges ──────────────────────────────────────────────────

@app.route('/login-loge', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute")
def login_loge():
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
    if len(infos) == 1 and check_password(passwd, infos[0]['password_hash']):
        session['loge'] = loge_nom
        app.logger.info(f"Loge {loge_nom}: connectée")
        return redirect(url_for('loge', id_loge=loge_nom))
    app.logger.warning(f"Loge {loge_nom}: échec connexion")
    return redirect(url_for('login_loge', loge=loge_nom,
                             message='Mot de passe incorrect'))


@app.route('/logout-loge')
@nocache
def logout_loge():
    """Déconnexion d'un surveillant de loge."""
    if 'loge' in session:
        loge_nom = session.pop('loge')
        app.logger.info(f"Loge {loge_nom}: déconnectée")
    return redirect(url_for('index'))


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Administration (admin uniquement)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/gestion/reload-pages', methods=['GET'])
@admin_required
@nocache
def reload_pages():
    link = _safe_redirect_url(request.args.get("link_back"))
    sse.publish(" ", type='reload_page', channel='general')
    app.logger.info("SSE: rechargement de toutes les pages")
    if link is None:
        return "ok", 200
    return redirect(link)


@app.route("/gestion")
@admin_required
@nocache
def index_gestion():
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
def liste_examinateurs_json():
    matiere = request.args.get('matiere', -1)
    if matiere == -1:
        abort(400)
    liste = db_get(db_facility_web.SELECT_LISTE_EXAMINATEURS_PAR_MATIERE,
                   matiere, no_list_auto=False)
    return jsonify(liste)


@app.route("/gestion/edit-oral", methods=["GET", "POST"])
@admin_required
@nocache
def edit_oral():
    if request.method == "POST":
        d = {
            'id': request.form.get('id'),
            'examinateur': request.form.get('examinateur'),
            'heure_sujet': request.form.get('heure_sujet'),
            'heure_oral': request.form.get('heure_oral'),
            'mis_a_jour': 1 if request.form.get('mis_a_jour') == 'on' else 0,
        }
        ine = request.form.get('ine')
        db_update(db_facility_web.UPDATE_INFOS_ORAL, **d)
        if d['mis_a_jour'] == 1:
            # Récupère salle et loge pour publier sur les canaux ciblés
            exam = db_get(
                db_facility_web.SELECT_SALLE_LOGE_FROM_EXAMINATEUR,
                d['examinateur'],
                no_list_auto=False,
            )
            sse.publish(data=ine, type="data_updated", channel='general')
            if exam:
                sse.publish(data=ine, type="data_updated",
                            channel=f"salle_{exam[0]['salle']}")
                sse.publish(data=ine, type="data_updated",
                            channel=f"loge_{exam[0]['loge']}")
            sse.publish(data=ine, type="data_updated",
                        channel=f"candidat_{ine}")
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
def liste_examinateurs():
    examinateurs = db_get(db_facility_web.SELECT_LISTE_EXAMINATEURS, no_list_auto=False)
    return render_template(
        'liste_examinateurs.html',
        centre=CENTRE_EXAMEN,
        examinateurs=examinateurs,
        admin=is_admin_user(),
        url_of_page=request.url,
        username=get_username(),
    )


@app.route("/gestion/edit-examinateur", methods=['GET', 'POST'])
@admin_required
@nocache
def edit_examinateur():
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


@app.route('/gestion/delete-examinateur')
@admin_required
@nocache
def delete_examinateur():
    id_examinateur = request.args.get('id_examinateur', None)
    if id_examinateur is None:
        abort(404, "Pas d'examinateur avec ce numéro")
    db_update(db_facility_web.DELETE_EXAMINATEUR, id=id_examinateur)
    return redirect(url_for('liste_examinateurs'))


@app.route('/gestion/add-examinateur', methods=['GET', 'POST'])
@admin_required
@nocache
def add_examinateur():
    if request.method == "POST":
        d = {
            'nom': request.form.get('nom'),
            'salle': request.form.get('salle'),
            'matiere': request.form.get('matiere'),
            'loge': request.form.get('loge'),
            'etablissements': request.form.get('etablissements'),  # corrigé (était 'etablissments')
        }
        db_update(db_facility_web.INSERT_EXAMINATEUR, **d)
        return redirect(url_for('liste_examinateurs'))

    # GET
    liste_matieres = db_get(db_facility_web.SELECT_LISTE_MATIERES, no_list_auto=False)
    return render_template(
        "add_examinateur.html",
        centre=CENTRE_EXAMEN,
        liste_matieres=liste_matieres,
        url_of_page=request.url,
        username=get_username(),
    )


@app.route('/gestion/verify-logs')
@admin_required
@nocache
def verify_logs():
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
    )


@app.route('/generate-screen-batch', methods=['GET'])
@admin_required
@nocache
def generate_screen_batch():
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
def generate_doc_batch(type_doc, id_doc=None):
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
def algo_doc_exists(filename):
    """Indique si un PDF dans static/docs/ existe (utilisé par la page algo)."""
    if not re.match(r'^[\w\-. ]+\.pdf$', filename):
        abort(400)
    exists = (Path(app.root_path) / 'static' / 'docs' / filename).exists()
    return jsonify({"exists": exists,
                    "url": url_for('download', filename=filename) if exists else None})


@app.route('/download')
@nocache
def download():
    """
    Sert les fichiers PDF générés.
    - Les fiches candidats (candidat_*) sont publiques.
    - Les papillons (papillons_*) nécessitent d'être admin.
    - Tous les autres documents nécessitent d'être authentifié.
    """
    filename = request.args.get('filename', '')
    # Anti path-traversal : nom de fichier simple uniquement
    if not re.match(r'^[\w\-. ]+\.pdf$', filename):
        abort(400, "Nom de fichier invalide")

    if filename.startswith('papillons_'):
        if not is_admin_user():
            abort(403)
    elif not filename.startswith('candidat_'):
        if not is_authenticated():
            return redirect(url_for('login', link_back=request.url))

    app.logger.info(f"Téléchargement: {filename}")
    return send_from_directory('static/docs', filename)


@app.route('/about')
def about():
    return render_template("about.html", centre=CENTRE_EXAMEN,
                           hostname=HOSTNAME, username=get_username())


@app.route('/mentions-legales')
def mentions_legales():
    return render_template(
        "mentions_legales.html",
        centre=CENTRE_EXAMEN,
        fqdn=FQDN,
        director_name=DIRECTOR_NAME,
        centre_address=CENTRE_ADDRESS,
        academie=ACADEMIE,
        hebergeur=HEBERGEUR,
        dpd_email=DPD_EMAIL,
        username=get_username(),
    )


# ── Gestion de algo.py (admin) ────────────────────────────────────────────────

_DATA_DIR = Path(app.root_path).parent / "data"
_ALLOWED_CSV = {
    "candidats": "candidats.csv",
    "profs":     "profs_total.csv",
    "preps":     "preps.csv",
}


@app.route("/gestion/algo")
@admin_required
@nocache
def gestion_algo():
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
        csrf_token=generate_csrf(),
    )


@app.route("/gestion/algo/upload", methods=["POST"])
@admin_required
def algo_upload_csv():
    """Upload des fichiers CSV vers data/."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    uploaded, errors = [], []
    for field, target_name in _ALLOWED_CSV.items():
        f = request.files.get(field)
        if not f or not f.filename:
            continue
        if not f.filename.lower().endswith(".csv"):
            errors.append(f"{field} : extension .csv requise")
            continue
        dest = _DATA_DIR / target_name
        if dest.exists():
            dest.rename(dest.with_suffix(".csv.bak"))
        f.save(str(dest))
        uploaded.append(target_name)
        app.logger.info(f"CSV upload: {target_name}")
    return jsonify({"ok": not errors, "uploaded": uploaded, "errors": errors})


@app.route("/gestion/algo/run", methods=["POST"])
@admin_required
def algo_run():
    """Lance algo.py en tâche de fond; la sortie est streamée via SSE."""
    from algo_bg import run_algo as _run, is_running as _is_running
    from flask_sse import Message, _get_redis_pub
    if _is_running():
        return jsonify({"ok": False, "reason": "already_running"})

    # Résoudre l'URL Redis ICI (dans le contexte Flask) pour éviter
    # RuntimeError "Working outside of application context" dans le thread.
    redis_url = app.config.get("SSE_REDIS_URL") or app.config.get("REDIS_URL")
    redis_client = _get_redis_pub(redis_url)

    def _publish(data: str) -> None:
        msg = Message(data, type="algo_line")
        redis_client.publish(channel="algo_output",
                             message=json.dumps(msg.to_dict()))

    started = _run(_publish, db_host=os.environ.get("DB_HOST", "localhost"))
    app.logger.info(f"algo.py: {'démarré' if started else 'déjà en cours'}")
    return jsonify({"ok": started})


@app.route("/gestion/algo/status")
@admin_required
def algo_status():
    """Statut JSON de algo.py."""
    from algo_bg import is_running as _is_running
    return jsonify({"running": _is_running()})


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée développement
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=dev_on)
