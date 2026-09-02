"""
Configuration Flask pour les tests d'intégration.

Stratégie :
- Injecter un module `app_secrets` fictif dans sys.modules avant tout import webserver
- Injecter un module `dev` avec dev_on=True → mode dev : pas de Talisman, pas de
  SERVER_NAME, Flask-Limiter en mémoire
- Patcher db_facility_web.DbInterface avant l'import de app.py pour éviter toute
  connexion MariaDB
- Désactiver CSRF (WTF_CSRF_ENABLED=False) pour simplifier les tests POST
"""

from __future__ import annotations

import sys
import types
from base64 import b64encode
from hashlib import scrypt, sha256
from pathlib import Path
from unittest.mock import MagicMock

import pyotp
import pytz
import pytest

# ── Chemins ───────────────────────────────────────────────────────────────────

WEBSERVER_DIR = Path(__file__).resolve().parents[2] / "webserver"
# Replace toujours webserver/ en tête de sys.path : pytest réinsère la racine
# du projet à cet endroit lors de la résolution des packages de test, ce qui
# ferait sinon résoudre `import reports`/`import db_facility_web` (modules
# présents à la fois en racine et dans webserver/) vers les modules racine —
# alors qu'en production (CWD = /app/webserver) ce sont ceux de webserver/ qui
# sont chargés. Sans ce forçage, app.py importerait par exemple le `reports.py`
# racine (façade limitée pour algo.py) au lieu de webserver/reports.py.
while str(WEBSERVER_DIR) in sys.path:
    sys.path.remove(str(WEBSERVER_DIR))
sys.path.insert(0, str(WEBSERVER_DIR))

# ── 1. Module dev (dev_on=True) ───────────────────────────────────────────────
# Déclenche le chemin de configuration sans Talisman, SERVER_NAME ni Redis obligatoire.

if "dev" not in sys.modules:
    _dev = types.ModuleType("dev")
    _dev.dev_on = True
    sys.modules["dev"] = _dev

# ── 2. Module app_secrets factice ────────────────────────────────────────────

_TEST_PEPPER = "testpepper"
_TEST_SALT   = "testsalt2"
_TEST_OTP    = "JBSWY3DPEHPK3PXP"   # clé base32 valide


def _hash_pw(pw: str, identifier: str = "") -> str:
    salt = sha256(f"{_TEST_SALT}:{identifier}".encode("utf8")).hexdigest()
    return b64encode(scrypt(
        password=(pw + _TEST_PEPPER).encode("utf8"),
        salt=salt.encode("utf8"),
        n=2048, r=8, p=2,
    )).decode("utf8")


def _verify_log(item, prev=""):
    return True


if "app_secrets" not in sys.modules:
    _as = types.ModuleType("app_secrets")
    _as.CENTRE_EXAMEN   = "Centre Test"
    _as.FQDN            = "localhost"
    _as.DIGITAL_SIGN    = True
    _as.APP_SECRET_KEY  = "test-secret-key-0123456789abcdef"
    _as.LOGIN_KEY       = _TEST_OTP
    _as.HOSTNAME        = "testhost"
    _as.TIMEZONE        = pytz.timezone("Europe/Paris")
    _as.DB_PARAMS       = {
        "host": "localhost", "user": "u", "password": "p",
        "database": "d", "port": 3306,
        "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
    }
    _as.DB_SALT          = "testsalt"
    _as.PASSWORD_LENGTH  = 8
    _as.PASSWORD_PEPPER  = _TEST_PEPPER
    _as.PASSWORD_SALT    = _TEST_SALT
    _as.DIRECTOR_NAME    = "Jean Test"
    _as.CENTRE_ADDRESS   = "1 rue de la Paix, 75001 Paris"
    _as.ACADEMIE         = "Académie de Test"
    _as.HEBERGEUR        = "TestHost SAS"
    _as.DPD_EMAIL        = "dpd@test.fr"
    _as.hash_password    = _hash_pw
    _as.check_password   = lambda pw, identifier, h: _hash_pw(pw, identifier) == h
    _as.generate_password = lambda n=12: "TestPass1234"
    _as.verify_log_item  = _verify_log
    sys.modules["app_secrets"] = _as

# ── 3. Mocker les modules système indisponibles en CI ────────────────────────

# pypdftk nécessite pdftk installé — on le remplace par un MagicMock
if "pypdftk" not in sys.modules:
    sys.modules["pypdftk"] = MagicMock()

# ── 4. Patcher DbInterface avant l'import de app.py ──────────────────────────

import db_facility_web  # noqa: E402 — doit être importé après le sys.path.insert

_db_mock = MagicMock()
_db_mock.make_sql_select.return_value = []
_db_mock.make_sql_update.return_value = None
_db_mock.con = MagicMock()
_db_mock.con.is_connected.return_value = True

# Remplace la classe sur le module (avant que app.py ne l'importe)
db_facility_web.DbInterface = MagicMock(return_value=_db_mock)

# ── 4. Import de l'app Flask ──────────────────────────────────────────────────

from app import app as _flask_app  # noqa: E402

# Flask("2ndOral_app") résout root_path sur le cwd au lieu de webserver/ →
# on le force explicitement pour que les templates et static soient trouvés.
_flask_app.root_path = str(WEBSERVER_DIR)

_flask_app.config["TESTING"]          = True
_flask_app.config["WTF_CSRF_ENABLED"] = False


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def flask_app():
    """Application Flask configurée pour les tests."""
    return _flask_app


@pytest.fixture(autouse=True)
def _reset_db_mock():
    """Réinitialise le mock DB avant chaque test (évite les fuites de side_effect).

    `reset_mock()` remet aussi les compteurs d'appels à zéro : sans lui, un test
    qui affirme `make_sql_update.call_count == 0` (« aucune écriture ne doit
    avoir lieu ») passe ou échoue selon les tests exécutés avant lui.
    """
    _db_mock.make_sql_select.reset_mock()
    _db_mock.make_sql_update.reset_mock()
    _db_mock.make_sql_select.side_effect = None
    _db_mock.make_sql_select.return_value = []
    _db_mock.make_sql_update.side_effect = None
    _db_mock.make_sql_update.return_value = None


@pytest.fixture(autouse=True)
def _isolate_credentials_store(tmp_path, monkeypatch):
    """Redirige le store credentials.enc vers tmp_path pour chaque test.

    _DATA_DIR/_CREDENTIALS_FILE sont des constantes calculées une seule fois,
    à l'import d'app.py, à partir d'app.root_path — donc figées pour toute la
    session de test, quel que soit le test. Sans cette isolation, tout test
    qui déclenche _save_credentials() (renouvellement de mot de passe, ajout
    d'examinateur...) sans le monkeypatcher lui-même écrit pour de vrai sur
    le disque à cet emplacement fixe, potentiellement hors du repo selon la
    résolution de root_path par Flask/pytest (déjà arrivé : écriture réelle
    dans ~/data/credentials.enc lors d'un run de la suite).
    """
    import app as app_module
    monkeypatch.setattr(app_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_CREDENTIALS_FILE", tmp_path / "credentials.enc")
    monkeypatch.setattr(app_module, "_CREDENTIALS_TMP_FILE", tmp_path / "credentials_new.json")


@pytest.fixture()
def client(flask_app):
    """Client de test Flask (session isolée par test)."""
    with flask_app.test_client() as c:
        yield c


@pytest.fixture()
def admin_client(flask_app):
    """Client de test avec session admin déjà ouverte."""
    with flask_app.test_client() as c:
        with flask_app.test_request_context():
            pass
        with c.session_transaction() as sess:
            sess["user"] = "admin"
        yield c


@pytest.fixture()
def db_mock(_reset_db_mock):
    """Expose le mock DbInterface pour configurer des retours par test."""
    return _db_mock
