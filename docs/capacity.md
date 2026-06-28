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

Exemple avec 180 candidats, matières de 20 min, écart 80 min :
- Créneaux mini entre oraux : `ceil(80/20 + 1) = 5`
- Examinateurs nécessaires par matière : `ceil(180/13) = 14`

### Temps d'exécution mesuré (un run unique)

| Nb candidats | Nb examinateurs/matière | Temps resoudre+horaires | Date |
|-------------|------------------------|------------------------|------|
| 20  | 3  | < 10 ms (test automatisé) | 2026-06 |
| 50  | 6  | < 10 ms (test automatisé) | 2026-06 |
| 100 | 11 | < 10 ms (test automatisé) | 2026-06 |
| 180 | — | _à mesurer_ | — |
| 250 | — | _à mesurer_ | — |

### Temps total (1 000 runs parallèles)

| Nb candidats | Nb CPUs | Temps total | % runs réussis | Date |
|-------------|---------|-------------|---------------|------|
| 180 | _à mesurer_ | _à mesurer_ | _à mesurer_ | — |

Pour mesurer :
```bash
ALGO_N_RUN=1000 time python3 algo.py
# Regarder la dernière ligne du log : "erreurs: X / 1000"
```

---

## Rate limiting SSE

### Paramètre actuel

```python
# webserver/app.py
limiter.limit("30 per minute", override_defaults=True)(sse)
```

La limite s'applique **par IP**. En lycée, tous les candidats peuvent partager un même NAT (une seule IP visible côté serveur).

### Scénario de reconnexion

| Scénario | Req SSE/min estimées | Limite actuelle | Résultat |
|----------|---------------------|-----------------|---------|
| 10 candidats, 1 onglet, reconnexion toutes les 3s | 10 × 20 = 200/min | 30/min | ✗ bloquant si même NAT |
| 50 candidats, 1 onglet, reconnexion toutes les 3s | 50 × 20 = 1000/min | 30/min | ✗ très bloquant |
| 50 candidats, reconnexion 1× après coupure | 50 req en rafale | 30/min | ✗ bloquant |
| 1 candidat, 2 onglets | 2 req/reconnexion | 30/min | ✓ OK |

> **Conclusion provisoire :** si les candidats partagent un NAT, 30/min est trop bas.  
> Une valeur de **150-200/min** est plus adaptée à un groupe de 50 candidats derrière un routeur.

### Résultats mesurés (test empirique)

Utiliser le script `tests/load/test_sse_rate_limit.py` :

```bash
python3 tests/load/test_sse_rate_limit.py \
    --url https://FQDN \
    --cookie "session=<cookie_admin>" \
    --channel salle_A101 \
    --clients 50 \
    --requests 6
```

| Date | Clients | Req/client | Limite testée | % 429 | Conclusion |
|------|---------|-----------|--------------|-------|-----------|
| — | — | — | 30/min | _à mesurer_ | — |

### Recommandation

Après mesure, ajuster dans [webserver/app.py](../webserver/app.py) :

```python
limiter.limit("XXX per minute", override_defaults=True)(sse)
```

---

## Charge serveur Flask/gunicorn

| Métrique | Valeur configurée | Mesurée | Date |
|----------|------------------|---------|------|
| Workers gunicorn | 4 (gevent) | — | — |
| Greenlets max simultanés | illimité (gevent) | — | — |
| Candidats simultanés (SSE actifs) | — | _à mesurer_ | — |
| Pic de reconnexions supporté | — | _à mesurer_ | — |
| Temps de réponse médian (p50) | — | _à mesurer_ | — |
| Temps de réponse p95 | — | _à mesurer_ | — |

Pour mesurer avec `ab` (Apache Benchmark, paquet `apache2-utils`) :

```bash
# Charge sur la page publique (sans auth)
ab -n 1000 -c 20 https://FQDN/

# Avec session cookie :
ab -n 500 -c 10 -H "Cookie: session=<cookie>" https://FQDN/candidat/1
```

---

## Checklist avant mise en production multi-école

- [ ] Algo testé avec le nombre réel de candidats de la session
- [ ] Test SSE réalisé (`tests/load/test_sse_rate_limit.py`) — résultats consignés ci-dessus
- [ ] Limite SSE ajustée si nécessaire
- [ ] `docker stats` observé pendant un run algo (CPU, RAM)
- [ ] `/health` répond 200 après le démarrage complet
