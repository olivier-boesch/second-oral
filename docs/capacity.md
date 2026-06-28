# Capacité et performances

Ce document rassemble les limites théoriques dérivées du code et les valeurs mesurées en production.  
**Compléter les colonnes "Mesuré" après chaque session de test.**

---

## Algorithme de placement

### Capacité théorique

| Paramètre | Valeur par défaut | Formule |
|-----------|------------------|---------|
| Créneaux par examinateur | `ALGO_CRENEAUX = 13` | — |
| Examinateurs nécessaires (par matière) | — | `ceil(nb_candidats / ALGO_CRENEAUX)` |
| Écart mini entre oraux | `ALGO_ECART_MINI = 80 min` | — |
| Créneaux mini entre oraux | — | `ceil(ecart_mini / duree_oral + 1)` |
| Runs parallèles | `ALGO_N_RUN = 1000` | = nb CPUs utilisés au max |

Exemple avec 90 candidats (180 oraux), matières de 20 min, écart 80 min :
- Créneaux mini entre oraux : `ceil(80/20 + 1) = 5`
- Examinateurs nécessaires par matière : `ceil(90/13) = 7`

### Temps d'exécution mesuré (un run unique)

| Nb candidats | Nb examinateurs/matière | Temps resoudre+horaires | Date |
|-------------|------------------------|------------------------|------|
| 20  | 3  | < 10 ms (test automatisé) | 2026-06 |
| 50  | 6  | < 10 ms (test automatisé) | 2026-06 |
| 100 | 11 | < 10 ms (test automatisé) | 2026-06 |
| 180 | — | _à mesurer_ | — |
| 250 | — | _à mesurer_ | — |

### Temps total (1 000 runs parallèles)

| Nb candidats | Nb examinateurs | Runs réussis | 1000 runs | Sauvegarde DB | PDFs | Total | Date |
|-------------|----------------|-------------|-----------|--------------|------|-------|------|
| 99 (~session 2025) | 25 | 827/1000 (82.7%) | 24.5 s | 52.5 s | 0.3 s | **77 s** | 2026-06-28 |

> **Goulot d'étranglement :** la sauvegarde DB (DROP/CREATE/INSERT) représente 68% du temps total.  
> Les 1 000 runs de placement ne prennent que 24 secondes.

Pour mesurer :
```bash
time ./run_algo.sh
# ou depuis le site : /gestion/algo → log en direct
# Regarder "erreurs: X / 1000" et les timestamps de début/fin dans le log
```

---

## Rate limiting SSE

### Paramètre actuel

```python
# webserver/app.py
limiter.limit("300 per minute", override_defaults=True)(sse)
```

La limite s'applique **par IP**. En lycée, tous les candidats peuvent partager un même NAT (une seule IP visible côté serveur).

### Résultats mesurés — 2026-06-28 (2odev.mesoraux.fr)

| Requêtes envoyées | Limite | OK (200) | Bloqués (429) | Conclusion |
|-------------------|--------|----------|---------------|-----------|
| 200 rafale | 300/min | 200 (100%) | 0 (0%) | ✓ Sous la limite |
| 400 rafale | 300/min | 300 (75%) | 100 (25%) | ✓ Limite active, 300 premiers passent |

La limite se déclenche exactement à 300 : comportement conforme.

### Scénario de reconnexion simultanée (même NAT)

| Scénario | Req simultanées | Résultat avec 300/min |
|----------|----------------|----------------------|
| 90 candidats + 25 exam + loges (~120 total), 1 onglet | 120 | ✓ Tous passent |
| Même scénario, 2 onglets par personne | 240 | ✓ Tous passent |
| 200 connexions simultanées (cas extrême) | 200 | ✓ Tous passent |
| 300 connexions simultanées | 300 | ✓ Limite atteinte, suivants bloqués |

> **Référence terrain :** session 2025 = 90 candidats + ~25 examinateurs + loges ≈ 120 connexions SSE simultanées.  
> 300/min offre une marge ×2.5 par rapport au pic réel observé.

---

## Charge serveur Flask/gunicorn

| Métrique | Valeur configurée | Mesurée | Date |
|----------|------------------|---------|------|
| Workers gunicorn | 4 (gevent) | — | — |
| Greenlets max simultanés | illimité (gevent) | — | — |
| Candidats simultanés (SSE actifs) | — | _à mesurer_ | — |
| Débit (page publique `/`, 20 connexions) | — | **427 req/s** | 2026-06-28 |
| Latence p50 — page publique | — | **43 ms** | 2026-06-28 |
| Latence p95 — page publique | — | **68 ms** | 2026-06-28 |
| Latence p99 — page publique | — | **104 ms** | 2026-06-28 |
| Débit (redirect non-auth `/candidat/`, 10 connexions) | — | **318 req/s** | 2026-06-28 |
| Latence p50 — redirect non-auth | — | **30 ms** | 2026-06-28 |
| Latence p95 — redirect non-auth | — | **40 ms** | 2026-06-28 |
| Latence p99 — redirect non-auth | — | **47 ms** | 2026-06-28 |
| Débit (429 rate-limit, overhead nginx+gunicorn pur) | — | **299 req/s** | 2026-06-28 |
| Latence p50 — 429 (sans DB) | — | **31 ms** | 2026-06-28 |
| Latence p95 — 429 (sans DB) | — | **47 ms** | 2026-06-28 |
| Latence p99 — 429 (sans DB) | — | **61 ms** | 2026-06-28 |
| Débit (routes DB auth, 10 connexions simultanées) | — | **191–201 req/s** | 2026-06-28 |
| `/salle/B101` — p50 / p95 / p99 / max | — | 33 / 90 / 143 / 425 ms | 2026-06-28 |
| `/candidat/<id>` — p50 / p95 / p99 / max | — | 36 / 112 / 134 / 167 ms | 2026-06-28 |

> **Méthodologie :** 500 req, 10 connexions simultanées, cookie de session valide.  
> Les 200 premières ont été servies en 200 OK (pages avec requêtes DB), les 300 suivantes  
> en 429 (limite 200/h épuisée). Les métriques reflètent le mix — les latences DB réelles  
> sont proches de la médiane (p50).

```bash
# Page publique :
ab -n 1000 -c 20 https://FQDN/

# Route authentifiée avec cookie (noter la syntaxe Cookie: ) :
ab -n 500 -c 10 -H "Cookie: session=<valeur_cookie>" https://FQDN/candidat/1
```

---

## Checklist avant mise en production multi-école

- [ ] Algo testé avec le nombre réel de candidats de la session
- [ ] Test SSE réalisé (`tests/load/test_sse_rate_limit.py`) — résultats consignés ci-dessus
- [ ] Limite SSE ajustée si nécessaire
- [ ] `docker stats` observé pendant un run algo (CPU, RAM)
- [ ] `/health` répond 200 après le démarrage complet
