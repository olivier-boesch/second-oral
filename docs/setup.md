# Configuration d'un nouveau site — 2ndOral

`setup_new_site.py` configure une instance complète en une commande.
Il doit être lancé avec `sudo` car il crée des fichiers appartenant à root.

---

## Lancement

```bash
# Mode interactif (questions posées une par une)
sudo python setup_new_site.py

# Mode batch (pour l'automatisation)
sudo python setup_new_site.py \
    --subdomain stex \
    --domain mesoraux.fr \
    --name "Lycée Saint Exupéry - Marseille" \
    --db-user secondoral \
    --db-password MOT_DE_PASSE \
    --certbot-email admin@mesoraux.fr \
    --director-name "Jean Dupont" \
    --centre-address "13 avenue du Lycée, 13001 Marseille" \
    --academie "Académie d'Aix-Marseille" \
    --hebergeur "OVHcloud SAS, 2 rue Kellermann, 59100 Roubaix" \
    --dpd-email "dpd@ac-aix-marseille.fr" \
    --sentry-dsn "https://xxx@oyyy.ingest.sentry.io/zzz" \  # optionnel
    --no-digital-sign   # optionnel : désactive l'émargement en ligne
```

---

## Ce que fait le script (dans l'ordre)

1. **`webserver/app_secrets.py`** — génère tous les secrets (clé OTP, `APP_SECRET_KEY`,
   `DB_SALT`, pepper/sel, `ACCENT_COLOR`) + infos légales (directeur de publication,
   adresse, académie, DPD)

2. **Couleur d'accent** — choix interactif parmi 6 palettes prédéfinies (violet, bleu,
   vert, rouge, orange, turquoise) ou couleur personnalisée `#rrggbb` ; stockée dans
   `app_secrets.py` et appliquée au site et aux PDFs

3. **QR code TOTP** — affiché dans le terminal + sauvegardé en PNG (`otp_setup.png`)
   + vérification interactive du code

4. **PDF administrateur** — clé TOTP + démarches légales RGPD à effectuer par le
   chef de centre, aux couleurs de l'accent choisi

5. **`.env` Docker** — généré automatiquement avec `DB_ROOT_PASSWORD` aléatoire,
   credentials cohérents avec `app_secrets.py`, et `SENTRY_DSN` si fourni
   (ou ligne commentée sinon)

6. **Config nginx** — écrite dans `nginx-conf/<fqdn>` et installée dans
   `/etc/nginx/sites-available/` (TLS 1.2+, HTTP→HTTPS, HSTS)

7. **Certbot** — `certbot --nginx -d <fqdn>` (Let's Encrypt)

8. **Docker** *(optionnel, demande confirmation)* — `docker compose build`, démarrage
   MariaDB + Redis, attente de disponibilité, création de la base et de l'utilisateur
   avec privilèges limités, démarrage de la stack complète

---

## Fichiers sensibles

| Fichier | Permissions | Contenu |
|---|---|---|
| `webserver/app_secrets.py` | `640`, root | Secrets applicatifs + ACCENT_COLOR + infos légales |
| `.env` | `600` | Variables Docker (DB credentials, SENTRY_DSN…) |
| `otp_setup.png` | root | QR code TOTP — **à supprimer après scan** |

> Ces fichiers ne doivent **jamais être versionnés**. Ils sont listés dans `.gitignore`.

---

## Prérequis système

- Docker + Docker Compose v2
- nginx installé sur l'hôte (SSL via Certbot)
- `python3-certbot-nginx`
- DNS du domaine pointant sur le serveur

---

## Commandes Docker courantes

```bash
docker compose up -d          # Démarrer
docker compose stop           # Arrêter
docker compose logs -f app    # Logs de l'application
docker compose restart app    # Appliquer un changement de code (sans rebuild)
docker compose restart nginx  # Appliquer un changement de config nginx Docker
docker compose build app      # Rebuild si requirements.txt a changé
sudo nginx -s reload          # Appliquer un changement de config nginx hôte
```

---

## Checklist déploiement initial

- [ ] `sudo python setup_new_site.py` exécuté jusqu'à la fin (avec infos légales)
- [ ] Clé OTP configurée dans l'application TOTP (scannée pendant le script)
- [ ] PDF administrateur imprimé et fichier numérique supprimé
- [ ] `otp_setup.png` supprimé
- [ ] nginx hôte rechargé (`sudo nginx -s reload`)
- [ ] DPD de l'académie informé du traitement (voir section RGPD du PDF admin)
- [ ] Fichiers CSV déposés dans `data/` et `./run_algo.sh` lancé
- [ ] PDFs papillons imprimés et distribués avant le jour des épreuves
