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


def run_algo(publish_fn: Callable[[str], None], db_host: str | None = None,
             params: dict | None = None,
             on_done: Callable[[int], None] | None = None) -> bool:
    """
    Lance algo.py dans un thread séparé.

    :param publish_fn: callable(data_str) publiant une ligne sur le canal SSE.
                       data_str est un JSON : {"line": "...", "done": false}.
    :param db_host:    Hôte MariaDB (surcharge la valeur de app_secrets.py).
    :param params:     Paramètres algo (n_run, ecart_mini, heure_debut, creneaux, debug).
    :param on_done:    Callback appelé à la fin du processus avec le code de retour.
                       Utile pour traiter les fichiers produits par algo.py (ex. chiffrement
                       des credentials). Appelé dans le thread de streaming, pas dans celui
                       qui appelle run_algo.
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
            if "debug"       in params: env["ALGO_DEBUG"]       = "1" if params["debug"] else "0"
        # Force le mode non-bufférisé : chaque ligne est envoyée immédiatement
        # sans attendre le remplissage du buffer stdout Python (crucial pour le
        # streaming temps-réel vers le log console).
        env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(ALGO_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        _process = proc

    def _pub(line: str, *, done: bool = False, rc: int | None = None) -> None:
        payload: dict = {"line": line, "done": done}
        if rc is not None:
            payload["rc"] = rc
        publish_fn(json.dumps(payload))

    def _stream() -> None:
        global _process
        _pub("=== Démarrage de algo.py ===")
        rc = -1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                _pub(line.rstrip())
            proc.wait()
            rc = proc.returncode
            status = "succès ✔" if rc == 0 else f"erreur (code {rc}) ✘"
            _pub(f"=== Terminé — {status} ===", done=True, rc=rc)
        except Exception as exc:
            _pub(f"=== Erreur interne : {exc} ===", done=True, rc=-1)
        finally:
            with _lock:
                _process = None
            if on_done is not None:
                try:
                    on_done(rc)
                except Exception as exc:
                    _pub(f"=== Avertissement post-algo : {exc} ===")

    threading.Thread(target=_stream, daemon=True).start()
    return True
