"""Tests d'intégration Flask — page de monitoring Jour J (/gestion/jour-j)."""

import json

import pytest


class TestJourJ:
    """Page de monitoring seule (/gestion/jour-j) : état ambiant (algo, pause
    méridienne) + supervision technique. Les actions de rééquilibrage
    (disponibilité examinateur, changement de matière) ont été retirées le
    2026-07-11 — elles font désormais double emploi avec les boutons
    déjà présents sur /gestion/liste-examinateurs et /gestion/candidats."""

    @pytest.fixture(autouse=True)
    def _isolation(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, "_ALGO_PARAMS_FILE", tmp_path / "algo_params.json")

    def test_ok_page_monitoring(self, admin_client, db_mock):
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert "au repos" in body
        assert "non configurée" in body  # aucune pause méridienne par défaut

    def test_actions_rapides_retirees(self, admin_client, db_mock):
        """Ne doit plus proposer les raccourcis disponibilité/changement de
        matière — devenus redondants avec liste-examinateurs/candidats."""
        r = admin_client.get("/gestion/jour-j")
        body = r.data.decode()
        assert "Disponibilité d'un examinateur" not in body
        assert "Changement de matière d'un candidat" not in body

    def test_algo_en_cours_affiche(self, admin_client, db_mock, monkeypatch):
        import algo_bg
        monkeypatch.setattr(algo_bg, "is_running", lambda: True)
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "en cours…" in r.data.decode()

    def test_pause_meridienne_en_cours(self, admin_client, db_mock, tmp_path, monkeypatch):
        import app as app_module
        import datetime as _dt_module
        (tmp_path / "algo_params.json").write_text(json.dumps({
            "pause_meridienne_debut": "12:00", "pause_meridienne_duree": 30,
        }))

        class _FakeDatetime:
            @staticmethod
            def now(tz=None):
                return _dt_module.datetime(2026, 7, 6, 12, 10)

        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert "en cours (12:00" in body

    def test_pause_meridienne_a_venir(self, admin_client, db_mock, tmp_path, monkeypatch):
        import app as app_module
        import datetime as _dt_module
        (tmp_path / "algo_params.json").write_text(json.dumps({
            "pause_meridienne_debut": "12:00", "pause_meridienne_duree": 30,
        }))

        class _FakeDatetime:
            @staticmethod
            def now(tz=None):
                return _dt_module.datetime(2026, 7, 6, 9, 0)

        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "à venir (12:00" in r.data.decode()

    def test_section_monitoring_affichee_meme_si_redis_indisponible(self, admin_client, db_mock, monkeypatch):
        """La section supervision technique (ex-page /gestion/monitoring) s'affiche
        même si Redis est indisponible."""
        import app as app_module
        monkeypatch.setattr(app_module, "_redis",
                            lambda: (_ for _ in ()).throw(OSError("Redis KO")))
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        assert "Redis indisponible" in r.data.decode()

    def test_sessions_actives_lient_salle_et_loge(self, admin_client, db_mock, monkeypatch):
        """Les sessions actives (supervision technique) doivent lier la salle et
        la loge vers leur fiche en direct (cf. project_liens_admin)."""
        import app as app_module

        class _FakeRedis:
            def get(self, key):
                return {
                    'stats:online:exam:A1': b'1.2.3.4',
                    'stats:online:loge:L1': b'5.6.7.8',
                }.get(key)

            def scan_iter(self, pattern):
                return iter([b'stats:online:exam:A1', b'stats:online:loge:L1'])

        monkeypatch.setattr(app_module, "_redis", lambda: _FakeRedis())
        r = admin_client.get("/gestion/jour-j")
        assert r.status_code == 200
        body = r.data.decode()
        assert '<a href="/examinateur/A1" target="_blank">A1</a>' in body
        assert '<a href="/loge/L1" target="_blank">L1</a>' in body


