"""Tests unitaires pour webserver/ods_handler.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webserver"))

from ods_handler import (
    generate_ods_modele,
    parse_ods,
    PREPS_HEADERS,
    EXAM_HEADERS,
    CANDIDATS_HEADERS,
    _DEFAULT_PREPS,
)


# ── generate_ods_modele ───────────────────────────────────────────────────────

class TestGenerateOdsModele:
    def test_returns_bytes(self):
        data = generate_ods_modele()
        assert isinstance(data, bytes)
        assert len(data) > 1000

    def test_three_sheets(self):
        sheets = parse_ods(generate_ods_modele())
        assert set(sheets.keys()) == {"preps", "examinateurs", "candidats"}

    def test_preps_prefilled_default(self):
        sheets = parse_ods(generate_ods_modele())
        assert len(sheets["preps"]) == len(_DEFAULT_PREPS)

    def test_preps_headers(self):
        sheets = parse_ods(generate_ods_modele())
        for row in sheets["preps"]:
            for h in PREPS_HEADERS:
                assert h in row

    def test_preps_first_row_values(self):
        sheets = parse_ods(generate_ods_modele())
        first = sheets["preps"][0]
        assert first["Matiere"] == "Lettres"
        assert first["Matière court"] == "Lettres"
        assert first["Temps preparation (min)"] == "30"
        assert first["Duree (min)"] == "20"

    def test_preps_custom_rows(self):
        custom = [
            {"Matiere": "TestA", "Matière court": "TA", "Temps preparation (min)": "15", "Duree (min)": "10"},
            {"Matiere": "TestB", "Matière court": "TB", "Temps preparation (min)": "20", "Duree (min)": "15"},
        ]
        sheets = parse_ods(generate_ods_modele(custom))
        assert len(sheets["preps"]) == 2
        assert sheets["preps"][0]["Matiere"] == "TestA"
        assert sheets["preps"][1]["Matière court"] == "TB"

    def test_examinateurs_and_candidats_empty(self):
        sheets = parse_ods(generate_ods_modele())
        # Les lignes vides sont filtrées par parse_ods
        assert sheets["examinateurs"] == []
        assert sheets["candidats"] == []


# ── parse_ods ─────────────────────────────────────────────────────────────────

class TestParseOds:
    def test_roundtrip_preps_content(self):
        data = generate_ods_modele()
        sheets = parse_ods(data)
        maths = next(r for r in sheets["preps"] if r["Matière court"] == "Maths")
        assert maths["Matiere"] == "Mathématiques"
        assert maths["Temps preparation (min)"] == "20"

    def test_invalid_bytes_raises(self):
        with pytest.raises(ValueError, match="Impossible de lire"):
            parse_ods(b"pas un fichier ods")

    def test_keys_normalized_lowercase(self):
        sheets = parse_ods(generate_ods_modele())
        for key in sheets:
            assert key == key.lower()

    def test_empty_rows_filtered(self):
        data = generate_ods_modele()
        sheets = parse_ods(data)
        for sheet_name, rows in sheets.items():
            for row in rows:
                assert any(v for v in row.values()), \
                    f"Ligne entièrement vide dans la feuille '{sheet_name}'"

    def test_all_16_disciplines_in_preps(self):
        sheets = parse_ods(generate_ods_modele())
        short_names = {r["Matière court"] for r in sheets["preps"]}
        expected = {c for _, c, _, _ in _DEFAULT_PREPS}
        assert short_names == expected
