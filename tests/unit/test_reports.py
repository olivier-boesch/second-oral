"""Tests unitaires pour reports.py — génération des PDFs.

Se concentre sur les correctifs de l'audit sécurité : `_safe_filename_part`
(anti path-traversal sur des valeurs issues des CSV importés) et
`liste_fiches_candidats` (les fiches individuelles, qui contiennent le
login_key en clair et un QR d'auto-connexion, ne doivent plus persister dans
`file_dir` — seule la concaténation `liste_candidats.pdf` en sort).
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Chemins ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webserver"))

# ── Mock app_secrets (reports.py l'importe au chargement du module) ─────────
if "app_secrets" not in sys.modules:
    _as = types.ModuleType("app_secrets")
    _as.ACCENT_COLOR = "#6c63ff"
    _as.TIMEZONE = __import__("pytz").timezone("Europe/Paris")
    sys.modules["app_secrets"] = _as

import reports  # noqa: E402


# ── _safe_filename_part ───────────────────────────────────────────────────────

class TestSafeFilenamePart:
    """Ces valeurs (nom de candidat/examinateur, salle) viennent des CSV
    importés et sont concaténées dans un chemin de fichier : sans filtrage,
    un nom contenant `/` ou `..` ferait écrire le PDF hors du répertoire prévu."""

    def test_espaces_remplaces(self):
        assert reports._safe_filename_part("Jean Paul") == "Jean_Paul"

    def test_slash_neutralise(self):
        assert "/" not in reports._safe_filename_part("../../etc/passwd")

    def test_traversal_ne_produit_pas_de_chemin_parent(self):
        safe = reports._safe_filename_part("../../etc/passwd")
        assert not safe.startswith("..")
        assert "/" not in safe

    def test_caracteres_speciaux_neutralises(self):
        safe = reports._safe_filename_part("Dupont;rm -rf /")
        assert ";" not in safe and " " not in safe

    def test_nom_normal_inchange_modulo_espaces(self):
        assert reports._safe_filename_part("Martin-Dupont") == "Martin-Dupont"

    def test_valeur_vide_ne_produit_pas_de_nom_vide(self):
        assert reports._safe_filename_part("...") == "sans_nom"
        assert reports._safe_filename_part("") == "sans_nom"


# ── liste_fiches_candidats ────────────────────────────────────────────────────

class TestListeFichesCandidatsNePersistePasLesFichesIndividuelles:
    """Vuln audit : les fiches individuelles (login_key en clair + QR
    d'auto-connexion) étaient écrites dans `file_dir` (= generated/) sous un
    nom dérivé du nom du candidat, donc devinable — `/download` les rendait
    accessibles à toute session authentifiée (IDOR). Elles ne doivent plus
    exister que le temps de la concaténation, dans le répertoire temporaire."""

    def _candidats(self, n=3):
        return [
            {"id": i, "nom": f"Candidat {i}", "numero": f"{i:011d}",
             "token": "tok", "login_key": "secret"}
            for i in range(n)
        ]

    def test_seul_liste_candidats_pdf_reste_dans_file_dir(self, tmp_path, monkeypatch):
        fiches_ecrites_hors_tmp = []

        def _fake_fiche_candidat(infos, tempdirname, file_dir='.',
                                  filename_root='', centre_examen=''):
            # Le contrat testé : file_dir doit être le répertoire temporaire de
            # l'appelant, jamais le file_dir passé à liste_fiches_candidats.
            if Path(file_dir) == tmp_path:
                fiches_ecrites_hors_tmp.append(file_dir)
            filename = f"{filename_root}{infos['id']}.pdf"
            (Path(file_dir) / filename).write_bytes(b"%PDF-1.4 fake")
            return filename

        monkeypatch.setattr(reports, "fiche_candidat", _fake_fiche_candidat)
        monkeypatch.setattr(reports, "_concat_pdfs",
                            lambda files, out: Path(out).write_bytes(b"%PDF-1.4 concat"))

        reports.liste_fiches_candidats(self._candidats(), file_dir=str(tmp_path))

        assert not fiches_ecrites_hors_tmp, (
            "fiche_candidat a été appelée avec file_dir=tmp_path (persistant) "
            "au lieu du répertoire temporaire"
        )
        restants = sorted(p.name for p in tmp_path.iterdir())
        assert restants == ["liste_candidats.pdf"], (
            f"file_dir ne doit contenir que la concaténation, trouvé : {restants}"
        )

    def test_deux_candidats_homonymes_ne_secrasent_pas(self, tmp_path, monkeypatch):
        """Avant l'indexation par position, deux candidats de même nom
        produisaient le même nom de fichier et s'écrasaient l'un l'autre."""
        noms_vus = []

        def _fake_fiche_candidat(infos, tempdirname, file_dir='.',
                                  filename_root='', centre_examen=''):
            filename = f"{filename_root}{reports._safe_filename_part(infos['nom'])}.pdf"
            path = Path(file_dir) / filename
            assert not path.exists(), f"{filename} déjà écrit — collision de nom"
            path.write_bytes(b"%PDF-1.4 fake")
            noms_vus.append(filename)
            return filename

        monkeypatch.setattr(reports, "fiche_candidat", _fake_fiche_candidat)
        monkeypatch.setattr(reports, "_concat_pdfs",
                            lambda files, out: Path(out).write_bytes(b"%PDF-1.4 concat"))

        homonymes = [
            {"id": 1, "nom": "Martin Paul", "numero": "1", "token": "t", "login_key": "k"},
            {"id": 2, "nom": "Martin Paul", "numero": "2", "token": "t", "login_key": "k"},
        ]
        reports.liste_fiches_candidats(homonymes, file_dir=str(tmp_path))

        assert len(noms_vus) == len(set(noms_vus)) == 2
