#!/usr/bin/env python3
"""
setup_new_site.py — Configure une nouvelle instance de 2ndOral.

Ce script doit être lancé avec les droits root (sudo) car il :
  1. Génère webserver/app_secrets.py (appartient à root, chmod 640)
  2. Crée la configuration nginx dans nginx-conf/ et /etc/nginx/sites-available/
  3. Lance certbot pour obtenir le certificat TLS

Usage interactif :
    sudo python setup_new_site.py

Usage en ligne de commande :
    sudo python setup_new_site.py \\
        --subdomain stex --domain mesoraux.fr \\
        --name "Lycée Saint Exupéry - Marseille" \\
        --db-user secondoral --db-password MOT_DE_PASSE
"""

import argparse
import base64
import getpass
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote as _urlquote

# Dépendances optionnelles (présentes dans le venv projet)
try:
    import segno as _segno
    _HAS_SEGNO = True
except ImportError:
    _HAS_SEGNO = False

try:
    import pyotp as _pyotp
    _HAS_PYOTP = True
except ImportError:
    _HAS_PYOTP = False

PROJECT_ROOT = Path(__file__).resolve().parent

# Compte système dédié, partagé par toutes les instances de l'app sur cette
# machine. Doit avoir le même UID/GID que « appuser » dans le conteneur
# Docker (cf. Dockerfile : groupadd/useradd --uid/--gid 1000 appuser), pour
# que les fichiers du bind-mount (ex. app_secrets.py) restent lisibles par
# le conteneur tout en appartenant à root côté hôte.
APP_SYSTEM_USER = "secondoral"
APP_SYSTEM_UID = 1000

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────
GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; BOLD = "\033[1m"; NC = "\033[0m"
ok   = lambda s: print(f"{GREEN}✔{NC}  {s}")
warn = lambda s: print(f"{YELLOW}⚠{NC}  {s}")
err  = lambda s: print(f"{RED}✘{NC}  {s}", file=sys.stderr)
hdr  = lambda s: print(f"\n{BOLD}── {s} ──{NC}")


# ── Helpers interactifs ───────────────────────────────────────────────────────

def ask(prompt: str, default: str = None, secret: bool = False,
        validator=None) -> str:
    """Pose une question avec valeur par défaut, masquage optionnel, validation."""
    while True:
        suffix = f" [{default}]" if default else ""
        full_prompt = f"  {prompt}{suffix}: "
        value = getpass.getpass(full_prompt) if secret else input(full_prompt).strip()
        if not value and default:
            value = default
        if value and (validator is None or validator(value)):
            return value
        if not value:
            warn("Ce champ est requis.")
        elif validator:
            warn("Valeur invalide.")


def is_domain_label(s: str) -> bool:
    return bool(re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?', s, re.I))


# ── Génération des secrets ────────────────────────────────────────────────────

def random_base32(n_bytes: int = 80) -> str:
    """Génère une clé OTP base32 (compatible pyotp/RFC 4648)."""
    return base64.b32encode(secrets.token_bytes(n_bytes)).decode('ascii')


def generate_app_secrets(fqdn: str, centre: str, db_user: str,
                         db_password: str, db_name: str,
                         db_host: str = "localhost",
                         digital_sign: bool = True,
                         director_name: str = "",
                         centre_address: str = "",
                         academie: str = "",
                         hebergeur: str = "",
                         dpd_email: str = "",
                         accent_color: str = "#6c63ff") -> tuple[str, str]:
    """
    Génère le contenu de app_secrets.py et retourne (contenu, clé_otp).
    """
    otp_key     = random_base32(80)          # 128 caractères base32
    secret_key  = secrets.token_hex(32)
    db_salt     = secrets.token_urlsafe(32)
    pwd_pepper  = secrets.token_hex(20)
    pwd_salt    = secrets.token_hex(13)

    content = f'''\
from subprocess import check_output
from hashlib import scrypt, sha256
from secrets import choice
from string import ascii_letters, digits
from base64 import b64encode
import pytz

# ── Centre d'examen ───────────────────────────────────────────────────────────
CENTRE_EXAMEN = "{centre}"

# Nom de domaine complet (utilisé par Flask SERVER_NAME)
FQDN = "{fqdn}"

# Signature dématérialisée (émargement en ligne des examinateurs)
DIGITAL_SIGN = {str(digital_sign)}

# ── Mentions légales et RGPD ──────────────────────────────────────────────────
DIRECTOR_NAME  = "{director_name}"
CENTRE_ADDRESS = "{centre_address}"
ACADEMIE       = "{academie}"
HEBERGEUR      = "{hebergeur}"
DPD_EMAIL      = "{dpd_email}"

# ── Authentification admin (TOTP) ─────────────────────────────────────────────
# Conserver cette clé et la configurer dans votre application TOTP
# (Open authenticator, FreeOTP, Google Authenticator, etc.).
LOGIN_KEY = '{otp_key}'

# ── Fuseaux horaires ──────────────────────────────────────────────────────────
TIMEZONE = pytz.timezone('Europe/Paris')

# ── Base de données MariaDB ───────────────────────────────────────────────────
DB_PARAMS = {{
    "host": "{db_host}",
    "user": "{db_user}",
    "password": "{db_password}",
    "database": "{db_name}",
    "port": 3306,
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
}}

# ── Secrets applicatifs ───────────────────────────────────────────────────────
APP_SECRET_KEY = "{secret_key}"
HOSTNAME = check_output(['hostname']).decode('utf8').strip('\\n')

# ── Couleur d'accent ──────────────────────────────────────────────────────────
# Utilisée pour la charte graphique du site et des PDFs.
# Modifier ici uniquement et relancer le setup pour changer le thème.
ACCENT_COLOR = "{accent_color}"
DB_SALT = '{db_salt}'

# ── Mots de passe ─────────────────────────────────────────────────────────────
PASSWORD_LENGTH = 12
PASSWORD_PEPPER = '{pwd_pepper}'
PASSWORD_SALT   = '{pwd_salt}'


def verify_log_item(log_item, previous_hash=''):
    data_to_hash = (
        f"{{log_item['action_data']}}{{log_item['table_name']}}"
        f"{{previous_hash}}{{DB_SALT}}"
    )
    return sha256(data_to_hash.encode()).hexdigest() == log_item['hash']


def _derive_salt(identifier):
    """
    Dérive un sel propre à `identifier` (INE, nom de salle, nom de loge...) à
    partir du sel global de l'application. Combiné au poivre (lui aussi
    propre à l'instance), cela évite qu'un sel statique unique ne s'applique
    à tous les comptes — deux comptes avec le même mot de passe produisent
    ainsi des empreintes différentes, et une table arc-en-ciel précalculée
    pour un compte ne peut pas être réutilisée pour un autre.
    """
    return sha256(f"{{PASSWORD_SALT}}:{{identifier}}".encode('utf8')).hexdigest().encode('utf8')


def hash_password(password, identifier=''):
    return b64encode(
        scrypt(
            password=(password + PASSWORD_PEPPER).encode('utf8'),
            salt=_derive_salt(identifier),
            n=2 ** 15, r=8, p=2,
            # OpenSSL refuse silencieusement les paramètres dont l'empreinte
            # mémoire (~128 * n * r * p octets) dépasse sa limite par défaut
            # (32 Mo) — on la relève explicitement pour ce coût plus élevé.
            maxmem=128 * 1024 * 1024,
        )
    ).decode('utf8')


def generate_password(password_len=PASSWORD_LENGTH):
    return ''.join(choice(ascii_letters + digits) for _ in range(password_len))


def check_password(password, identifier, hash_value):
    from hmac import compare_digest
    return compare_digest(hash_password(password, identifier), hash_value)
'''
    return content, otp_key


# ── Affichage / vérification OTP ─────────────────────────────────────────────

def build_totp_uri(otp_key: str, fqdn: str) -> str:
    """Construit l'URI standard otpauth:// pour les applications TOTP.

    Format unifié : issuer fixe « 2ndOral », account = fqdn.
    Cela permet à un admin gérant plusieurs instances de les distinguer
    dans son appli TOTP tout en les regroupant sous le même issuer.

    :param otp_key: Clé base32 TOTP.
    :param fqdn:    Nom de domaine complet de l'instance (ex. stex.mesoraux.fr).
    :returns: URI otpauth:// conforme RFC 6238.
    """
    issuer = "2ndOral"
    label = _urlquote(f"{issuer}:{fqdn}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={otp_key}"
        f"&issuer={_urlquote(issuer)}"
        f"&algorithm=SHA1&digits=6&period=30"
    )


def show_otp_setup(otp_key: str, centre: str, output_dir: Path,
                   fqdn: str = "2ndOral") -> None:
    """
    Affiche les informations de configuration TOTP :
      - QR code dans le terminal (si segno est disponible)
      - Sauvegarde PNG dans output_dir/otp_setup.png
      - Clé brute pour saisie manuelle
      - Vérification interactive optionnelle (si pyotp est disponible)

    :param otp_key:    Clé base32 TOTP.
    :param centre:     Nom du centre d'examen (affiché en clair uniquement).
    :param output_dir: Dossier de sortie pour le PNG du QR code.
    :param fqdn:       FQDN de l'instance, utilisé comme account dans l'URI TOTP.
    """
    uri = build_totp_uri(otp_key, fqdn=fqdn)

    print()

    # ── QR code terminal ──────────────────────────────────────────────────────
    if _HAS_SEGNO:
        qr = _segno.make_qr(uri)
        print(f"  {BOLD}Scannez ce QR code avec votre application TOTP :{NC}\n")
        qr.terminal(compact=True, border=2)
        print()

        # Sauvegarde PNG
        png_path = output_dir / "otp_setup.png"
        qr.save(str(png_path), scale=10)
        ok(f"QR code PNG sauvegardé → {png_path}")
    else:
        warn("segno non trouvé — QR code non affiché dans le terminal.")
        warn("Installez-le : pip install segno")
        print(f"\n  URI TOTP (à coller dans votre appli TOTP) :")
        print(f"  {BOLD}{uri}{NC}\n")

    # ── Clé brute ─────────────────────────────────────────────────────────────
    print(f"  {BOLD}Clé TOTP (saisie manuelle) :{NC}")
    # Affichage par groupes de 4 pour faciliter la lecture
    grouped = " ".join(otp_key[i:i+4] for i in range(0, len(otp_key), 4))
    print(f"  {YELLOW}{grouped}{NC}\n")

    # ── Vérification interactive ──────────────────────────────────────────────
    if _HAS_PYOTP:
        totp = _pyotp.TOTP(otp_key)
        print(f"  Code courant (valable ~30 s) : {BOLD}{totp.now()}{NC}")
        verify = input(
            "  Entrez le code affiché par votre appli TOTP pour vérifier "
            "(Entrée pour ignorer) : "
        ).strip()
        if verify:
            if totp.verify(verify, valid_window=1):
                ok("Code correct — configuration TOTP validée ✔")
            else:
                warn("Code incorrect. Vérifiez que l'horloge de votre appareil est synchronisée.")
    else:
        warn("pyotp non trouvé — vérification ignorée.")
        warn("Installez-le : pip install pyotp")


# ── PDF administrateur ───────────────────────────────────────────────────────

def generate_admin_pdf(otp_key: str, fqdn: str, centre: str,
                       output_dir: Path,
                       director_name: str = "",
                       academie: str = "",
                       dpd_email: str = "",
                       accent_color: str = "#6c63ff") -> Path | None:
    """
    Génère un PDF confidentiel pour l'administrateur contenant :
    - Titre 2ndOral + centre d'examen
    - Adresse du site
    - QR code TOTP (à scanner avec Aegis / Google Authenticator)
    - Clé TOTP brute (saisie manuelle)
    - URI TOTP complète
    - Démarches légales RGPD à effectuer par le chef de centre
    """
    try:
        import datetime as _dt
        import tempfile as _tempfile
        from reportlab.lib import colors as _rc, pagesizes as _rp
        from reportlab.lib.units import mm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
            Table, TableStyle, HRFlowable,
        )
        from reportlab.lib.utils import ImageReader
    except ImportError:
        warn("reportlab non disponible — PDF admin non généré.")
        return None

    if not _HAS_SEGNO:
        warn("segno non disponible — PDF admin non généré.")
        return None

    # ── Palette dérivée de la couleur d'accent choisie au setup ─────────────
    import sys as _sys
    _wdir = str(PROJECT_ROOT / 'webserver')
    if _wdir not in _sys.path:
        _sys.path.insert(0, _wdir)
    from theme import derive_palette as _derive_palette
    _pal = _derive_palette(accent_color)
    C_PRIMARY  = _rc.HexColor(_pal['primary'])
    C_SURFACE  = _rc.HexColor(_pal['surface'])
    C_SURFACE2 = _rc.HexColor(_pal['surface_2'])
    C_TEXT     = _rc.HexColor(_pal['text'])
    C_MUTED    = _rc.HexColor(_pal['text_sm'])
    C_WHITE    = _rc.white
    C_DANGER   = _rc.HexColor('#dc2626')

    # ── Police ────────────────────────────────────────────────────────────────
    font_path = PROJECT_ROOT / 'webserver' / 'static' / 'PoppinsLatin-Regular.ttf'
    font = 'Helvetica'
    if font_path.exists():
        try:
            pdfmetrics.registerFont(TTFont('Poppins', str(font_path)))
            font = 'Poppins'
        except Exception:
            pass
    font_b = 'Helvetica-Bold' if font == 'Helvetica' else font

    # ── Fichier de sortie ─────────────────────────────────────────────────────
    pdf_path = output_dir / 'admin_setup.pdf'
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=_rp.portrait(_rp.A4),
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"2ndOral — Configuration administrateur — {fqdn}",
        author="setup_new_site.py",
    )

    W = _rp.portrait(_rp.A4)[0] - 36 * mm  # largeur utile

    # ── Styles de texte ───────────────────────────────────────────────────────
    def ps(name, **kw):
        kw.setdefault('fontName', font)
        kw.setdefault('textColor', C_TEXT)
        return ParagraphStyle(name, **kw)

    st_big_title = ps('bigtitle', fontSize=32, textColor=C_WHITE,
                      fontName=font_b, alignment=TA_CENTER, leading=38)
    st_subtitle  = ps('subtitle',  fontSize=11, textColor=C_MUTED,
                      alignment=TA_CENTER, spaceAfter=4 * mm)
    st_section   = ps('section',   fontSize=13, textColor=C_PRIMARY,
                      fontName=font_b, spaceBefore=6 * mm, spaceAfter=3 * mm)
    st_label     = ps('label',     fontSize=9,  textColor=C_MUTED,
                      spaceBefore=1 * mm)
    st_value     = ps('value',     fontSize=11, fontName=font_b,
                      spaceAfter=2 * mm)
    st_key       = ps('key',       fontSize=13, fontName='Courier-Bold',
                      textColor=C_TEXT, alignment=TA_CENTER,
                      spaceBefore=3 * mm, spaceAfter=3 * mm)
    st_uri       = ps('uri',       fontSize=7,  fontName='Courier',
                      textColor=C_MUTED, alignment=TA_CENTER,
                      wordWrap='CJK')
    st_warning   = ps('warning',   fontSize=9,  textColor=C_DANGER,
                      fontName=font_b, alignment=TA_CENTER,
                      spaceBefore=5 * mm)
    st_foot      = ps('foot',      fontSize=7,  textColor=C_MUTED,
                      alignment=TA_CENTER)

    story = []

    # ── Bandeau titre ─────────────────────────────────────────────────────────
    title_table = Table(
        [[Paragraph("2ndOral", st_big_title)],
         [Paragraph("Document Administrateur", ps(
             'hdr2', fontSize=12, textColor=_rc.HexColor('#d4d0ff'),
             alignment=TA_CENTER))]],
        colWidths=[W],
    )
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_PRIMARY),
        ('TOPPADDING',    (0, 0), (-1, -1), 6 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('ROUNDEDCORNERS', [3 * mm]),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "⚠ CONFIDENTIEL — À conserver en lieu sûr — Ne pas distribuer",
        st_warning,
    ))
    story.append(HRFlowable(width=W, thickness=1, color=C_SURFACE2,
                             spaceAfter=4 * mm, spaceBefore=4 * mm))

    # ── Informations du site ──────────────────────────────────────────────────
    story.append(Paragraph("Informations du site", st_section))

    date_str = _dt.datetime.now().strftime("%d/%m/%Y à %H:%M")
    info_data = [
        ["Centre d'examen", centre],
        ["Adresse",         f"https://{fqdn}"],
        ["Configuré le",    date_str],
    ]
    info_table = Table(info_data, colWidths=[50 * mm, W - 50 * mm])
    info_table.setStyle(TableStyle([
        ('FONTNAME',   (0, 0), (0, -1), font_b),
        ('FONTNAME',   (1, 0), (1, -1), font),
        ('FONTSIZE',   (0, 0), (-1, -1), 10),
        ('TEXTCOLOR',  (0, 0), (0, -1), C_MUTED),
        ('TEXTCOLOR',  (1, 0), (1, -1), C_TEXT),
        ('BACKGROUND', (0, 0), (-1, -1), C_SURFACE),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [C_SURFACE, C_WHITE]),
        ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4 * mm),
        ('GRID', (0, 0), (-1, -1), 0.5, C_SURFACE2),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]))
    story.append(info_table)

    # ── QR code TOTP ──────────────────────────────────────────────────────────
    story.append(Paragraph("Authentification Administrateur (TOTP)", st_section))
    story.append(Paragraph(
        "Scannez ce QR code avec votre application TOTP "
        "(Open authenticator, FreeOTP, Google Authenticator, etc.)",
        st_subtitle,
    ))

    uri = build_totp_uri(otp_key, fqdn=fqdn)

    # Génère le QR code dans un fichier temporaire
    with _tempfile.NamedTemporaryFile(suffix='.png', delete=False) as _qr_tmp:
        _qr_path = _qr_tmp.name
    _segno.make_qr(uri).save(
        _qr_path, kind='png', scale=12, dpi=300,
        dark='#1e1b4b', light='#ffffff',
    )

    qr_size = 65 * mm
    qr_img = RLImage(_qr_path, width=qr_size, height=qr_size)
    qr_table = Table([[qr_img]], colWidths=[W])
    qr_table.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND',    (0, 0), (-1, -1), C_SURFACE),
        ('TOPPADDING',    (0, 0), (-1, -1), 5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5 * mm),
        ('ROUNDEDCORNERS', [3 * mm]),
    ]))
    story.append(qr_table)

    # ── Clé TOTP brute ────────────────────────────────────────────────────────
    story.append(Paragraph("Clé TOTP (saisie manuelle)", st_label))
    grouped = "  ".join(
        otp_key[i:i + 8] for i in range(0, len(otp_key), 8)
    )
    key_table = Table([[Paragraph(grouped, st_key)]], colWidths=[W])
    key_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_SURFACE2),
        ('TOPPADDING',    (0, 0), (-1, -1), 4 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4 * mm),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]))
    story.append(key_table)

    # ── URI TOTP ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("URI TOTP complète", st_label))
    story.append(Paragraph(uri, st_uri))

    # ── Démarches légales ─────────────────────────────────────────────────────
    story.append(Paragraph("Démarches légales (RGPD)", st_section))

    if director_name or academie:
        who = director_name or "le chef de centre"
        acad = f" ({academie})" if academie else ""
        story.append(Paragraph(
            f"En tant que directeur de publication{acad}, "
            f"<b>{who}</b> est responsable des traitements de données "
            "effectués via cette application.",
            ps('legal_intro', fontSize=9, textColor=C_TEXT,
               spaceAfter=3 * mm),
        ))

    steps = [
        (
            "1 — Registre des traitements",
            "Vérifier auprès du Délégué à la Protection des Données (DPD) "
            "de votre académie que ce traitement figure bien au registre "
            "académique (art. 30 RGPD). Si ce n'est pas le cas, le signaler "
            "pour inscription.",
        ),
        (
            "2 — Information des personnes concernées",
            "La page /mentions-legales du site remplit l'obligation d'information "
            "(art. 13 RGPD) envers les candidats et les examinateurs. "
            "Vérifier qu'elle est accessible et que les coordonnées du DPD y "
            "figurent correctement.",
        ),
        (
            "3 — Durée de conservation",
            "Les données doivent être supprimées à l'issue de la session d'examen, "
            "une fois les résultats définitifs et les délais de recours expirés. "
            "Planifier la suppression de la base de données avant la fin de "
            "l'année scolaire.",
        ),
        (
            "4 — Accès et habilitations",
            "S'assurer que seules les personnes habilitées disposent des codes "
            "d'accès (examinateurs, surveillants de loge). Détruire les papillons "
            "après les épreuves.",
        ),
        (
            "5 — Incident de sécurité",
            "En cas de violation de données (accès non autorisé, perte, "
            "divulgation), notifier le DPD dans les meilleurs délais. "
            "La CNIL doit être informée sous 72 h si la violation présente "
            "un risque pour les personnes (art. 33 RGPD).",
        ),
    ]

    dpd_line = (
        f"Contact DPD : {dpd_email}" if dpd_email
        else "Contact DPD : à renseigner dans /mentions-legales"
    )
    steps.append(("DPD et CNIL", dpd_line + "  —  www.cnil.fr"))

    step_data = [[Paragraph(f"<b>{title}</b>", ps(
                     f'st_{i}', fontSize=8, textColor=C_PRIMARY)),
                  Paragraph(body, ps(
                     f'sb_{i}', fontSize=8, textColor=C_TEXT))]
                 for i, (title, body) in enumerate(steps)]

    step_table = Table(step_data, colWidths=[42 * mm, W - 42 * mm])
    step_table.setStyle(TableStyle([
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2.5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 3 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 3 * mm),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [C_SURFACE, C_WHITE]),
        ('GRID',          (0, 0), (-1, -1), 0.3, C_SURFACE2),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]))
    story.append(step_table)

    # ── Note de sécurité ──────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=1, color=C_SURFACE2,
                             spaceAfter=3 * mm, spaceBefore=6 * mm))
    story.append(Paragraph(
        "Ce document contient les clés d'accès administrateur du site. "
        "Imprimez-le, conservez-le dans un endroit sécurisé, puis "
        "supprimez le fichier numérique.",
        st_foot,
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Généré par setup_new_site.py le {date_str}",
        st_foot,
    ))

    doc.build(story)

    # Nettoyage du QR temporaire
    try:
        Path(_qr_path).unlink()
    except Exception:
        pass

    return pdf_path


# ── Permissions nginx ────────────────────────────────────────────────────────

def fix_nginx_traversal(static_dir: Path) -> None:
    """
    S'assure que nginx (www-data) peut traverser chaque répertoire du chemin
    jusqu'à static_dir en ajoutant le bit o+x là où il manque.
    Sans ce bit, nginx retourne 403 même si les fichiers sont lisibles.
    """
    path = static_dir.resolve()
    fixed = []
    checked = []
    for parent in reversed([path] + list(path.parents)):
        if parent == Path('/'):
            continue
        mode = parent.stat().st_mode
        if not (mode & 0o001):  # bit x manquant pour "others"
            try:
                parent.chmod(mode | 0o001)
                fixed.append(parent)
            except PermissionError:
                warn(f"Permission refusée pour chmod o+x {parent}")
                warn(f"  Faites-le manuellement : sudo chmod o+x {parent}")
        else:
            checked.append(parent)

    if fixed:
        for p in fixed:
            ok(f"chmod o+x {p}  (nginx peut maintenant traverser ce dossier)")
    else:
        ok("Permissions de traversal nginx : OK")


# ── Compte système dédié à l'app ──────────────────────────────────────────────

def ensure_app_system_user(name: str = APP_SYSTEM_USER) -> None:
    """
    S'assure qu'un utilisateur système (et son groupe) dédié aux instances de
    l'app existe sur cette machine — le crée si besoin (UID/GID choisis
    automatiquement par le système, dans la plage des comptes système).

    Le reste du script référence ce compte par son nom (chown, build Docker) :
    seul le code qui doit franchir la frontière hôte/conteneur — où les noms
    n'ont pas de sens, seuls les UID/GID numériques comptent côté noyau —
    résout ce nom en UID/GID (cf. docker_setup, build args APP_UID/APP_GID).
    """
    import pwd

    try:
        pwd.getpwnam(name)
        return
    except KeyError:
        pass

    try:
        subprocess.run(["groupadd", "--system", name], check=True)
        subprocess.run(
            ["useradd", "--gid", name, "--system",
             "--no-create-home", "--shell", "/usr/sbin/nologin", name],
            check=True,
        )
        ok(f"Utilisateur système « {name} » créé")
    except subprocess.CalledProcessError:
        warn(f"Impossible de créer l'utilisateur système « {name} ».")
        warn(f"  Créez-le manuellement : groupadd --system {name} "
             f"&& useradd --gid {name} --system --no-create-home {name}")


# ── Répertoire de données (bind-monté dans le conteneur) ─────────────────────

def ensure_data_dir(data_dir: Path) -> None:
    """
    Crée data/ si besoin et s'assure qu'il appartient au compte système dédié
    (cf. APP_SYSTEM_USER). Ce dossier est bind-monté dans le conteneur
    (cf. docker-compose.yml : « .:/app ») et « appuser » — dont l'UID/GID
    correspond à APP_SYSTEM_USER côté hôte — doit pouvoir y écrire (upload
    des CSV via /gestion/algo/upload, cf. webserver/app.py:algo_upload_csv).
    Sans ce chown, le dossier appartient à root et l'upload échoue avec
    PermissionError.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.chown(data_dir, user=APP_SYSTEM_USER, group=APP_SYSTEM_USER)
        for path in data_dir.rglob("*"):
            shutil.chown(path, user=APP_SYSTEM_USER, group=APP_SYSTEM_USER)
        ok(f"data/ appartient à {APP_SYSTEM_USER}:{APP_SYSTEM_USER} (récursif)")
    except (PermissionError, LookupError):
        warn("Impossible de chown data/ (pas root ?). Faites-le manuellement :")
        warn(f"  sudo chown -R {APP_SYSTEM_USER}:{APP_SYSTEM_USER} {data_dir}")


# ── Détection du port disponible ──────────────────────────────────────────────

def find_free_port(start: int = 8080) -> int:
    """Retourne le premier port TCP libre sur 127.0.0.1 à partir de start."""
    for port in range(start, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Aucun port TCP libre trouvé à partir de {start}")


# ── Génération de la config nginx ─────────────────────────────────────────────

def generate_nginx_conf(fqdn: str, app_port: int = 8080) -> str:
    """Config HTTP uniquement — certbot ajoutera le bloc HTTPS et les directives ssl_*."""
    static_dir = PROJECT_ROOT / 'webserver' / 'static'
    return f'''\
##
# Second Oral — {fqdn}
# Généré par setup_new_site.py — SSL ajouté par certbot.
# Durcissement TLS global : /etc/nginx/conf.d/ssl-params.conf
##
server {{
    server_name {fqdn};

    root {static_dir};

    location / {{
        proxy_pass http://127.0.0.1:{app_port};

        proxy_set_header Host               $host;
        proxy_set_header X-Forwarded-For    $remote_addr;
        proxy_set_header X-Forwarded-Proto  $scheme;
        proxy_set_header X-Forwarded-Host   $host;
        proxy_set_header X-Forwarded-Prefix /;

        proxy_read_timeout  120s;
        proxy_connect_timeout 10s;
        proxy_buffering off;
    }}

    # ── SSE ───────────────────────────────────────────────────────────────────
    # Connexions longue durée, souvent inactives (pas de message tant que rien
    # ne change) : sans bloc dédié, le timeout de 120s ci-dessus coupe le flux
    # toutes les ~2 minutes (cf. nginx interne docker/nginx/second_oral.conf).
    location /stream {{
        proxy_pass http://127.0.0.1:{app_port};

        proxy_set_header Host               $host;
        proxy_set_header X-Forwarded-For    $remote_addr;
        proxy_set_header X-Forwarded-Proto  $scheme;
        proxy_set_header X-Forwarded-Host   $host;
        proxy_set_header X-Forwarded-Prefix /;

        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        chunked_transfer_encoding on;

        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;
        proxy_connect_timeout 10s;
    }}

    location /static/ {{
        alias {static_dir}/;
        expires 7d;
        add_header Cache-Control "public";
        access_log off;
    }}

    error_page 500 502 503 504 /error.html;
    location = /error.html   {{ }}
    location = /main.css     {{ }}
    location = /error_bg.jpg {{ }}

    # Les navigateurs demandent /favicon.ico à la racine (pas via /static/) :
    # servi directement par nginx depuis {static_dir}/favicon.ico (root ci-dessus).
    location = /favicon.ico  {{ }}

    listen 80;
    listen [::]:80;
}}
'''



# ── Fichier .env Docker ──────────────────────────────────────────────────────

def generate_env_file(db_user: str, db_password: str,
                      db_name: str, app_port: int = 8080,
                      app_uid: int = APP_SYSTEM_UID,
                      app_gid: int = APP_SYSTEM_UID,
                      sentry_dsn: str = "") -> tuple[str, str]:
    """
    Génère le contenu du fichier .env Docker et retourne (contenu, root_password).

    APP_UID/APP_GID sont écrits dans .env afin que `docker compose build`
    utilise toujours le bon UID/GID pour appuser — sans avoir à passer
    --build-arg manuellement à chaque rebuild.
    """
    db_root_password = secrets.token_urlsafe(24)
    sentry_line = (
        f"SENTRY_DSN={sentry_dsn}\n" if sentry_dsn
        else "# SENTRY_DSN=https://xxx@oyyy.ingest.sentry.io/zzz\n"
    )
    content = (
        "# Généré par setup_new_site.py — ne pas versionner\n"
        f"DB_ROOT_PASSWORD={db_root_password}\n"
        f"DB_NAME={db_name}\n"
        f"DB_USER={db_user}\n"
        f"DB_PASSWORD={db_password}\n"
        f"APP_PORT={app_port}\n"
        f"APP_UID={app_uid}\n"
        f"APP_GID={app_gid}\n"
        f"{sentry_line}"
    )
    return content, db_root_password


# ── Workflow Docker ───────────────────────────────────────────────────────────

def _compose(args_list: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Lance docker compose depuis PROJECT_ROOT."""
    return subprocess.run(
        ["docker", "compose"] + args_list,
        cwd=str(PROJECT_ROOT),
        **kwargs,
    )


def _compose_check(args_list: list[str]) -> None:
    """Lance docker compose et lève une exception en cas d'erreur."""
    _compose(args_list, check=True)


def wait_for_mariadb(db_root_password: str, timeout: int = 180) -> bool:
    """
    Attend que MariaDB réponde aux connexions (max timeout secondes).
    Vérifie en exécutant un SELECT 1 réel — plus fiable que le healthcheck Docker.
    """
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        result = _compose(
            ["exec", "-T", "mariadb",
             "mariadb", "-u", "root", f"-p{db_root_password}",
             "--silent", "--skip-column-names", "-e", "SELECT 1"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and "1" in result.stdout:
            return True
        if attempt % 6 == 0:  # log toutes les ~30 s
            elapsed = int(time.time() - (deadline - timeout))
            print(f"  … attente MariaDB ({elapsed}s)", end="\r", flush=True)
        time.sleep(5)
    print()  # saut de ligne après le \r
    return False


def init_mariadb(db_root_password: str, db_name: str,
                 db_user: str, db_password: str) -> None:
    """Crée la base et l'utilisateur applicatif dans le conteneur MariaDB."""
    sql = (
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
        f"  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; "
        f"CREATE USER IF NOT EXISTS '{db_user}'@'%' "
        f"  IDENTIFIED BY '{db_password}'; "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{db_name}`.* TO '{db_user}'@'%'; "
        f"FLUSH PRIVILEGES;"
    )
    _compose_check([
        "exec", "-T", "mariadb",
        "mariadb", "-u", "root", f"-p{db_root_password}",
        "-e", sql,
    ])


def docker_setup(db_root_password: str, db_name: str,
                 db_user: str, db_password: str) -> bool:
    """
    Build, démarrage, initialisation MariaDB, puis démarrage complet.
    Retourne True si tout s'est bien passé.
    """
    # Vérification de Docker
    r = subprocess.run(["docker", "compose", "version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        warn("docker compose non disponible. Installez Docker Desktop ou Docker Engine.")
        return False

    # Le conteneur a son propre /etc/passwd : impossible d'y résoudre le nom
    # « secondoral » de l'hôte. On ne peut transmettre au Dockerfile que les
    # UID/GID numériques du compte système dédié (cf. ensure_app_system_user),
    # afin que « appuser » dans le conteneur partage les mêmes et puisse lire
    # les fichiers du bind-mount qui lui appartiennent côté hôte (ex.
    # app_secrets.py).
    import pwd
    try:
        pw = pwd.getpwnam(APP_SYSTEM_USER)
        app_uid, app_gid = pw.pw_uid, pw.pw_gid
    except KeyError:
        warn(f"Utilisateur système « {APP_SYSTEM_USER} » introuvable, "
             f"utilisation de l'UID/GID par défaut ({APP_SYSTEM_UID}).")
        app_uid = app_gid = APP_SYSTEM_UID

    hdr("Construction de l'image Docker")
    try:
        _compose_check([
            "build", "--pull",
            "--build-arg", f"APP_UID={app_uid}",
            "--build-arg", f"APP_GID={app_gid}",
        ])
    except subprocess.CalledProcessError:
        err("La construction de l'image a échoué.")
        return False
    ok("Image construite.")

    hdr("Démarrage de MariaDB et Redis")
    try:
        _compose_check(["up", "-d", "mariadb", "redis"])
    except subprocess.CalledProcessError:
        err("Impossible de démarrer les services.")
        return False
    ok("Services démarrés.")

    print(f"\n  Attente que MariaDB soit prête (max 3 min)…")
    if not wait_for_mariadb(db_root_password):
        err("MariaDB n'a pas répondu dans les délais.")
        err("Vérifiez les logs : docker compose logs mariadb")
        return False
    print()
    ok("MariaDB prête.")

    hdr("Initialisation de la base de données")
    try:
        init_mariadb(db_root_password, db_name, db_user, db_password)
    except subprocess.CalledProcessError as e:
        err(f"Initialisation échouée : {e}")
        return False
    ok(f"Base '{db_name}' et utilisateur '{db_user}' créés.")

    hdr("Démarrage de la stack complète")
    try:
        _compose_check(["up", "-d"])
    except subprocess.CalledProcessError:
        err("Impossible de démarrer la stack complète.")
        return False
    ok("Stack démarrée.")
    return True


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure une nouvelle instance de 2ndOral",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--subdomain",      help="Sous-domaine (ex: stex)")
    parser.add_argument("--domain",         help="Domaine de base (ex: mesoraux.fr)")
    parser.add_argument("--name",           help="Nom du centre d'examen")
    parser.add_argument("--db-user",        help="Utilisateur MariaDB", default="secondoral")
    parser.add_argument("--db-password",    help="Mot de passe MariaDB")
    parser.add_argument("--db-name",        help="Nom de la base", default="SecondOral")
    parser.add_argument("--db-host",        help="Hôte MariaDB", default="localhost")
    parser.add_argument("--nginx-dir",      help="Dossier sites-available nginx",
                        default="/etc/nginx/sites-available")
    parser.add_argument("--certbot-email",    help="Email pour Let's Encrypt")
    parser.add_argument("--no-certbot",       action="store_true",
                        help="Ne pas lancer certbot")
    parser.add_argument("--no-digital-sign",  action="store_true",
                        help="Désactiver la signature dématérialisée")
    parser.add_argument("--director-name",    help="Nom du directeur de publication (chef de centre)")
    parser.add_argument("--centre-address",   help="Adresse postale du centre d'examen")
    parser.add_argument("--academie",         help="Académie de rattachement (ex: Académie d'Aix-Marseille)")
    parser.add_argument("--hebergeur",        help="Nom et adresse de l'hébergeur (ex: OVHcloud SAS, 2 rue Kellermann, 59100 Roubaix)")
    parser.add_argument("--dpd-email",        help="Email du Délégué à la Protection des Données de l'académie")
    parser.add_argument("--app-port",         type=int, default=None,
                        help="Port hôte du conteneur nginx (défaut: premier port libre ≥ 8080)")
    parser.add_argument("--sentry-dsn",         help="DSN Sentry pour le suivi des erreurs (optionnel)", default="")
    parser.add_argument("--no-update-lycees",  action="store_true",
                        help="Ne pas mettre à jour la liste des lycées depuis data.education.gouv.fr")
    parser.add_argument("--yes",              action="store_true",
                        help="Confirmer sans demander")
    args = parser.parse_args()

    # ── Collecte des informations ─────────────────────────────────────────────
    print(f"\n{BOLD}╔══════════════════════════════════════════╗{NC}")
    print(f"{BOLD}║  Configuration d'une nouvelle instance  ║{NC}")
    print(f"{BOLD}╚══════════════════════════════════════════╝{NC}\n")

    subdomain = args.subdomain or ask(
        "Sous-domaine", validator=is_domain_label,
    )
    domain = args.domain or ask(
        "Domaine de base (ex: mesoraux.fr)",
        validator=lambda s: "." in s and len(s) > 3,
    )
    fqdn = f"{subdomain}.{domain}"
    centre = args.name or ask("Nom du centre d'examen (ex: Lycée Saint Exupéry - Marseille)")

    hdr("Informations légales (mentions légales et RGPD)")
    director_name   = args.director_name  or ask(
        "Nom complet du directeur de publication (chef de centre d'examen)",
    )
    centre_address  = args.centre_address or ask(
        "Adresse postale du centre (ex: 13 avenue du Lycée, 13001 Marseille)",
    )
    academie        = args.academie        or ask(
        "Académie de rattachement (ex: Académie d'Aix-Marseille)",
    )
    hebergeur       = args.hebergeur       or ask(
        "Hébergeur — nom et adresse (ex: OVHcloud SAS, 2 rue Kellermann, 59100 Roubaix)",
    )
    dpd_email       = args.dpd_email       or ask(
        "Email du DPD de l'académie (ex: dpd@ac-aix-marseille.fr)",
        validator=lambda s: "@" in s,
    )

    db_user = args.db_user or ask("Utilisateur MariaDB", default="secondoral")
    db_name = args.db_name or ask("Nom de la base de données", default="SecondOral")
    db_host = args.db_host
    db_password = args.db_password or ask("Mot de passe MariaDB", secret=True)

    # Signature dématérialisée
    if args.no_digital_sign:
        digital_sign = False
    elif args.yes:
        digital_sign = True
    else:
        ds = input(
            "  Activer la signature dématérialisée (émargement en ligne) ? [O/n] "
        ).strip().lower()
        digital_sign = ds not in ("n", "non", "no")

    # Couleur d'accent
    _PALETTES = [
        ("#6c63ff", "Violet (défaut)"),
        ("#3b82f6", "Bleu"),
        ("#059669", "Vert"),
        ("#dc2626", "Rouge"),
        ("#ea580c", "Orange"),
        ("#0891b2", "Turquoise"),
    ]
    def _color_swatch(hex_c: str) -> str:
        """Retourne un carré coloré ANSI True Color pour affichage terminal."""
        h = hex_c.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"\033[48;2;{r};{g};{b}m   \033[0m"

    if args.yes:
        accent_color = _PALETTES[0][0]
    else:
        hdr("Couleur d'accent (charte graphique du site et des PDFs)")
        for i, (hex_c, label) in enumerate(_PALETTES, 1):
            swatch = _color_swatch(hex_c)
            print(f"  {i}. {swatch} {label:20s}  {hex_c}")
        print("  7. Personnalisée (#rrggbb)")
        choice_str = input("  Votre choix [1] : ").strip()
        if not choice_str:
            accent_color = _PALETTES[0][0]
        elif choice_str == "7":
            accent_color = ask(
                "Couleur hex",
                default="#6c63ff",
                validator=lambda s: len(s) == 7 and s.startswith("#"),
            )
        else:
            try:
                accent_color = _PALETTES[int(choice_str) - 1][0]
            except (ValueError, IndexError):
                warn("Choix invalide, utilisation du violet par défaut.")
                accent_color = _PALETTES[0][0]

    app_port = args.app_port if args.app_port is not None else find_free_port(8080)

    sentry_dsn = args.sentry_dsn
    if not sentry_dsn and not args.yes:
        print("  Sentry permet de recevoir une alerte email en cas d'erreur en production.")
        print("  Créez un projet gratuit sur https://sentry.io pour obtenir un DSN.")
        _raw = input("  DSN Sentry (laisser vide pour ignorer) : ").strip()
        sentry_dsn = _raw if _raw.startswith("https://") else ""
        if _raw and not sentry_dsn:
            warn("DSN invalide (doit commencer par https://), ignoré.")

    hdr("Récapitulatif")
    print(f"  Domaine        : {BOLD}{fqdn}{NC}")
    print(f"  Centre         : {centre}")
    print(f"  Directeur pub. : {director_name}")
    print(f"  Adresse        : {centre_address}")
    print(f"  Académie       : {academie}")
    print(f"  DPD            : {dpd_email}")
    print(f"  MariaDB        : {db_user}@{db_host}/{db_name}")
    print(f"  Port app       : {app_port}")
    print(f"  Signature      : {'✔ activée' if digital_sign else '✘ désactivée'}")
    print(f"  Accent         : {accent_color}")
    print(f"  Sentry         : {sentry_dsn if sentry_dsn else '(non configuré)'}")
    print(f"  Répertoire     : {PROJECT_ROOT}")
    print()

    if not args.yes:
        confirm = input("  Confirmer la création ? [o/N] ").strip().lower()
        if confirm not in ("o", "oui", "y", "yes"):
            print("Annulé.")
            sys.exit(0)

    # ── 1. app_secrets.py ─────────────────────────────────────────────────────
    hdr("Génération de app_secrets.py")
    secrets_path = PROJECT_ROOT / "webserver" / "app_secrets.py"

    if secrets_path.exists():
        bak = secrets_path.with_suffix(".py.bak")
        secrets_path.rename(bak)
        warn(f"Ancien fichier sauvegardé → {bak}")

    content, otp_key = generate_app_secrets(
        fqdn, centre, db_user, db_password, db_name, db_host, digital_sign,
        director_name, centre_address, academie, hebergeur, dpd_email,
        accent_color=accent_color,
    )
    secrets_path.write_text(content, encoding="utf-8")

    # security.txt — mise à jour du FQDN canonique
    sec_txt = PROJECT_ROOT / "webserver" / "static" / ".well-known" / "security.txt"
    if sec_txt.exists():
        sec_txt.write_text(
            sec_txt.read_text(encoding="utf-8").replace("FQDN_PLACEHOLDER", fqdn),
            encoding="utf-8",
        )
        ok(f"security.txt mis à jour → {sec_txt}")
    ensure_app_system_user()
    ensure_data_dir(PROJECT_ROOT / "data")
    try:
        os.chmod(secrets_path, 0o640)
        # Le groupe doit correspondre à celui de « appuser » dans le conteneur
        # Docker (cf. Dockerfile et ensure_app_system_user) :
        # root:{APP_SYSTEM_USER} 640 → seul root (hôte) et appuser (conteneur)
        # peuvent lire ce fichier.
        shutil.chown(secrets_path, user="root", group=APP_SYSTEM_USER)
    except (PermissionError, LookupError):
        warn("Impossible de chmod/chown (pas root ?). Faites-le manuellement :")
        warn(f"  sudo chown root:{APP_SYSTEM_USER} {secrets_path} && sudo chmod 640 {secrets_path}")
    ok(f"app_secrets.py → {secrets_path}")

    # Affichage QR + clé + vérification interactive
    hdr("Configuration TOTP (authentification admin)")
    show_otp_setup(otp_key, centre, PROJECT_ROOT, fqdn=fqdn)

    # PDF administrateur
    pdf = generate_admin_pdf(otp_key, fqdn, centre, PROJECT_ROOT,
                             director_name, academie, dpd_email,
                             accent_color=accent_color)
    if pdf:
        ok(f"PDF administrateur généré → {pdf}")
        warn("Imprimez ce document et supprimez le fichier numérique.")

    # ── 1b. Mise à jour de la liste des lycées ────────────────────────────────
    if not args.no_update_lycees:
        hdr("Mise à jour de la liste des lycées")
        try:
            import update_lycees as _ul
            rc = _ul.run(verbose=True)
            if rc != 0:
                warn("La mise à jour de la liste des lycées a échoué.")
                warn("Relancez manuellement : python update_lycees.py")
            else:
                ok("Liste des lycées à jour dans ods_handler.py")
        except Exception as _exc:
            warn(f"Impossible de mettre à jour la liste des lycées : {_exc}")
            warn("Relancez manuellement : python update_lycees.py")
    else:
        warn("Mise à jour des lycées ignorée (--no-update-lycees).")

    # ── 1c. Fichier .env Docker ───────────────────────────────────────────────
    hdr("Génération du fichier .env Docker")
    env_path = PROJECT_ROOT / ".env"
    # Résoudre l'UID/GID du compte système dès ici pour les écrire dans .env,
    # afin que docker compose build puisse toujours utiliser les bons args.
    import pwd as _pwd
    try:
        _pw = _pwd.getpwnam(APP_SYSTEM_USER)
        _app_uid, _app_gid = _pw.pw_uid, _pw.pw_gid
    except KeyError:
        _app_uid = _app_gid = APP_SYSTEM_UID
    env_content, db_root_password = generate_env_file(
        db_user, db_password, db_name, app_port,
        app_uid=_app_uid, app_gid=_app_gid,
        sentry_dsn=sentry_dsn,
    )

    if env_path.exists():
        env_bak = env_path.with_suffix(".env.bak")
        env_path.rename(env_bak)
        warn(f"Ancien .env sauvegardé → {env_bak}")

    env_path.write_text(env_content, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except PermissionError:
        warn("Impossible de chmod 600 sur .env")
    ok(f".env généré → {env_path}")

    # ── 2. Configuration nginx ────────────────────────────────────────────────
    hdr("Configuration nginx")
    nginx_content = generate_nginx_conf(fqdn, app_port)

    # Sauvegarde locale (toujours)
    local_conf = PROJECT_ROOT / "nginx-conf" / fqdn
    local_conf.parent.mkdir(exist_ok=True)
    local_conf.write_text(nginx_content, encoding="utf-8")
    ok(f"Config locale → {local_conf}")

    # Installation système
    nginx_dir = Path(args.nginx_dir)
    if nginx_dir.is_dir():
        dest_conf = nginx_dir / fqdn
        try:
            dest_conf.write_text(nginx_content, encoding="utf-8")
            ok(f"Installé → {dest_conf}")

            link = nginx_dir.parent / "sites-enabled" / fqdn
            if not link.exists():
                link.symlink_to(dest_conf)
                ok(f"Lien symbolique → {link}")

            test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
            if test.returncode == 0:
                subprocess.run(["nginx", "-s", "reload"], check=True)
                ok("nginx rechargé.")
            else:
                warn(f"nginx -t a signalé des erreurs :\n{test.stderr}")
        except PermissionError:
            warn(f"Permission refusée pour {nginx_dir}.")
            warn(f"Copiez manuellement : sudo cp {local_conf} {dest_conf}")
    else:
        warn(f"Dossier {nginx_dir} introuvable. Config locale disponible dans nginx-conf/")

    # ── 2b. Permissions de traversal nginx ───────────────────────────────────
    hdr("Permissions nginx")
    fix_nginx_traversal(PROJECT_ROOT / "webserver" / "static")

    # ── 3. Certbot ────────────────────────────────────────────────────────────
    if not args.no_certbot:
        hdr("Certificat TLS (Let's Encrypt)")
        email = args.certbot_email or ask("Email pour Let's Encrypt")
        try:
            subprocess.run([
                "certbot", "--nginx",
                "-d", fqdn,
                "--email", email,
                "--agree-tos",
                "--non-interactive",
            ], check=True)
            ok("Certificat TLS configuré.")
        except FileNotFoundError:
            warn("certbot introuvable.")
            warn("Installez : sudo apt install certbot python3-certbot-nginx")
            warn(f"Puis lancez : sudo certbot --nginx -d {fqdn}")
        except subprocess.CalledProcessError as e:
            warn(f"certbot a échoué (code {e.returncode}).")
            warn(f"Vérifiez que l'entrée DNS {fqdn} pointe sur ce serveur.")
    else:
        warn("Certbot ignoré (--no-certbot).")
        warn(f"Lancez manuellement : sudo certbot --nginx -d {fqdn}")

    # ── 4. Docker (optionnel) ─────────────────────────────────────────────────
    if not args.yes:
        do_docker = input(
            "\n  Lancer la configuration Docker maintenant "
            "(build + démarrage + init DB) ? [O/n] "
        ).strip().lower()
        skip_docker = do_docker in ("n", "non", "no")
    else:
        skip_docker = False

    docker_ok = False
    if not skip_docker:
        docker_ok = docker_setup(db_root_password, db_name, db_user, db_password)

    # ── Résumé ────────────────────────────────────────────────────────────────
    hdr("Résumé")
    ok("app_secrets.py généré et sécurisé")
    ok(".env Docker généré")
    ok("Configuration nginx générée")

    if skip_docker or not docker_ok:
        print()
        print(f"  Prochaines étapes manuelles :")
        print(f"    docker compose up -d --build")
        print(f"    ./run_algo.sh")
        print()
        print(f"  Pour mettre à jour la liste des lycées :")
        print(f"    python update_lycees.py")
    else:
        print()
        print(f"  Application accessible sur : https://{fqdn}")
        if not docker_ok:
            print(f"  ⚠ Des erreurs Docker se sont produites — vérifiez les logs.")

    print()
    ok("Configuration terminée.")


if __name__ == "__main__":
    main()
