# Architecture et choix de conception — 2ndOral

Ce document explique le **pourquoi** derrière les décisions techniques et de conception de l'application. Le *quoi* (déploiement, workflow, sécurité) est dans le [README](../Readme.md).

---

## Philosophie générale

L'application est conçue pour un usage **mono-instance, mono-journée** :
un centre d'examen, ~100 candidats, une journée de session.

Conséquences directes sur les choix techniques :
- Pas de multi-tenant, pas de SaaS, pas de scalabilité horizontale.
- Simplicité avant flexibilité : il vaut mieux un système que personne n'a besoin
  de comprendre plutôt qu'un système que personne n'arrive à déployer.
- Le code d'un audit de sécurité doit pouvoir être relu en une demi-journée.

---

## Décisions techniques

### Flask plutôt que Django

Django apporte un ORM, une interface d'administration, des migrations, un système
d'authentification complet. Aucun de ces éléments n'était souhaitable ici :

- L'ORM crée une couche d'abstraction opaque difficile à auditer. Toutes les
  requêtes SQL de l'application sont dans un seul fichier (`db_facility_web.py`),
  visibles en quelques minutes.
- L'interface Django Admin aurait exigé une configuration de sécurité supplémentaire
  pour un bénéfice nul (l'interface d'admin est construite sur mesure).
- Flask impose moins de surface d'attaque par défaut.

### `app.py` monolithique plutôt que blueprints

L'application n'a qu'un seul domaine fonctionnel. Découper en blueprints aurait
dispersé le code sans apporter de bénéfice architectural réel, et aurait rendu
plus difficile la lecture linéaire du flux de sécurité (décorateurs, middleware,
before_request).

### SSE plutôt que WebSockets

Les mises à jour en temps réel (fiche candidat, fiche salle, fiche loge) sont
**unidirectionnelles** : le serveur notifie, le client n'envoie rien sur ce canal.

SSE (Server-Sent Events) est le protocole HTTP natif pour ce besoin :
- Fonctionne sans configuration particulière derrière nginx (contrairement aux
  WebSockets qui nécessitent `proxy_http_version 1.1` + `Upgrade`).
- La reconnexion automatique est gérée par le navigateur.
- Pas de bibliothèque JS côté client — l'API `EventSource` est native.

### gevent plutôt que threads OS

SSE maintient des connexions HTTP ouvertes longtemps (idle entre deux événements).
Avec des threads OS, 4 workers gunicorn ne peuvent servir que ~4 connexions SSE
simultanées. Avec gevent, chaque worker peut gérer des milliers de greenlets
(coroutines légères) — une par connexion SSE active.

Le monkey-patching gevent (`patched_app.py`) rend toutes les I/O non-bloquantes
de manière transparente pour le code Flask.

### Redis pour l'état partagé

4 workers gunicorn = 4 processus sans mémoire partagée. Redis joue trois rôles :

| Rôle | Détail |
|---|---|
| **Pub/sub SSE** | Un worker publie (`sse.publish`), tous les workers abonnés reçoivent et diffusent à leurs clients respectifs |
| **Rate limiting** | Compteurs partagés entre workers (Flask-Limiter) — sans Redis, chaque worker aurait son propre compteur indépendant |
| **Monitoring + sessions actives** | Compteurs horaires de requêtes, sessions actives par rôle, TTL natif Redis (pas de cron de purge à écrire) |

### `algo.py` séparé du serveur web

L'algorithme de placement utilise le module `multiprocessing` de Python pour
paralléliser les essais de placement (plusieurs runs en parallèle, le meilleur
résultat est conservé).

`multiprocessing` est **incompatible avec le monkey-patching gevent** : forker
un processus après que gevent a modifié les primitives de synchronisation produit
des comportements indéfinis. `algo.py` tourne donc comme sous-processus indépendant,
lancé depuis `algo_bg.py` (qui streame sa sortie via SSE).

Conséquence : le serveur web reste disponible pendant le calcul (~ 5–30 s).

### MariaDB plutôt que PostgreSQL ou SQLite

- **MariaDB** : triggers pour le journal d'audit (les triggers sont déclenchés
  quelle que soit l'origine de la modification, y compris une requête directe
  en base — impossible à contourner depuis l'application). Écosystème Docker
  éprouvé, compatibilité MySQL des hébergements mutualisés si besoin.
- **PostgreSQL** aurait convenu techniquement mais aurait alourdi le déploiement
  sans bénéfice à cette échelle.
- **SQLite** aurait posé des problèmes de concurrence avec gevent + plusieurs workers.

### Credentials chiffrés en fichier plutôt qu'en base

Les identifiants (login_key, mots de passe en clair avant hachage) doivent
survivre aux relances successives d'`algo.py` sans être exposés dans les CSV
ou en base de données. Le store `data/credentials.enc` (AES-256-GCM, clé dérivée
via HKDF-SHA256 depuis `APP_SECRET_KEY`) remplit ce rôle :

- Lisible uniquement par `algo.py` et `credential_store.py`.
- Indépendant de la base — une restauration de DB ne remet pas les mots de passe
  à zéro.
- Pas de credentials en clair dans les CSV d'entrée (`candidats.csv`, etc.).

### Pas de framework JS côté client

L'interface est en HTML + CSS + JavaScript vanilla. Les interactions dynamiques
se limitent à :
- La mise à jour en temps réel via SSE (EventSource natif).
- Les minuteurs de loge (timer.js, ~100 lignes).
- L'affichage de la fiche loge : tri dynamique par minuteur déclenché, filtre
  passés/restants, colonne examinateur masquable (loge_view.js).
- Le formulaire d'édition d'oral (fetch pour la liste des examinateurs).

Un framework (React, Vue…) aurait introduit une chaîne de build et des dépendances
npm à auditer pour des gains d'interactivité non justifiés à cette échelle.

---

## Modèle d'accès et canaux SSE

Quatre rôles, fixes et adaptés au domaine :

| Rôle | Session Flask | Canal SSE autorisé |
|---|---|---|
| Admin | `session['user'] = 'admin'` | `general` + tous les canaux ciblés |
| Examinateur | `session['user'] = '<salle>'` | `general`, `salle_<X>`, `loge_<Y>` |
| Loge | `session['loge'] = '<nom>'` | `general`, `loge_<Y>`, `salle_<X>` (ses salles) |
| Candidat | `session['candidat'] = '<token>'` | `candidat_<numero>` uniquement |

Le canal `general` ne diffuse **jamais de données personnelles** (numéros de
candidat, noms) — il sert uniquement de signal de rechargement. Les données
personnelles transitent uniquement sur les canaux ciblés, soumis à autorisation
par `_sse_channel_allowed()`.

Le numéro de candidat n'est **jamais stocké en clair dans le cookie** :
un token aléatoire (TTL 5 min, Redis) fait le lien entre la session et le numéro.

---

## Système de thème

L'apparence du site (web + PDFs) est entièrement dérivée d'**une seule couleur
hexadécimale** (`ACCENT_COLOR`, configurée au setup dans `app_secrets.py`).

```
ACCENT_COLOR ──► derive_palette() ──► 12 tokens
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
   /theme.css (Flask)        reports.py (ReportLab)
   10 variables CSS           11 constantes de couleur
   surchargeant main.css      pour les PDFs générés
```

`derive_palette()` travaille en espace HLS : toutes les couleurs partagent la
même teinte (H) que l'accent, seules la saturation et la luminosité varient
selon le rôle sémantique (fond, texte, bordure, etc.). Les fonds de tableau
(`row_alt`) sont intentionnellement désaturés pour garantir un rendu correct
en impression noir et blanc.

`main.css` contient les valeurs par défaut (palette violet #6c63ff).
`/theme.css` les écrase via les variables CSS — le navigateur applique la
surcharge sans reconstruire la feuille de style principale.

---

## Journal d'audit

Les modifications de données sensibles (émargements, horaires, etc.) sont
enregistrées par des **triggers MariaDB** dans une table `journal_audit`.

Chaque entrée contient le hash SHA-256 de l'entrée précédente (chaîne de hash,
sel côté DB via `@salt`). La falsification d'une entrée casse la chaîne —
détectable depuis `/gestion/verify-logs`.

Les triggers opèrent **au niveau DB**, indépendamment du code applicatif :
une modification directe en base (hors Flask) est également tracée.

Les `password_hash` ne sont jamais journalisés — les hashs scrypt des
examinateurs et loges n'apparaissent pas dans l'archive RGPD.

---

## Interface d'administration

`/gestion` est le tableau de bord d'accueil admin (`gestion_home()`) — atterrissage
par défaut après connexion (`login()`, en l'absence de `link_back`), organisé en
liens statiques groupés par étape (Préparation/Jour J/Fin de session), sans
requête DB. La vue candidats/oraux fusionnée (ex-`/gestion`) vit désormais sur
`/gestion/candidats` (`gestion_candidats()`).

Les pages d'administration (`/gestion/*`) partagent une sidebar d'icônes SVG fixe
(`admin_nav.html`, incluse dans chaque template admin). Les icônes utilisent le
style Lucide (trait, pas de remplissage, `viewBox="0 0 24 24"`) — aucune
dépendance externe, les SVG sont inline.

Les pages d'archivage (`/gestion/archive`), de vérification des logs
(`/gestion/verify-logs`) et `/liste` (grand écran TV) sont intentionnellement
**hors de la sidebar** : ce sont des opérations de fin de session ou un
affichage secondaire, pas des commandes courantes de la sidebar — elles
restent accessibles depuis le tableau de bord (`/gestion`, bloc Jour J pour
`/liste`, retiré de la sidebar le 2026-07-13).

`/salle` et `/loge` (« Index des salles/loges », pages génériques listant
tous les liens) ne sont **pas** dans la partie admin (ni dashboard, ni
sidebar) — retiré le 2026-07-11 car redondant : `/gestion/liste-examinateurs`
lie déjà directement chaque salle, et `/gestion/liste-loges` chaque loge.
Ces deux pages restent utilisées ailleurs (bas de `salle.html`/`loge.html`,
navigation entre salles/loges pour le personnel non-admin).

Les couleurs de la sidebar (`--primary`, `--primary-lt`, `--border`) suivent
automatiquement le thème d'accent — aucune valeur codée en dur.

**Responsive (`main.css`, `@media (max-width: 800px)`)** : la sidebar
(colonne fixe de 48px sur desktop) bascule en barre horizontale défilable
sous le header, pour ne pas amputer la largeur utile sur mobile — l'état
actif passe d'une bordure gauche à une bordure basse, le tooltip s'affiche
en dessous de l'icône plutôt qu'à droite. Les tableaux larges des pages
admin (`liste_examinateurs.html`, `gestion_candidats.html`,
`liste_loges.html`, écrans de prévisualisation disponibilité/changement de
matière/tiers-temps, `jour_j.html`) sont enveloppés dans `.table-scroll`
(déjà utilisée par `salle.html`/`loge.html`) pour défiler horizontalement
sans élargir toute la page.
