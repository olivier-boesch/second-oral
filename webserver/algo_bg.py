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
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALGO_SCRIPT  = PROJECT_ROOT / "algo.py"

_process: subprocess.Popen | None = None
_lock = threading.Lock()

# Délai laissé à algo.py pour s'arrêter proprement après SIGTERM avant
# d'escalader vers SIGKILL (cf. stop_algo) — le moteur CP-SAT (OR-Tools)
# tourne sur plusieurs threads natifs (num_search_workers) pendant Solve() ;
# selon la façon dont ce pool de threads masque les signaux, SIGTERM peut
# rester en attente jusqu'à la fin du calcul au lieu d'interrompre le
# processus immédiatement. SIGKILL, lui, ne peut jamais être bloqué/ignoré.
_STOP_GRACE_PERIOD_S = 5.0


def is_running() -> bool:
    """Vrai si algo.py est en cours d'exécution."""
    with _lock:
        return _process is not None and _process.poll() is None


def stop_algo() -> bool:
    """Arrête algo.py s'il est en cours d'exécution.

    Envoie SIGTERM à tout le groupe de processus (algo.py + ses workers
    multiprocessing.Pool) plutôt qu'au seul processus parent, pour ne pas
    laisser de workers orphelins. Le thread `_stream` (déjà lancé) détecte
    la fin du processus et publie normalement le message `done`.

    Si le groupe de processus est toujours vivant après `_STOP_GRACE_PERIOD_S`
    secondes (ex. CP-SAT bloqué en calcul natif, SIGTERM resté en attente),
    un SIGKILL est envoyé en secours dans un thread séparé — SIGKILL ne peut
    ni être bloqué ni ignoré, contrairement à SIGTERM.

    :returns: True si un arrêt a été déclenché, False si rien ne tournait.
    """
    with _lock:
        proc = _process
        if proc is None or proc.poll() is not None:
            return False
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return False

    def _escalader_si_toujours_vivant() -> None:
        try:
            proc.wait(timeout=_STOP_GRACE_PERIOD_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    threading.Thread(target=_escalader_si_toujours_vivant, daemon=True).start()
    return True


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
            if "engine" in params:
                env["ALGO_ENGINE"] = str(params["engine"])
            if "cp_timeout" in params:
                env["ALGO_CP_TIMEOUT"] = str(params["cp_timeout"])
            if "cp_optimal" in params:
                env["ALGO_CP_OPTIMAL"] = "1" if params["cp_optimal"] else "0"
            if "pause_meridienne_debut" in params:
                env["ALGO_PAUSE_MERIDIENNE_DEBUT"] = str(params["pause_meridienne_debut"])
            if "pause_meridienne_duree" in params:
                env["ALGO_PAUSE_MERIDIENNE_DUREE"] = str(params["pause_meridienne_duree"])
            if "petites_matieres_fin_journee" in params:
                env["ALGO_PETITES_MATIERES_FIN_JOURNEE"] = (
                    "1" if params["petites_matieres_fin_journee"] else "0"
                )
            if "seuil_petite_matiere" in params:
                env["ALGO_SEUIL_PETITE_MATIERE"] = str(params["seuil_petite_matiere"])
            if "creneau_cible_fin_journee" in params:
                env["ALGO_CRENEAU_CIBLE_FIN_JOURNEE"] = str(params["creneau_cible_fin_journee"])
            if "poids_creneau_fin_journee" in params:
                env["ALGO_POIDS_CRENEAU_FIN_JOURNEE"] = str(params["poids_creneau_fin_journee"])
            if "poids_equite" in params:
                env["ALGO_POIDS_EQUITE"] = str(params["poids_equite"])
            if "bruit_tassement" in params:
                env["ALGO_BRUIT_TASSEMENT"] = str(params["bruit_tassement"])
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
            encoding="utf-8",
            # errors="replace" : filet de sécurité en cas de corruption résiduelle
            # (les runs parallèles sont désormais sérialisés côté algo.py via un
            # verrou inter-processus, mais on ne veut jamais faire planter le
            # streaming pour un octet mal décodé).
            errors="replace",
            bufsize=1,
            env=env,
            # Nouveau groupe de processus : permet de tuer algo.py ET les
            # processus worker qu'il lance via multiprocessing.Pool en un
            # seul signal (cf. stop_algo), plutôt que de ne tuer que le
            # processus parent et laisser les workers orphelins.
            start_new_session=True,
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
