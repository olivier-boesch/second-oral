"""Tests d'intégration Flask — état du minuteur de loge (SSE) et
passage en loge (Oral.passage_loge)."""


# ── Timer de loge ─────────────────────────────────────────────────────────────

class TestTimerState:
    def test_timer_state_get_requires_auth(self, client):
        r = client.get("/loge/timer-state?loge=C107", follow_redirects=False)
        assert r.status_code == 403

    def test_timer_state_post_requires_auth(self, client):
        r = client.post("/loge/timer-state",
                        json={"loge": "C107", "numero": "123", "sujet": "08:00",
                              "elapsed": 0, "running": False, "startedAt": None})
        assert r.status_code == 403


# ── Passage en loge (persisté en base, contrairement aux minuteurs Redis) ─────

class TestLogePassage:
    def test_requires_auth(self, client):
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 403

    def test_wrong_loge_forbidden(self, client):
        """Un surveillant d'une autre loge ne peut pas marquer le passage."""
        with client.session_transaction() as sess:
            sess["loge"] = "Loge B"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 403

    def test_loge_user_can_mark_passage(self, client, db_mock):
        db_mock.make_sql_update.reset_mock()
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "passage": True}
        db_mock.make_sql_update.assert_called_once()
        _, kwargs = db_mock.make_sql_update.call_args
        assert kwargs == {"id": 1, "loge": "Loge A", "passage_loge": True}

    def test_loge_user_can_unmark_passage(self, client, db_mock):
        with client.session_transaction() as sess:
            sess["loge"] = "Loge A"
        r = client.post("/loge/Loge%20A/passage/1", json={"passage": False})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "passage": False}

    def test_admin_can_mark_passage(self, admin_client, db_mock):
        r = admin_client.post("/loge/Loge%20A/passage/1", json={"passage": True})
        assert r.status_code == 200


