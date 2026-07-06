"""Tests unitaires pour algo_bg.py — gestion du run algo.py en tâche de fond."""
import subprocess
import sys
import time as _time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webserver"))

import algo_bg  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_process_state():
    """Isole chaque test : aucun process fantôme d'un test précédent."""
    algo_bg._process = None
    yield
    if algo_bg._process is not None and algo_bg._process.poll() is None:
        algo_bg._process.kill()
        algo_bg._process.wait()
    algo_bg._process = None


def _spawn_sleep(seconds: float = 30) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        start_new_session=True,
    )


class TestIsRunning:
    def test_false_when_no_process(self):
        assert algo_bg.is_running() is False

    def test_true_while_process_alive(self):
        algo_bg._process = _spawn_sleep()
        assert algo_bg.is_running() is True

    def test_false_after_process_exits(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        algo_bg._process = proc
        assert algo_bg.is_running() is False


class TestStopAlgo:
    def test_returns_false_when_nothing_running(self):
        assert algo_bg.stop_algo() is False

    def test_terminates_running_process(self):
        proc = _spawn_sleep()
        algo_bg._process = proc
        assert algo_bg.stop_algo() is True
        proc.wait(timeout=5)
        assert proc.poll() is not None

    def test_returns_false_when_process_already_exited(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        algo_bg._process = proc
        assert algo_bg.stop_algo() is False

    def test_escalade_vers_sigkill_si_sigterm_ignore(self, monkeypatch):
        """Régression : un process qui ignore SIGTERM (ex. CP-SAT bloqué en
        calcul natif, cf. commentaire sur _STOP_GRACE_PERIOD_S) doit quand
        même être arrêté, via l'escalade vers SIGKILL après le délai de grâce."""
        monkeypatch.setattr(algo_bg, "_STOP_GRACE_PERIOD_S", 0.3)
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             "print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        assert proc.stdout is not None
        proc.stdout.readline()  # attend que le handler SIG_IGN soit bien installé
        algo_bg._process = proc
        assert algo_bg.stop_algo() is True
        # SIGTERM seul est ignoré par le process -> il doit encore tourner juste après
        _time.sleep(0.05)
        assert proc.poll() is None
        # Passé le délai de grâce, l'escalade SIGKILL doit avoir eu lieu
        proc.wait(timeout=5)
        assert proc.poll() is not None
