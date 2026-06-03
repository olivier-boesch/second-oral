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
                         digital_sign: bool = True) -> tuple[str, str]:
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

# ── Authentification admin (TOTP) ─────────────────────────────────────────────
# Conserver cette clé et la configurer dans votre application TOTP
# (Aegis, Google Authenticator, etc.).
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
DB_SALT = '{db_salt}'

# ── Mots de passe ─────────────────────────────────────────────────────────────
PASSWORD_LENGTH = 8
PASSWORD_PEPPER = '{pwd_pepper}'
PASSWORD_SALT   = '{pwd_salt}'


def verify_log_item(log_item, previous_hash=''):
    data_to_hash = (
        f"{{log_item['action_data']}}{{log_item['table_name']}}"
        f"{{previous_hash}}{{DB_SALT}}"
    )
    return sha256(data_to_hash.encode()).hexdigest() == log_item['hash']


def hash_password(password):
    return b64encode(
        scrypt(
            password=(password + PASSWORD_PEPPER).encode('utf8'),
            salt=PASSWORD_SALT.encode('utf8'),
            n=2048, r=8, p=2,
        )
    ).decode('utf8')


def generate_password(password_len=PASSWORD_LENGTH):
    return ''.join(choice(ascii_letters + digits) for _ in range(password_len))


def check_password(password, hash_value):
    return hash_password(password) == hash_value
'''
    return content, otp_key


# ── Affichage / vérification OTP ─────────────────────────────────────────────

def build_totp_uri(otp_key: str, issuer: str = "2ndOral",
                   account: str = "Admin") -> str:
    """Construit l'URI standard otpauth:// pour les applications TOTP."""
    label = _urlquote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={otp_key}"
        f"&issuer={_urlquote(issuer)}"
        f"&algorithm=SHA1&digits=6&period=30"
    )


def show_otp_setup(otp_key: str, centre: str, output_dir: Path) -> None:
    """
    Affiche les informations de configuration TOTP :
      - QR code dans le terminal (si segno est disponible)
      - Sauvegarde PNG dans output_dir/otp_setup.png
      - Clé brute pour saisie manuelle
      - Vérification interactive optionnelle (si pyotp est disponible)
    """
    issuer = "2ndOral"
    uri = build_totp_uri(otp_key, issuer=issuer)

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


# ── Génération de la config nginx ─────────────────────────────────────────────

def generate_nginx_conf(fqdn: str) -> str:
    static_dir = PROJECT_ROOT / 'webserver' / 'static'
    return f'''\
##
# Second Oral — {fqdn}
# Généré par setup_new_site.py — SSL ajouté par certbot.
##
server {{
    server_name {fqdn};

    root {static_dir};

    location / {{
        proxy_pass http://127.0.0.1:8080;

        proxy_set_header Host               $host;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;
        proxy_set_header X-Forwarded-Host   $host;
        proxy_set_header X-Forwarded-Prefix /;

        proxy_read_timeout  120s;
        proxy_connect_timeout 10s;
        proxy_buffering off;
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

    listen 80;
    listen [::]:80;
}}
'''


# ── Fichier .env Docker ──────────────────────────────────────────────────────

def generate_env_file(db_user: str, db_password: str,
                      db_name: str) -> tuple[str, str]:
    """
    Génère le contenu du fichier .env Docker et retourne (contenu, root_password).
    Le mot de passe root MariaDB est généré aléatoirement et n'est utilisé
    que par Docker (pas dans app_secrets.py).
    """
    db_root_password = secrets.token_urlsafe(24)
    content = (
        "# Généré par setup_new_site.py — ne pas versionner\n"
        f"DB_ROOT_PASSWORD={db_root_password}\n"
        f"DB_NAME={db_name}\n"
        f"DB_USER={db_user}\n"
        f"DB_PASSWORD={db_password}\n"
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
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'%'; "
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

    hdr("Construction de l'image Docker")
    try:
        _compose_check(["build", "--pull"])
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
    parser.add_argument("--certbot-email",  help="Email pour Let's Encrypt")
    parser.add_argument("--no-certbot",      action="store_true",
                        help="Ne pas lancer certbot")
    parser.add_argument("--no-digital-sign", action="store_true",
                        help="Désactiver la signature dématérialisée")
    parser.add_argument("--yes",            action="store_true",
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

    hdr("Récapitulatif")
    print(f"  Domaine        : {BOLD}{fqdn}{NC}")
    print(f"  Centre         : {centre}")
    print(f"  MariaDB        : {db_user}@{db_host}/{db_name}")
    print(f"  Signature      : {'✔ activée' if digital_sign else '✘ désactivée'}")
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
        fqdn, centre, db_user, db_password, db_name, db_host, digital_sign
    )
    secrets_path.write_text(content, encoding="utf-8")
    try:
        os.chmod(secrets_path, 0o640)
    except PermissionError:
        warn("Impossible de chmod 640 (pas root ?). Faites-le manuellement.")
    ok(f"app_secrets.py → {secrets_path}")

    # Affichage QR + clé + vérification interactive
    hdr("Configuration TOTP (authentification admin)")
    show_otp_setup(otp_key, centre, PROJECT_ROOT)

    # ── 1b. Fichier .env Docker ───────────────────────────────────────────────
    hdr("Génération du fichier .env Docker")
    env_path = PROJECT_ROOT / ".env"
    env_content, db_root_password = generate_env_file(db_user, db_password, db_name)

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
    nginx_content = generate_nginx_conf(fqdn)

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
        if docker_ok:
            # Algo.py optionnel
            do_algo = input(
                "\n  Lancer algo.py maintenant pour initialiser la base "
                "(nécessite les fichiers CSV) ? [o/N] "
            ).strip().lower()
            if do_algo in ("o", "oui", "y", "yes"):
                hdr("Lancement de algo.py")
                try:
                    subprocess.run(
                        ["bash", "run_algo.sh"],
                        cwd=str(PROJECT_ROOT),
                        check=True,
                    )
                    ok("algo.py terminé.")
                except subprocess.CalledProcessError as e:
                    warn(f"algo.py a échoué (code {e.returncode}).")
                    warn("Relancez manuellement : ./run_algo.sh")

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
    else:
        print()
        print(f"  Application accessible sur : https://{fqdn}")
        if not docker_ok:
            print(f"  ⚠ Des erreurs Docker se sont produites — vérifiez les logs.")

    print()
    ok("Configuration terminée.")


if __name__ == "__main__":
    main()
