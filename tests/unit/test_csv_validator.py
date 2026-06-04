"""Tests unitaires pour webserver/csv_validator.py."""

import io
import sys
from pathlib import Path

import pytest

# Accès au module depuis la racine du projet
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webserver"))

from csv_validator import (
    _decode,
    _detect_delimiter,
    normalize_csv,
    validate_preps,
    validate_profs,
    validate_candidats,
    validate_all,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def csv_bytes(header: str, *rows, sep=";", encoding="utf-8") -> bytes:
    lines = [header] + list(rows)
    return ("\n".join(lines) + "\n").encode(encoding)


def errors_of(issues):
    return [i for i in issues if i["level"] == "error"]

def warnings_of(issues):
    return [i for i in issues if i["level"] == "warning"]


# ── _decode ───────────────────────────────────────────────────────────────────

class TestDecode:
    def test_utf8(self):
        assert _decode("bonjour".encode("utf-8")) == "bonjour"

    def test_utf8_bom(self):
        raw = "bonjour".encode("utf-8-sig")   # le codec ajoute le BOM
        result = _decode(raw)
        assert result.startswith("b"), "Le BOM doit être supprimé"

    def test_latin1_fallback(self):
        raw = "caf\xe9".encode("latin-1")          # café en latin-1
        result = _decode(raw)
        assert "caf" in result


# ── _detect_delimiter ─────────────────────────────────────────────────────────

class TestDetectDelimiter:
    def test_semicolons(self):
        assert _detect_delimiter("a;b;c\n1;2;3") == ";"

    def test_commas(self):
        assert _detect_delimiter("a,b,c\n1,2,3") == ","

    def test_more_semicolons(self):
        assert _detect_delimiter("a;b,c;d") == ";"


# ── normalize_csv ─────────────────────────────────────────────────────────────

class TestNormalizeCsv:
    def test_basic_semicolon(self):
        raw = csv_bytes("A;B", "1;2", "3;4")
        rows, delim = normalize_csv(raw)
        assert delim == ";"
        assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]

    def test_basic_comma(self):
        raw = csv_bytes("A,B", "1,2", sep=",")
        rows, delim = normalize_csv(raw)
        assert delim == ","
        assert rows[0] == {"A": "1", "B": "2"}

    def test_strips_whitespace(self):
        raw = b" A ; B \n 1 ; 2 \n"
        rows, _ = normalize_csv(raw)
        assert "A" in rows[0]
        assert rows[0]["A"] == "1"

    def test_bom_utf8(self):
        raw = "A;B\n1;2\n".encode("utf-8-sig")   # le codec ajoute le BOM
        rows, _ = normalize_csv(raw)
        assert list(rows[0].keys())[0] == "A", "Le BOM ne doit pas polluer la première clé"

    def test_latin1_encoding(self):
        raw = ("Matiere;Durée\ncafé;10\n").encode("latin-1")
        rows, _ = normalize_csv(raw)
        assert rows[0].get("Matiere") == "caf\xe9"  # accepté

    def test_file_object(self):
        f = io.BytesIO(b"X;Y\na;b\n")
        rows, _ = normalize_csv(f)
        assert rows[0] == {"X": "a", "Y": "b"}


# ── validate_preps ────────────────────────────────────────────────────────────

PREPS_HEADER = "Matiere;Matière court;Temps preparation (min);Duree (min)"

def make_preps_row(mat="Maths", court="M", prep="20", duree="15"):
    return f"{mat};{court};{prep};{duree}"

def parse_preps(*rows):
    raw = csv_bytes(PREPS_HEADER, *rows)
    from csv_validator import normalize_csv
    r, _ = normalize_csv(raw)
    return r

class TestValidatePreps:
    def test_valid(self):
        rows = parse_preps(make_preps_row(), make_preps_row("SES","S","25","20"))
        assert validate_preps(rows) == []

    def test_empty(self):
        assert errors_of(validate_preps([])) != []

    def test_missing_column(self):
        raw = csv_bytes("Matiere;Matière court", "Maths;M")
        rows, _ = normalize_csv(raw)
        errs = errors_of(validate_preps(rows))
        assert any("Colonnes manquantes" in e["message"] for e in errs)

    def test_duplicate_matiere(self):
        rows = parse_preps(make_preps_row("Maths"), make_preps_row("Maths"))
        errs = errors_of(validate_preps(rows))
        assert any("double" in e["message"] for e in errs)

    def test_zero_duration(self):
        rows = parse_preps(make_preps_row(duree="0"))
        errs = errors_of(validate_preps(rows))
        assert any("Duree" in e["message"] for e in errs)

    def test_non_integer_prep(self):
        rows = parse_preps(make_preps_row(prep="abc"))
        errs = errors_of(validate_preps(rows))
        assert any("Temps preparation" in e["message"] for e in errs)

    def test_empty_name(self):
        rows = parse_preps(make_preps_row(mat=""))
        errs = errors_of(validate_preps(rows))
        assert any("vide" in e["message"] for e in errs)

    def test_empty_nom_court_is_warning(self):
        rows = parse_preps(make_preps_row(court=""))
        warns = warnings_of(validate_preps(rows))
        assert any("vide" in w["message"] for w in warns)


# ── validate_profs ────────────────────────────────────────────────────────────

PROFS_HEADER = "Nom;Disc.poste;Salle;Heure mini;Etab;Loge"
MATIERES = {"Maths", "SES"}
NOM_COURTS = {"M", "S"}

def make_profs_row(nom="Dupont", disc="Maths", salle="1", heure="8", etab="Lycée", loge="A"):
    return f"{nom};{disc};{salle};{heure};{etab};{loge}"

def parse_profs(*rows):
    raw = csv_bytes(PROFS_HEADER, *rows)
    from csv_validator import normalize_csv
    r, _ = normalize_csv(raw)
    return r

class TestValidateProfs:
    def test_valid(self):
        rows = parse_profs(make_profs_row())
        assert validate_profs(rows, MATIERES, NOM_COURTS) == []

    def test_empty(self):
        assert errors_of(validate_profs([], MATIERES, NOM_COURTS)) != []

    def test_missing_column(self):
        raw = csv_bytes("Nom;Disc.poste", "Dupont;Maths")
        rows, _ = normalize_csv(raw)
        errs = errors_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("Colonnes manquantes" in e["message"] for e in errs)

    def test_unknown_discipline(self):
        rows = parse_profs(make_profs_row(disc="Inconnue"))
        errs = errors_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("introuvable" in e["message"] for e in errs)

    def test_discipline_case_insensitive(self):
        rows = parse_profs(make_profs_row(disc="maths"))  # minuscule
        assert validate_profs(rows, MATIERES, NOM_COURTS) == []

    def test_discipline_by_nom_court(self):
        rows = parse_profs(make_profs_row(disc="M"))      # nom court
        assert validate_profs(rows, MATIERES, NOM_COURTS) == []

    def test_bad_hour_alpha(self):
        rows = parse_profs(make_profs_row(heure="abc"))
        errs = errors_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("Heure mini" in e["message"] for e in errs)

    def test_bad_hour_out_of_range(self):
        rows = parse_profs(make_profs_row(heure="25"))
        errs = errors_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("Heure mini" in e["message"] for e in errs)

    def test_duplicate_salle_warning(self):
        rows = parse_profs(make_profs_row(salle="1"), make_profs_row(nom="Martin", salle="1"))
        warns = warnings_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("plusieurs fois" in w["message"] for w in warns)

    def test_empty_loge_warning(self):
        rows = parse_profs(make_profs_row(loge=""))
        warns = warnings_of(validate_profs(rows, MATIERES, NOM_COURTS))
        assert any("Loge" in w["message"] for w in warns)


# ── validate_candidats ────────────────────────────────────────────────────────

CANDS_HEADER = "CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs"

def make_cand_row(cand="Durand Paul (1234567890A)", d1="Maths", d2="SES",
                   tt="0", etab="Lycée", profs=""):
    return f"{cand};{d1};{d2};{tt};{etab};{profs}"

def parse_cands(*rows):
    raw = csv_bytes(CANDS_HEADER, *rows)
    from csv_validator import normalize_csv
    r, _ = normalize_csv(raw)
    return r

class TestValidateCandidats:
    def test_valid(self):
        rows = parse_cands(make_cand_row())
        assert validate_candidats(rows, MATIERES, NOM_COURTS) == []

    def test_empty(self):
        assert errors_of(validate_candidats([], MATIERES, NOM_COURTS)) != []

    def test_missing_column(self):
        raw = csv_bytes("CANDIDAT;TT", "Dupont (123);0")
        rows, _ = normalize_csv(raw)
        errs = errors_of(validate_candidats(rows, MATIERES, NOM_COURTS))
        assert any("Colonnes manquantes" in e["message"] for e in errs)

    def test_bad_ine_format(self):
        rows = parse_cands(make_cand_row(cand="Dupont Paul"))  # sans (INE)
        errs = errors_of(validate_candidats(rows, MATIERES, NOM_COURTS))
        assert any("Format invalide" in e["message"] for e in errs)

    def test_duplicate_ine(self):
        rows = parse_cands(make_cand_row(cand="Martin A (ABC)"),
                           make_cand_row(cand="Martin B (ABC)"))
        errs = errors_of(validate_candidats(rows, MATIERES, NOM_COURTS))
        assert any("double" in e["message"] for e in errs)

    def test_bad_tt(self):
        rows = parse_cands(make_cand_row(tt="2"))
        errs = errors_of(validate_candidats(rows, MATIERES, NOM_COURTS))
        assert any("TT" in e["message"] for e in errs)

    def test_tt_values_accepted(self):
        for tt in ("0", "1"):
            rows = parse_cands(make_cand_row(tt=tt))
            errs = [e for e in validate_candidats(rows, MATIERES, NOM_COURTS)
                    if "TT" in e["message"]]
            assert errs == [], f"TT='{tt}' devrait être accepté"

    def test_unknown_discipline(self):
        rows = parse_cands(make_cand_row(d1="Inconnue"))
        errs = errors_of(validate_candidats(rows, MATIERES, NOM_COURTS))
        assert any("introuvable" in e["message"] for e in errs)

    def test_discipline_case_insensitive(self):
        rows = parse_cands(make_cand_row(d1="maths", d2="ses"))
        assert validate_candidats(rows, MATIERES, NOM_COURTS) == []


# ── validate_all ──────────────────────────────────────────────────────────────

class TestValidateAll:
    def test_all_none(self):
        report = validate_all(None, None, None)
        assert not report["ok"]
        assert len(report["errors"]) == 3  # un par fichier absent

    def test_clean_files(self, tmp_path):
        preps = tmp_path / "preps.csv"
        profs = tmp_path / "profs.csv"
        cands = tmp_path / "cands.csv"

        preps.write_text("Matiere;Matière court;Temps preparation (min);Duree (min)\n"
                         "Maths;M;20;15\nSES;S;25;20\n")
        profs.write_text("Nom;Disc.poste;Salle;Heure mini;Etab;Loge\n"
                         "Dupont;Maths;1;8;Lycée;A\n")
        cands.write_text("CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs\n"
                         "Martin Paul (111111111AA);Maths;SES;0;Lycée;\n")

        report = validate_all(cands, profs, preps)
        assert report["ok"]
        assert report["stats"]["matieres"] == 2
        assert report["stats"]["profs"]     == 1
        assert report["stats"]["candidats"] == 1

    def test_cross_file_discipline_error(self, tmp_path):
        preps = tmp_path / "preps.csv"
        profs = tmp_path / "profs.csv"
        cands = tmp_path / "cands.csv"

        preps.write_text("Matiere;Matière court;Temps preparation (min);Duree (min)\n"
                         "Maths;M;20;15\n")
        profs.write_text("Nom;Disc.poste;Salle;Heure mini;Etab;Loge\n"
                         "Dupont;Physique;1;8;Lycée;A\n")  # discipline inconnue
        cands.write_text("CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs\n"
                         "Martin Paul (111111111AA);Maths;Maths;0;Lycée;\n")

        report = validate_all(cands, profs, preps)
        assert not report["ok"]
        assert any("Physique" in e["message"] for e in report["errors"])

    def test_missing_preps_cascades(self, tmp_path):
        profs = tmp_path / "profs.csv"
        cands = tmp_path / "cands.csv"
        profs.write_text("Nom;Disc.poste;Salle;Heure mini;Etab;Loge\nDupont;Maths;1;8;Lycée;A\n")
        cands.write_text("CANDIDAT;CHOIX DISCIPLINE 1;CHOIX DISCIPLINE 2;TT;Etab;Profs\n"
                         "Martin Paul (111);Maths;SES;0;Lycée;\n")
        report = validate_all(cands, profs, None)
        assert not report["ok"]
        # Sans preps, les disciplines sont inconnues → erreurs sur profs et candidats aussi
        files_with_errors = {e["file"] for e in report["errors"]}
        assert "preps" in files_with_errors
