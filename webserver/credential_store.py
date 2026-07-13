"""
Chiffrement/déchiffrement du store de credentials (AES-256-GCM).

Ce module est partagé entre app.py (lecture/écriture via les routes de renouvellement)
et algo.py (lecture des credentials existants pour les runs suivants).
"""
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _aesgcm(secret_key: str) -> AESGCM:
    """Retourne une instance AESGCM avec une clé AES-256 dérivée via HKDF-SHA256.

    HKDF offre une séparation de domaine explicite grâce au salt et à l'info,
    ce qui évite la réutilisation de la clé dans d'autres contextes.

    :param secret_key: APP_SECRET_KEY de l'instance.
    :returns: Instance AESGCM prête à chiffrer/déchiffrer.
    """
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'second_oral_credentials_v1',
        info=b'aesgcm-credentials-key',
    ).derive(secret_key.encode())
    return AESGCM(key)


def load_credentials(enc_file: Path, secret_key: str) -> dict:
    """Charge et déchiffre le store de credentials depuis un fichier AES-256-GCM.

    Format du fichier : nonce (12 bytes) || ciphertext+tag GCM.
    Retourne un dict vide {"examinateurs": {}, "loges": {}} si le fichier
    n'existe pas ou si le déchiffrement échoue.

    :param enc_file:   Chemin vers le fichier chiffré (credentials.enc).
    :param secret_key: APP_SECRET_KEY de l'instance.
    :returns: Dict {"examinateurs": {identifiant: plaintext}, "loges": {nom: plaintext}}.
    """
    empty: dict = {"examinateurs": {}, "loges": {}}
    if not enc_file.exists():
        return empty
    try:
        raw = enc_file.read_bytes()
        nonce, ciphertext = raw[:12], raw[12:]
        plaintext = _aesgcm(secret_key).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode())
    except Exception:
        return empty


def save_credentials(enc_file: Path, secret_key: str, creds: dict) -> None:
    """Chiffre et persiste le store de credentials dans un fichier AES-256-GCM.

    Utilise un nonce aléatoire 96 bits à chaque appel (jamais réutilisé).
    Le tag d'authentification GCM (128 bits) est inclus dans le ciphertext.
    Le fichier est créé avec les permissions 0o600 (propriétaire uniquement).

    :param enc_file:   Chemin de destination (credentials.enc).
    :param secret_key: APP_SECRET_KEY de l'instance.
    :param creds:      Dict {"examinateurs": {identifiant: plaintext}, "loges": {nom: plaintext}}.
    """
    enc_file.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    ciphertext = _aesgcm(secret_key).encrypt(nonce, json.dumps(creds).encode(), None)
    enc_file.write_bytes(nonce + ciphertext)
    enc_file.chmod(0o600)
