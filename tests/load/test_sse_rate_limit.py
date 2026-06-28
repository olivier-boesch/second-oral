"""
Test empirique du rate limiting SSE — à lancer contre une instance en cours d'exécution.

Scénario : simule N clients qui se reconnectent au flux SSE en rafale (après une
coupure réseau ou un refresh onglet), mesure le % de réponses 429 (rate limited).

Usage :
    # Récupérer un cookie de session valide depuis le navigateur (DevTools → Application
    # → Cookies → session) après s'être connecté comme admin ou examinateur.
    python3 tests/load/test_sse_rate_limit.py \\
        --url https://stex.mesoraux.fr \\
        --cookie "session=<valeur_cookie>" \\
        --channel salle_A101 \\
        --clients 50 \\
        --requests 6

    # Ou en local (dev) :
    python3 tests/load/test_sse_rate_limit.py \\
        --url http://localhost:8080 \\
        --cookie "session=<valeur_cookie>" \\
        --channel salle_A101

Interprétation :
    - 0 % de 429 : limite trop haute, peut permettre un DoS (flot de greenlets Redis)
    - < 5 % de 429 : acceptable pour des reconnexions légitimes après coupure réseau
    - > 20 % de 429 : limite trop basse, des clients légitimes seront bloqués lors
      d'un pic de reconnexion (ex. coupure WIFI en salle)

    Attention : si tous les candidats partagent un même NAT (cas fréquent en lycée),
    les requêtes de TOUS les onglets sont comptées contre la même IP.
    Avec 50 candidats × 2 onglets = 100 reconnexions simultanées → 100 req/min par IP.
    La limite actuelle est 30/min : dans ce cas, ajuster à 100-150/min.
"""

import argparse
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin


def _sse_request(base_url: str, channel: str, cookie: str, timeout: float = 5.0) -> int:
    """
    Ouvre une connexion SSE et retourne le code HTTP.
    Ferme la connexion immédiatement après les headers (on ne lit pas le flux).
    """
    url = urljoin(base_url, f"/stream?channel={channel}")
    req = Request(url, headers={
        "Cookie": cookie,
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status
    except HTTPError as e:
        return e.code
    except URLError:
        return 0


def run_test(base_url: str, channel: str, cookie: str,
             n_clients: int, requests_per_client: int, burst_delay: float) -> dict:
    """
    Lance n_clients × requests_per_client requêtes SSE en parallèle (burst).
    Retourne les statistiques.
    """
    total = n_clients * requests_per_client
    codes: list[int] = []
    latencies: list[float] = []

    print(f"\n{'='*60}")
    print(f"Cible       : {base_url}/stream?channel={channel}")
    print(f"Clients     : {n_clients}  ×  {requests_per_client} req  =  {total} requêtes")
    print(f"Délai burst : {burst_delay}s entre chaque vague de clients")
    print(f"{'='*60}\n")

    with ThreadPoolExecutor(max_workers=n_clients) as pool:
        futures = []
        for i in range(n_clients):
            for _ in range(requests_per_client):
                futures.append(pool.submit(_sse_request, base_url, channel, cookie))
            time.sleep(burst_delay)

        for f in as_completed(futures):
            t0 = time.perf_counter()
            code = f.result()
            latencies.append(time.perf_counter() - t0)
            codes.append(code)

    count_200  = codes.count(200)
    count_429  = codes.count(429)
    count_err  = sum(1 for c in codes if c not in (200, 429))
    pct_429    = count_429 / total * 100 if total else 0

    stats = {
        "total": total,
        "ok": count_200,
        "rate_limited": count_429,
        "errors": count_err,
        "pct_429": pct_429,
        "latency_median_ms": statistics.median(latencies) * 1000 if latencies else 0,
    }

    print(f"Résultats :")
    print(f"  200 OK         : {count_200:4d} ({count_200/total*100:.1f}%)")
    print(f"  429 rate limit : {count_429:4d} ({pct_429:.1f}%)")
    print(f"  Erreurs/timeout: {count_err:4d}")
    print(f"  Latence médiane: {stats['latency_median_ms']:.0f} ms")
    print()

    if pct_429 == 0:
        print("⚠  0 % de 429 — la limite est peut-être trop haute (risque DoS).")
        print("   Essayer avec plus de clients (--clients 100) ou un délai plus court.")
    elif pct_429 < 5:
        print("✓  < 5 % de 429 — limite acceptable pour des reconnexions légitimes.")
    elif pct_429 < 20:
        print("⚠  5-20 % de 429 — limite un peu serrée ; vérifier avec un scénario NAT.")
        print("   Si tous les candidats partagent un NAT, augmenter à 60-100/min.")
    else:
        print("✗  > 20 % de 429 — limite trop basse, des clients légitimes seront bloqués.")
        print("   Augmenter la limite SSE dans app.py : limiter.limit(\"X per minute\")(sse)")

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url",      default="http://localhost:8080", help="URL de base (défaut : http://localhost:8080)")
    parser.add_argument("--cookie",   required=True, help="Cookie de session (ex. 'session=abc123')")
    parser.add_argument("--channel",  default="salle_A101", help="Canal SSE à tester (défaut : salle_A101)")
    parser.add_argument("--clients",  type=int, default=50,  help="Nombre de clients simulés (défaut : 50)")
    parser.add_argument("--requests", type=int, default=6,   help="Requêtes par client (défaut : 6)")
    parser.add_argument("--delay",    type=float, default=0.05, help="Délai entre vagues (défaut : 0.05s)")
    args = parser.parse_args()

    run_test(
        base_url=args.url,
        channel=args.channel,
        cookie=args.cookie,
        n_clients=args.clients,
        requests_per_client=args.requests,
        burst_delay=args.delay,
    )
