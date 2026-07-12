# Sécurité — 2ndOral

---

## Niveaux d'accès

Toutes les pages contenant des données personnelles sont protégées.

| Rôle | Session | Fiche candidat | Fiche salle | Fiche loge | Liste générale |
|---|---|---|---|---|---|
| **Admin** (TOTP) | `session['user'] = 'admin'` | ✅ toutes | ✅ toutes | ✅ toutes | ✅ |
| **Examinateur** | `session['user'] = '<salle>'` | ✅ toutes | ✅ sa salle | ✅ sa loge | ✅ |
| **Loge** | `session['loge'] = '<nom>'` | ✅ toutes | ✅ salles de sa loge | ✅ sa loge | ✅ |
| **Candidat** | `session['candidat'] = '<numero>'` | ✅ sa fiche uniquement | ❌ | ❌ | ❌ |
| **Anonyme** | — | ❌ → login | ❌ → login | ❌ → login | ❌ → login |

**Pages de connexion :**

| Rôle | URL | Mécanisme |
|---|---|---|
| Admin | `/login` | Code TOTP 6 chiffres (toutes les 30 s) |
| Examinateur | `/login-examinateur` | Mot de passe par salle |
| Surveillant de loge | `/login-loge` | Mot de passe par loge |
| Candidat | `/login-candidat` | Numéro de candidat + mot de passe du papillon |

---

## Mesures de sécurité

| Mesure | Détail |
|---|---|
| **SQL** | Paramètres liés `%s` / `%(name)s` — aucune interpolation |
| **Mots de passe** | `scrypt(n=2**15, r=8, p=2)` + pepper + sel dérivé par compte (numéro candidat / salle / loge), encodé base64 ; comparaison via `hmac.compare_digest` (protection timing attack) |
| **TOTP admin** | `pyotp`, fenêtre ±1 intervalle, rate limit 10 req/min |
| **CSRF** | `CSRFProtect` (Flask-WTF) enregistré globalement — vérifie le jeton sur toute requête mutante (POST/PUT/PATCH/DELETE) |
| **CSP** | `default-src 'self'`, nonce par requête + `'strict-dynamic'` sur `script-src`, `form-action 'self'`, `base-uri 'self'` via Flask-Talisman |
| **Sessions** | `HttpOnly`, `Secure`, `SameSite=Lax` ; expiration 8 h ; `session.clear()` avant chaque login (protection fixation de session) |
| **N° candidat en session** | Stocké en Redis (TTL 5 min, token aléatoire) — jamais en clair dans le cookie |
| **Rate limiting** | Flask-Limiter + Redis ; 10 req/min sur toutes les routes de connexion (`login`, `login-examinateur`, `login-candidat`, `login-loge`) ; 30 connexions/min sur le flux SSE (`/stream`) |
| **Alerting auth** | Compteur d'échecs par IP (fenêtre glissante de 5 min) ; `WARNING` gunicorn après 5 tentatives — sans journaliser le mot de passe ni le code OTP soumis |
| **HSTS** | `max-age=31536000; includeSubDomains; preload` côté Talisman et nginx |
| **TLS** | TLS 1.2+ uniquement ; suites ECDHE/DHE-GCM/CHACHA20 ; `ssl_session_tickets off` ; redirection HTTP→HTTPS |
| **Headers** | `Referrer-Policy: strict-origin-when-cross-origin`, `Cross-Origin-Opener-Policy: same-origin`, `X-Content-Type-Options: nosniff` |
| **Permissions-Policy** | `camera`, `microphone`, `geolocation`, `payment`, `usb` désactivés |
| **Open redirect** | Toutes les URLs `link_back` validées : schéma limité à `http`/`https`/vide ; même domaine obligatoire |
| **IDOR** | `/generate-doc-one` protégé — `fiche_candidat` réservé à l'admin ou au candidat lui-même (même règle que `show_credentials`, resserré le 2026-07-10 : contenait déjà `login_key` en clair) ; `fiche_salle`/`fiche_loge` : auth obligatoire ; `/download?filename=candidat_*` requiert une session active ; canaux SSE soumis à autorisation par session |
| **Path traversal** | `/download` : regex `^[\w\-. ]+\.pdf$` + `send_from_directory` |
| **PDF générés (accès direct)** | Stockés dans `webserver/generated/`, hors de `webserver/static/` — jamais servis en statique par nginx (ni par le handler `/static/` intégré de Flask) ; seul `/download` (authentifié, cf. IDOR ci-dessus) y donne accès. Volume Docker dédié, non monté dans le conteneur nginx. |
| **Logs d'audit** | Triggers MariaDB, chaîne de hash SHA-256 + sel côté DB. Les `password_hash` ne sont jamais journalisés. |
| **SSE** | Auth requise (`before_request`) + autorisation par canal (`_sse_channel_allowed`) — un candidat/loge/examinateur ne peut s'abonner qu'à ses propres canaux ; `general` ne diffuse aucune donnée personnelle |
| **Actions mutantes** | `delete-examinateur` / `reload-pages` exposées uniquement en POST (+ CSRF) |
| **Tokens en logs** | Tokens de signature tronqués (`_redact_token`) avant journalisation |
| **Noms examinateurs** | Dropdown `/login-examinateur` : numéro de salle uniquement, pas de noms |
| **Server header** | `server_tokens off` sur nginx hôte et Docker |
| **IP client réelle** | nginx hôte remplace `X-Forwarded-For` par `$remote_addr` (anti-spoofing) ; `ProxyFix(x_for=1)` dans Flask |
| **IPs de session (monitoring)** | Stockées en Redis (TTL 8 h, supprimées au logout) — admin uniquement, intérêt légitime art. 6.1.f RGPD |
| **Logs gunicorn** | Format `%({x-forwarded-for}i)s` — affiche l'IP réelle du client |
| **Tokens signature** | Usage unique, expiration 5 min, canal `sign_<token>` dédié |
| **Token QR connexion candidat** | Usage unique, distinct du `login_key` réel (révocable sans reset de compte), expiration configurable (défaut 48h — imprimé à l'avance sur la fiche PDF) ; invalidé automatiquement au renouvellement du mot de passe candidat ; repli vers la connexion classique si expiré/déjà utilisé |
| **DB privilèges** | Compte applicatif limité à `SELECT, INSERT, UPDATE, DELETE` — pas de `DROP`/`ALTER` |
| **Docker** | Conteneur exécuté en tant qu'utilisateur non-root `appuser` (UID 1000) |
| **Dépendances** | Versions épinglées dans `requirements.txt` ; `pip-audit` exécuté à chaque CI |
| **security.txt** | `/.well-known/security.txt` (RFC 9116) — contact de divulgation responsable |

---

## RGPD

Les points RGPD notables sont distribués dans les sections fonctionnelles concernées
([workflow_admin.md](workflow_admin.md), [architecture.md](architecture.md)).

Résumé des traitements :

| Donnée | Base légale | Durée de conservation |
|---|---|---|
| Données candidats (nom, INE, établissement) | Mission de service public (art. 6.1.e) | Fin de session → archivage minimal |
| Téléphone mobile candidat | Intérêt légitime (art. 6.1.f) — joindre un candidat en cas d'imprévu | Fin de session, **exclu de l'archive zip** |
| Adresses IP des sessions actives | Intérêt légitime (art. 6.1.f) — diagnostic | 8 h (TTL Redis) |
| Journal d'audit | Obligation légale | À déterminer avec le DPD |
| Images de signature | Preuve d'émargement | Archive zip, à conserver par le centre |
| Mots de passe / login_key | — | Supprimés dès que le conteneur est purgé |

**Téléphone mobile candidat** (ajouté 2026-07-09) : import optionnel depuis
`candidats.csv`/le fichier ODS (colonne `Téléphone`), modifiable depuis
`/gestion/candidats` → clic sur le nom du candidat → `/gestion/edit-candidat`.
Minimisation appliquée à trois niveaux :
- **Accès restreint à l'administrateur** : `SELECT_INFOS_CANDIDAT` (fiche
  `candidat.html`, consultée par le candidat lui-même) ne sélectionne pas ce
  champ — seules les requêtes des routes `/gestion/*` (admin) le renvoient.
  Absent de `salle.html`/`loge.html` (aucune donnée candidat détaillée n'y
  transite).
- **Absent du journal d'audit** : volontairement exclu des triggers de log
  `Candidat` (comme `login_key`/`password_hash`), donc jamais présent dans
  `journal_audit.json`.
- **Absent de l'archive zip de fin de session** : `planning_oraux.csv` et
  `emargements.csv` listent leurs colonnes explicitement (pas de
  sérialisation automatique), le téléphone n'y figure pas.
