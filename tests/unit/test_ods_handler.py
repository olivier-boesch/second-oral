"""Tests unitaires pour webserver/ods_handler.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webserver"))

import io as _io

from odf.opendocument import load as odf_load
from odf.table import Table, TableCell, TableRow as OdfTableRow
from odf.text import P

from ods_handler import (
    generate_ods_modele,
    parse_ods,
    _merge_exam_etabs,
    PREPS_HEADERS,
    EXAM_HEADERS,
    CANDIDATS_HEADERS,
    LYCEES_HEADERS,
    LYCEES_SHEET_NAME,
    _EXAM_ODS_HEADERS,
    _DEFAULT_PREPS,
    _LYCEES_AIM,
)


# ── generate_ods_modele ───────────────────────────────────────────────────────

class TestGenerateOdsModele:
    def test_returns_bytes(self):
        data = generate_ods_modele()
        assert isinstance(data, bytes)
        assert len(data) > 1000

    def test_four_sheets(self):
        sheets = parse_ods(generate_ods_modele())
        assert set(sheets.keys()) == {"preps", "examinateurs", "candidats", LYCEES_SHEET_NAME}

    def test_sheet_order(self):
        """L'ordre des feuilles doit être : candidats, examinateurs, preps, lycees."""
        from odf.opendocument import load as odf_load
        import io
        doc = odf_load(io.BytesIO(generate_ods_modele()))
        from odf.table import Table
        names = [t.getAttribute("name") for t in doc.spreadsheet.getElementsByType(Table)]
        assert names == ["candidats", "examinateurs", "preps", LYCEES_SHEET_NAME]

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
            {"Matiere": "TestA", "Matière court": "TA",
             "Temps preparation (min)": "15", "Duree (min)": "10"},
            {"Matiere": "TestB", "Matière court": "TB",
             "Temps preparation (min)": "20", "Duree (min)": "15"},
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

    def test_lycees_sheet_has_correct_row_count(self):
        sheets = parse_ods(generate_ods_modele())
        assert len(sheets[LYCEES_SHEET_NAME]) == len(_LYCEES_AIM)

    def test_lycees_headers_present(self):
        sheets = parse_ods(generate_ods_modele())
        for row in sheets[LYCEES_SHEET_NAME]:
            for h in LYCEES_HEADERS:
                assert h in row

    def test_lycees_first_row_has_uai_nom_ville_tel(self):
        sheets = parse_ods(generate_ods_modele())
        first = sheets[LYCEES_SHEET_NAME][0]
        uai, nom, ville, tel = _LYCEES_AIM[0]
        assert first["UAI"] == uai
        assert first["Nom"] == nom
        assert first["Ville"] == ville
        assert first["Téléphone"] == tel

    def test_lycees_etab_column_contains_nom_ville_uai(self):
        """La colonne Etab (formule) doit contenir la concaténation nom — ville (UAI)."""
        sheets = parse_ods(generate_ods_modele())
        row = sheets[LYCEES_SHEET_NAME][0]
        uai, nom, ville, _ = _LYCEES_AIM[0]
        expected = f"{nom} — {ville} ({uai})"
        # La cellule formule stocke la valeur cachée pré-calculée
        assert row["Etab"] == expected


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

    def test_lycees_row_count(self):
        from ods_handler import _LYCEES_AIM
        sheets = parse_ods(generate_ods_modele())
        assert len(sheets[LYCEES_SHEET_NAME]) == len(_LYCEES_AIM)


# ── ODS examinateurs : 3 colonnes Etab ───────────────────────────────────────

class TestExamEtabMerge:
    """Vérifie la fusion Etab1/Etab2/Etab3 → Etab dans parse_ods / _merge_exam_etabs."""

    @staticmethod
    def _make_exam_ods(rows: list[dict]) -> bytes:
        """Construit un ODS minimal avec une feuille examinateurs contenant rows."""
        from odf.opendocument import OpenDocumentSpreadsheet
        doc = OpenDocumentSpreadsheet()
        sheet = Table(name="examinateurs")
        # En-tête
        hr = OdfTableRow()
        for h in _EXAM_ODS_HEADERS:
            c = TableCell(valuetype="string")
            c.addElement(P(text=h))
            hr.addElement(c)
        sheet.addElement(hr)
        # Données
        for r in rows:
            tr = OdfTableRow()
            for h in _EXAM_ODS_HEADERS:
                c = TableCell(valuetype="string")
                c.addElement(P(text=str(r.get(h, ""))))
                tr.addElement(c)
            sheet.addElement(tr)
        doc.spreadsheet.addElement(sheet)
        buf = _io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def test_three_etabs_merged(self):
        """Etab1=A, Etab2=B, Etab3=C → Etab='A,B,C'."""
        data = self._make_exam_ods([{
            "Nom": "Dupont", "Disc.poste": "Maths", "Salle": "101",
            "Heure mini": "8",
            "Etab1": "Paul Cézanne — Aix-en-Provence (0130002G)",
            "Etab2": "Montgrand — Marseille (0130042A)",
            "Etab3": "Thiers — Marseille (0130040Y)",
            "Loge": "A",
        }])
        sheets = parse_ods(data)
        row = sheets["examinateurs"][0]
        assert "Etab" in row
        assert row["Etab"] == (
            "Paul Cézanne — Aix-en-Provence (0130002G),"
            "Montgrand — Marseille (0130042A),"
            "Thiers — Marseille (0130040Y)"
        )
        assert "Etab1" not in row
        assert "Etab2" not in row
        assert "Etab3" not in row

    def test_only_etab1_filled(self):
        """Etab2 et Etab3 vides → Etab contient seulement Etab1."""
        data = self._make_exam_ods([{
            "Nom": "Martin", "Disc.poste": "Maths", "Salle": "102",
            "Heure mini": "8",
            "Etab1": "Marie Curie — Marseille (0130051K)",
            "Etab2": "", "Etab3": "", "Loge": "B",
        }])
        sheets = parse_ods(data)
        assert sheets["examinateurs"][0]["Etab"] == "Marie Curie — Marseille (0130051K)"

    def test_all_etabs_empty(self):
        """Tous les Etab vides → Etab = '' (ligne peut être filtrée si tout vide)."""
        rows = _merge_exam_etabs([{"Nom": "X", "Etab1": "", "Etab2": "", "Etab3": "", "Loge": "A"}])
        assert rows[0]["Etab"] == ""


# ── ODS candidats : colonne Téléphone (ajoutée 2026-07-09) ───────────────────

class TestCandidatsTelephoneColumn:
    """La lecture ODS est générique (1ère ligne = en-têtes) : une colonne
    Téléphone doit donc être lue automatiquement, sans code dédié."""

    @staticmethod
    def _make_cands_ods(rows: list[dict]) -> bytes:
        from odf.opendocument import OpenDocumentSpreadsheet
        doc = OpenDocumentSpreadsheet()
        sheet = Table(name="candidats")
        hr = OdfTableRow()
        for h in CANDIDATS_HEADERS:
            c = TableCell(valuetype="string")
            c.addElement(P(text=h))
            hr.addElement(c)
        sheet.addElement(hr)
        for r in rows:
            tr = OdfTableRow()
            for h in CANDIDATS_HEADERS:
                c = TableCell(valuetype="string")
                c.addElement(P(text=str(r.get(h, ""))))
                tr.addElement(c)
            sheet.addElement(tr)
        doc.spreadsheet.addElement(sheet)
        buf = _io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def test_telephone_read_from_ods(self):
        data = self._make_cands_ods([{
            "CANDIDAT": "Dupont Marie (1234567890A)",
            "CHOIX DISCIPLINE 1": "Maths", "CHOIX DISCIPLINE 2": "PC",
            "TT": "0", "Etab": "St Ex", "Profs": "", "Téléphone": "0612345678",
        }])
        sheets = parse_ods(data)
        assert sheets["candidats"][0]["Téléphone"] == "0612345678"

    def test_telephone_absent_when_empty(self):
        """Colonne présente mais vide : la ligne n'est pas filtrée (autres
        champs non vides), 'Téléphone' vaut une chaîne vide."""
        data = self._make_cands_ods([{
            "CANDIDAT": "Martin Jean (0987654321B)",
            "CHOIX DISCIPLINE 1": "SES", "CHOIX DISCIPLINE 2": "Anglais",
            "TT": "1", "Etab": "Lumière", "Profs": "", "Téléphone": "",
        }])
        sheets = parse_ods(data)
        assert sheets["candidats"][0]["Téléphone"] == ""

    def test_etab_key_order_preserved(self):
        """Etab doit apparaître à la position de Etab1 dans l'ordre des clés."""
        rows = _merge_exam_etabs([{
            "Nom": "X", "Disc.poste": "M", "Salle": "1",
            "Heure mini": "8",
            "Etab1": "Paul Cézanne — Aix-en-Provence (0130002G)",
            "Etab2": "", "Etab3": "", "Loge": "A",
        }])
        keys = list(rows[0].keys())
        assert keys.index("Etab") == 4  # position 4, avant Loge

    def test_no_etab_columns_unchanged(self):
        """Si Etab1 absent, la fusion est ignorée (rétrocompatibilité)."""
        rows = [{"Nom": "X", "Etab": "ancien format", "Loge": "A"}]
        result = _merge_exam_etabs(rows)
        assert result == rows

    def test_ods_modele_examinateurs_sheet_has_3_etab_headers(self):
        """Le modèle ODS généré a bien Etab1/Etab2/Etab3 dans la feuille examinateurs."""
        doc = odf_load(_io.BytesIO(generate_ods_modele()))
        for table in doc.spreadsheet.getElementsByType(Table):
            if table.getAttribute("name") == "examinateurs":
                rows = table.getElementsByType(OdfTableRow)
                header_row = rows[0]
                cells = header_row.getElementsByType(TableCell)
                headers = [
                    "".join(n.data for p in c.getElementsByType(P)
                             for n in p.childNodes if hasattr(n, "data"))
                    for c in cells
                ]
                assert "Etab1" in headers
                assert "Etab2" in headers
                assert "Etab3" in headers
                assert "Etab" not in headers
                break

    def test_ods_modele_candidats_sheet_has_single_etab(self):
        """La feuille candidats du modèle garde une seule colonne Etab."""
        doc = odf_load(_io.BytesIO(generate_ods_modele()))
        for table in doc.spreadsheet.getElementsByType(Table):
            if table.getAttribute("name") == "candidats":
                rows = table.getElementsByType(OdfTableRow)
                header_row = rows[0]
                cells = header_row.getElementsByType(TableCell)
                headers = [
                    "".join(n.data for p in c.getElementsByType(P)
                             for n in p.childNodes if hasattr(n, "data"))
                    for c in cells
                ]
                assert "Etab" in headers
                assert "Etab1" not in headers
                break

    def test_ods_modele_candidats_sheet_has_telephone_column(self):
        """Numéro de mobile candidat (ajouté 2026-07-09) : colonne optionnelle
        en fin de feuille (pas de validation ODS stricte, texte libre)."""
        assert "Téléphone" in CANDIDATS_HEADERS
        doc = odf_load(_io.BytesIO(generate_ods_modele()))
        for table in doc.spreadsheet.getElementsByType(Table):
            if table.getAttribute("name") == "candidats":
                rows = table.getElementsByType(OdfTableRow)
                header_row = rows[0]
                cells = header_row.getElementsByType(TableCell)
                headers = [
                    "".join(n.data for p in c.getElementsByType(P)
                             for n in p.childNodes if hasattr(n, "data"))
                    for c in cells
                ]
                assert "Téléphone" in headers
                break
