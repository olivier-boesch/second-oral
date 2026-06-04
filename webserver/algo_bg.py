"""
Exécution de algo.py en tâche de fond avec streaming de la sortie via Redis/SSE.

Utilisation depuis app.py :
    from algo_bg import run_algo, is_running

    def publish(data):
        sse.publish(data, type='algo_line', channel='algo_output')

    run_algo(publish, db_host='mariadb')
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALGO_SCRIPT  = PROJECT_ROOT / "algo.py"
REDIS_CHANNEL = "algo_output"

_process: subprocess.Popen | None = None
_lock = threading.Lock()


def is_running() -> bool:
    """Vrai si algo.py est en cours d'exécution."""
    with _lock:
        return _process is not None and _process.poll() is None


def run_algo(publish_fn: Callable[[str], None], db_host: str = None,
             params: dict = None) -> bool:
    """
    Lance algo.py dans un thread séparé.

    :param publish_fn: callable(data_str) qui publie une ligne sur le canal SSE.
                       data_str est un JSON : {"line": "...", "done": false}.
    :param db_host:    Hôte MariaDB (surcharge la valeur de app_secrets.py).
    :param params:     Paramètres algo (n_run, ecart_mini, heure_debut, creneaux).
    :returns: True si lancé, False si algo tourne déjà.
    """
    global _process

    with _lock:
        if _process is not None and _process.poll() is None:
            return False

        env = dict(os.environ)
        if db_host:
            env["DB_HOST"] = db_host
        if params:
            if "n_run"       in params: env["ALGO_N_RUN"]       = str(params["n_run"])
            if "ecart_mini"  in params: env["ALGO_ECART_MINI"]  = str(params["ecart_mini"])
            if "heure_debut" in params: env["ALGO_HEURE_DEBUT"] = str(params["heure_debut"])
            if "creneaux"    in params: env["ALGO_CRENEAUX"]    = str(params["creneaux"])
        # Force le mode non-bufférisé : chaque ligne est envoyée immédiatement
        # sans attendre le remplissage du buffer stdout Python (crucial pour le
        # streaming temps-réel vers le log console).
        env["PYTHONUNBUFFERED"] = "1"

        _process = subprocess.Popen(
            [sys.executable, str(ALGO_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

    def _pub(line: str, *, done: bool = False, rc: int | None = None) -> None:
        payload: dict = {"line": line, "done": done}
        if rc is not None:
            payload["rc"] = rc
        publish_fn(json.dumps(payload))

    def _stream() -> None:
        global _process
        _pub("=== Démarrage de algo.py ===")
        try:
            for line in _process.stdout:
                _pub(line.rstrip())
            _process.wait()
            rc = _process.returncode
            status = "succès ✔" if rc == 0 else f"erreur (code {rc}) ✘"
            _pub(f"=== Terminé — {status} ===", done=True, rc=rc)
        except Exception as exc:
            _pub(f"=== Erreur interne : {exc} ===", done=True, rc=-1)
        finally:
            with _lock:
                _process = None

    threading.Thread(target=_stream, daemon=True).start()
    return True
