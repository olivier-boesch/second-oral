"""Tests unitaires pour db_facility_save.py — sauvegarde DB de l'algo.

Vérifie en particulier que la parallélisation de Candidat.to_dict()/
Examinateur.to_dict() (via ThreadPoolExecutor, cf. algo.py) dans save_all()
ne casse pas l'ordre ni le contenu des dicts produits — ces appels
déclenchent hash_password() (scrypt), dominant largement le temps de
remplissage de la DB (~150ms/appel), d'où l'intérêt de les paralléliser.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webserver"))

if "webserver.app_secrets" not in sys.modules:
    _as = types.ModuleType("webserver.app_secrets")
    _as.CENTRE_EXAMEN = "Centre Test"
    _as.generate_password = lambda n=12: "TestPass12"
    _as.hash_password = lambda pw, identifier: f"hash({pw},{identifier})"
    _as.APP_SECRET_KEY = "test-key"
    _as.ACCENT_COLOR = "#336699"
    _as.DB_PARAMS = {
        "host": "localhost", "user": "u", "password": "p",
        "database": "d", "port": 3306,
        "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
    }
    _as.DB_SALT = "testsalt"
    sys.modules["webserver.app_secrets"] = _as
    if "app_secrets" not in sys.modules:
        sys.modules["app_secrets"] = _as

import mysql.connector  # noqa: E402

# D'autres fichiers de test (ex. test_algo.py) installent un db_facility_save
# factice (DbFacility = MagicMock) dans sys.modules pour éviter toute connexion
# réelle à l'import de algo.py — et peuvent le faire à tout moment pendant la
# collecte pytest (avant ou après ce module), donc s'appuyer sur sys.modules
# plus tard (ex. dans une fixture) n'est pas fiable. On importe ici le VRAI
# module et on garde une référence directe dessus, valable quel que soit ce
# que sys.modules contient par la suite.
sys.modules.pop("db_facility_save", None)
import db_facility_save as _real_db_facility_save  # noqa: E402


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **kw):
        pass

    def executemany(self, *a, **kw):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass


class _FakeObj:
    """Simule un Candidat/Examinateur/Matiere/Oral : to_dict() indépendant,
    pas d'état partagé muté — sûr à paralléliser via threads."""
    def __init__(self, ident):
        self.ident = ident

    def to_dict(self):
        return {"id": self.ident, "hash": f"hash-of-{self.ident}"}


class _FakeAlgo:
    def __init__(self, n_candidats=40, n_examinateurs=12, n_matieres=3, n_oraux=80):
        self.liste_candidats = [_FakeObj(f"cand{i}") for i in range(n_candidats)]
        self.liste_examinateurs = [_FakeObj(f"exam{i}") for i in range(n_examinateurs)]
        self.liste_matieres = [_FakeObj(f"mat{i}") for i in range(n_matieres)]
        self.liste_oraux = [_FakeObj(f"oral{i}") for i in range(n_oraux)]


@pytest.fixture
def db_facility(monkeypatch):
    monkeypatch.setattr(mysql.connector, "connect", lambda **kw: _FakeConn())
    return _real_db_facility_save.DbFacility()


class TestSaveAllParallelHashing:
    def test_dicts_preserve_input_order(self, db_facility, monkeypatch):
        calls = {}

        def _record(name):
            def _inner(rows):
                calls[name] = rows
            return _inner

        monkeypatch.setattr(db_facility, "save_matieres", _record("matieres"))
        monkeypatch.setattr(db_facility, "save_candidats", _record("candidats"))
        monkeypatch.setattr(db_facility, "save_examinateurs", _record("examinateurs"))
        monkeypatch.setattr(db_facility, "save_oraux", _record("oraux"))

        algo = _FakeAlgo()
        db_facility.save_all(algo)

        assert [d["id"] for d in calls["candidats"]] == [f"cand{i}" for i in range(40)]
        assert [d["id"] for d in calls["examinateurs"]] == [f"exam{i}" for i in range(12)]
        assert [d["id"] for d in calls["matieres"]] == [f"mat{i}" for i in range(3)]
        assert [d["id"] for d in calls["oraux"]] == [f"oral{i}" for i in range(80)]

    def test_idx_assigned_before_to_dict(self, db_facility, monkeypatch):
        monkeypatch.setattr(db_facility, "save_matieres", lambda rows: None)
        monkeypatch.setattr(db_facility, "save_candidats", lambda rows: None)
        monkeypatch.setattr(db_facility, "save_examinateurs", lambda rows: None)
        monkeypatch.setattr(db_facility, "save_oraux", lambda rows: None)

        algo = _FakeAlgo(n_candidats=5, n_examinateurs=2, n_matieres=1, n_oraux=10)
        db_facility.save_all(algo)

        assert [c.idx for c in algo.liste_candidats] == list(range(1, 6))
        assert [e.idx for e in algo.liste_examinateurs] == list(range(1, 3))


class TestSchemaLogeId:
    """Refonte 2026-07-09 : Examinateur.loge devient loge_id (FK vers Loge.id)
    au lieu d'un texte libre dupliqué — un renommage de loge n'invalide plus
    l'authentification ni le papillon (cf. incident 2026-07-08)."""

    def _sql_for(self, table_name: str) -> str:
        marker = f"CREATE TABLE IF NOT EXISTS {table_name} ("
        for entry in _real_db_facility_save.SQL_BASE:
            if marker in entry["sql"]:
                return entry["sql"]
        raise AssertionError(f"CREATE TABLE {table_name} introuvable dans SQL_BASE")

    def test_loge_created_before_examinateur(self):
        """Examinateur.loge_id référence Loge.id : Loge doit déjà exister."""
        noms_tables = [
            entry["sql"].split("CREATE TABLE IF NOT EXISTS ")[1].split(" ")[0]
            for entry in _real_db_facility_save.SQL_BASE
            if "CREATE TABLE IF NOT EXISTS" in entry["sql"]
        ]
        assert noms_tables.index("Loge") < noms_tables.index("Examinateur")

    def test_examinateur_loge_id_foreign_key(self):
        sql = self._sql_for("Examinateur")
        assert "loge_id" in sql
        assert "loge TEXT" not in sql
        assert "FOREIGN KEY (loge_id) REFERENCES Loge (id)" in sql

    def test_loge_nom_unique(self):
        sql = self._sql_for("Loge")
        assert "UNIQUE" in sql

    def test_sql_insert_loges_requires_explicit_id(self):
        """Pas d'AUTO_INCREMENT ici : l'id est assigné en Python (comme pour
        Matiere/Candidat/Examinateur/Oral) pour être connu avant le hash du
        mot de passe, qui l'utilise comme sel."""
        assert "%(id)s" in _real_db_facility_save.SQL_INSERT_LOGES

    def test_sql_insert_examinateurs_uses_loge_id(self):
        assert "loge_id" in _real_db_facility_save.SQL_INSERT_EXAMINATEURS
        assert "%(loge)s" not in _real_db_facility_save.SQL_INSERT_EXAMINATEURS

    def test_save_loges_before_save_all(self, db_facility, monkeypatch):
        """save_loges() doit pouvoir être appelée avant save_all() sans lever
        (executemany sur une liste de dicts id/nom/password_hash)."""
        db_facility.save_loges([{"id": 1, "nom": "B404", "password_hash": "h"}])
        # Pas d'assertion de contenu (curseur factice) — non-régression : ne
        # doit pas planter avec la nouvelle signature (liste, plus dict).


class TestSchemaPassageLoge:
    """Vue loge : le passage d'un candidat en loge est persisté en base
    (Oral.passage_loge), contrairement aux minuteurs qui ne vivent que dans
    Redis avec une expiration de 24h."""

    def _sql_for(self, table_name: str) -> str:
        marker = f"CREATE TABLE IF NOT EXISTS {table_name} ("
        for entry in _real_db_facility_save.SQL_BASE:
            if marker in entry["sql"]:
                return entry["sql"]
        raise AssertionError(f"CREATE TABLE {table_name} introuvable dans SQL_BASE")

    def test_oral_has_passage_loge_column(self):
        sql = self._sql_for("Oral")
        assert "passage_loge BOOLEAN NOT NULL DEFAULT FALSE" in sql
