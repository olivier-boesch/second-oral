# Stratégie de backup des secrets

## Fichiers critiques

| Fichier | Permissions | Propriétaire | Contenu |
|---------|------------|--------------|---------|
| `webserver/app_secrets.py` | `640` | `root:appuser` | Clés API, clé OTP admin, paramètres DB, pepper des mots de passe |
| `.env` | `600` | `root:root` | Variables Docker (DB_ROOT_PASSWORD, DB_USER, DB_PASSWORD, APP_PORT…) |
| `data/credentials.enc` | `640` | `appuser:appuser` | Store chiffré AES-256-GCM des identifiants candidats/examinateurs/loges |

`app_secrets.py` et `.env` sont non versionnés (`.gitignore`). Leur perte = lockout complet de l'application.

---

## Procédure de backup

### 1. Chiffrer et archiver

```bash
# Depuis le répertoire du projet sur l'hôte
tar czf secrets_$(date +%Y%m%d).tar.gz \
    webserver/app_secrets.py \
    .env \
    data/credentials.enc

# Chiffrer l'archive avec une passphrase forte
gpg --symmetric --cipher-algo AES256 secrets_$(date +%Y%m%d).tar.gz

# Vérifier l'archive chiffrée
gpg --decrypt secrets_$(date +%Y%m%d).tar.gz.gpg | tar tz

# Supprimer l'archive non chiffrée
rm secrets_$(date +%Y%m%d).tar.gz
```

### 2. Stocker en offsite

Copier `secrets_YYYYMMDD.tar.gz.gpg` dans au moins deux emplacements distincts :
- Stockage chiffré personnel (clé USB chiffrée, gestionnaire de mots de passe avec pièce jointe)
- Espace de stockage distant (S3, SFTP privé, etc.)

### 3. Tester la restauration (1×/an minimum, avant chaque session d'examens)

```bash
gpg --decrypt secrets_YYYYMMDD.tar.gz.gpg | tar xz -C /tmp/test_restore/
diff /tmp/test_restore/webserver/app_secrets.py webserver/app_secrets.py
diff /tmp/test_restore/.env .env
rm -rf /tmp/test_restore/
```

---

## Procédure de recovery (serveur perdu)

```bash
# 1. Recréer le répertoire projet et restaurer les secrets
gpg --decrypt secrets_YYYYMMDD.tar.gz.gpg | tar xz

# 2. Appliquer les permissions correctes
sudo chown root:root .env && sudo chmod 600 .env
sudo chown root:$(id -gn) webserver/app_secrets.py && sudo chmod 640 webserver/app_secrets.py

# 3. Relancer l'application
docker compose down && docker compose up -d

# 4. Vérifier
curl https://FQDN/health
# → {"status": "healthy"}
```

---

## Recovery en cas de lockout admin (TOTP cassé ou perdu)

Le TOTP admin est lié à `LOGIN_KEY` dans `webserver/app_secrets.py`. Si la clé est perdue ou si l'app OTP est inaccessible :

```bash
# 1. Générer une nouvelle clé OTP
cd /chemin/vers/le/projet
python3 login_key_generator.py
# → affiche une nouvelle clé base32 + QR code

# 2. Remplacer LOGIN_KEY dans app_secrets.py
sudo nano webserver/app_secrets.py
# Modifier : LOGIN_KEY = "NOUVELLE_CLE_BASE32"

# 3. Scanner le QR code avec l'app OTP (Google Authenticator, Aegis…)

# 4. Redémarrer l'app pour prendre en compte la nouvelle clé
docker compose restart app

# 5. Vérifier la connexion admin sur /login
```

> **Important :** scanner le QR code de la nouvelle clé OTP sur plusieurs appareils et en garder une copie chiffrée dans le backup.

---

## Checklist annuelle (avant la session d'examens)

- [ ] Backup des secrets réalisé et stocké en offsite
- [ ] Restauration testée sur machine de test
- [ ] App OTP admin fonctionnelle sur au moins deux appareils
- [ ] `docker compose up -d && curl https://FQDN/health` → 200 OK
