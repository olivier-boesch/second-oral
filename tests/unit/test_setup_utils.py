"""Tests unitaires pour les utilitaires de setup_new_site.py."""

import base64
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from setup_new_site import (
    random_base32,
    is_domain_label,
    build_totp_uri,
    generate_nginx_conf,
    generate_env_file,
    generate_app_secrets,
)


# ── random_base32 ─────────────────────────────────────────────────────────────

class TestRandomBase32:
    def test_is_string(self):
        assert isinstance(random_base32(), str)

    def test_valid_base32(self):
        key = random_base32()
        # Ne doit pas lever d'exception
        base64.b32decode(key)

    def test_default_length(self):
        # 80 bytes → 128 caractères base32 (80 * 8 / 5 = 128)
        assert len(random_base32(80)) == 128

    def test_unique(self):
        assert random_base32() != random_base32()


# ── is_domain_label ───────────────────────────────────────────────────────────

class TestIsDomainLabel:
    @pytest.mark.parametrize("label", ["stex", "lycee-saintexa", "abc123", "a", "x1"])
    def test_valid(self, label):
        assert is_domain_label(label)

    @pytest.mark.parametrize("label", [
        "", "-stex", "stex-", "st ex", "stex.", "abc_def",
        # Note : la regex a re.I → les majuscules sont acceptées (DNS insensible à la casse)
    ])
    def test_invalid(self, label):
        assert not is_domain_label(label)


# ── build_totp_uri ────────────────────────────────────────────────────────────

class TestBuildTotpUri:
    def test_scheme(self):
        uri = build_totp_uri("ABCDEFGH")
        assert uri.startswith("otpauth://totp/")

    def test_contains_secret(self):
        key = "ABCDEFGHIJKLMNOP"
        uri = build_totp_uri(key)
        assert f"secret={key}" in uri

    def test_contains_issuer(self):
        uri = build_totp_uri("ABCDEFGH", issuer="MonApp")
        assert "MonApp" in uri

    def test_algorithm_sha1(self):
        assert "algorithm=SHA1" in build_totp_uri("ABCDEFGH")

    def test_period_30(self):
        assert "period=30" in build_totp_uri("ABCDEFGH")

    def test_digits_6(self):
        assert "digits=6" in build_totp_uri("ABCDEFGH")


# ── generate_nginx_conf ───────────────────────────────────────────────────────

class TestGenerateNginxConf:
    def test_contains_fqdn(self):
        conf = generate_nginx_conf("stex.mesoraux.fr")
        assert "stex.mesoraux.fr" in conf

    def test_proxy_pass(self):
        conf = generate_nginx_conf("test.example.com")
        assert "proxy_pass" in conf

    def test_listen_80(self):
        conf = generate_nginx_conf("test.example.com")
        assert "listen 80" in conf

    def test_static_location(self):
        conf = generate_nginx_conf("test.example.com")
        assert "location /static/" in conf


# ── generate_env_file ─────────────────────────────────────────────────────────

class TestGenerateEnvFile:
    def test_returns_tuple(self):
        result = generate_env_file("user", "pass", "db")
        assert isinstance(result, tuple) and len(result) == 2

    def test_contains_required_keys(self):
        content, _ = generate_env_file("myuser", "mypass", "mydb")
        assert "DB_ROOT_PASSWORD=" in content
        assert "DB_NAME=mydb"      in content
        assert "DB_USER=myuser"    in content
        assert "DB_PASSWORD=mypass" in content

    def test_root_password_random(self):
        _, pwd1 = generate_env_file("u", "p", "d")
        _, pwd2 = generate_env_file("u", "p", "d")
        assert pwd1 != pwd2

    def test_no_versioning_comment(self):
        content, _ = generate_env_file("u", "p", "d")
        assert "versionner" in content  # rappel de sécurité présent


# ── generate_app_secrets ──────────────────────────────────────────────────────

class TestGenerateAppSecrets:
    def _make(self, **kw):
        defaults = dict(fqdn="stex.mesoraux.fr", centre="Lycée Test",
                        db_user="user", db_password="pass", db_name="db")
        defaults.update(kw)
        content, otp_key = generate_app_secrets(**defaults)
        return content, otp_key

    def test_returns_tuple(self):
        content, otp_key = self._make()
        assert isinstance(content, str)
        assert isinstance(otp_key, str)

    def test_otp_key_valid_base32(self):
        _, otp_key = self._make()
        base64.b32decode(otp_key)  # ne doit pas lever d'exception

    def test_fqdn_in_content(self):
        content, _ = self._make(fqdn="custom.example.org")
        assert "custom.example.org" in content

    def test_centre_in_content(self):
        content, _ = self._make(centre="Lycée Spécial")
        assert "Lycée Spécial" in content

    def test_required_constants_present(self):
        content, _ = self._make()
        for const in ("CENTRE_EXAMEN", "FQDN", "LOGIN_KEY", "APP_SECRET_KEY",
                      "DB_PARAMS", "DB_SALT", "PASSWORD_PEPPER", "PASSWORD_SALT",
                      "DIGITAL_SIGN"):
            assert const in content, f"Constante manquante : {const}"

    def test_legal_constants_present(self):
        content, _ = self._make(
            director_name="Jean Dupont",
            centre_address="1 rue de la Paix, 75001 Paris",
            academie="Académie de Paris",
            hebergeur="OVHcloud",
            dpd_email="dpd@ac-paris.fr",
        )
        for const in ("DIRECTOR_NAME", "CENTRE_ADDRESS", "ACADEMIE", "HEBERGEUR", "DPD_EMAIL"):
            assert const in content, f"Constante légale manquante : {const}"
        assert "Jean Dupont"    in content
        assert "dpd@ac-paris.fr" in content

    def test_digital_sign_false(self):
        content, _ = self._make(digital_sign=False)
        assert "DIGITAL_SIGN = False" in content

    def test_unique_secrets(self):
        c1, k1 = self._make()
        c2, k2 = self._make()
        assert k1 != k2, "Les clés OTP doivent être différentes à chaque appel"
