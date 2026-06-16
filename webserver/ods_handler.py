"""
Lecture et génération de fichiers ODS pour le workflow CSV de l'algo.

parse_ods(data)          -> dict de feuilles : {nom_feuille: list[dict]}
generate_ods_modele(...) -> bytes du fichier ODS modèle (3 feuilles)
"""
from __future__ import annotations

import io

from odf.opendocument import OpenDocumentSpreadsheet, load as odf_load
from odf.style import Style, TableCellProperties, TextProperties
from odf.table import (
    Table, TableCell, TableRow,
    ContentValidation, ContentValidations,
)
from odf.text import P

# Disciplines standard (correspond à preps_modele.csv)
_DEFAULT_PREPS: list[tuple[str, str, int, int]] = [
    ("Lettres", "Lettres", 30, 20),
    ("Arts", "Arts", 30, 30),
    ("Histoire-Géographie, Géopolitique et Sciences Politiques", "HGGSP", 20, 20),
    ("Humanités, Littérature et Philosophie", "HLP", 20, 20),
    ("Numérique Sciences Informatiques", "NSI", 20, 20),
    ("Physique-Chimie", "PC", 20, 20),
    ("Sciences de la Vie et de la Terre", "SVT", 20, 20),
    ("Sciences Économiques et Sociales", "SES", 30, 20),
    ("Anglais", "Anglais", 20, 20),
    ("Mathématiques", "Maths", 20, 20),
    ("Management", "Mana", 40, 20),
    ("Droit Economie", "Droit Eco", 20, 20),
    ("Langues, Littératures et Cultures Etrangères", "LLCE", 20, 20),
    ("Anglais Monde comtemporain", "AMC", 20, 20),
    ("Philosophie", "Philo", 20, 20),
    ("Sciences Industrielles de L'ingénieur", "SII", 20, 20),
]

PREPS_HEADERS    = ["Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"]
EXAM_HEADERS     = ["Nom", "Disc.poste", "Salle", "Heure mini", "Etab", "Loge"]
CANDIDATS_HEADERS = ["CANDIDAT", "CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2", "TT", "Etab", "Profs"]


# ── Lecture ODS ───────────────────────────────────────────────────────────────

def _cell_text(cell) -> str:
    """Extrait le texte d'une cellule ODS."""
    parts = []
    for p in cell.getElementsByType(P):
        parts.append("".join(
            node.data for node in p.childNodes
            if hasattr(node, "data")
        ))
    return " ".join(parts).strip()


def _sheet_to_rows(table) -> list[dict]:
    """Convertit une Table ODS en list[dict] (1ère ligne = headers)."""
    all_rows_raw: list[list[str]] = []
    for row in table.getElementsByType(TableRow):
        cells = row.getElementsByType(TableCell)
        cols: list[str] = []
        for cell in cells:
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
            val = _cell_text(cell)
            cols.extend([val] * repeat)
        # Supprime les colonnes répétées vides en fin de ligne
        while cols and cols[-1] == "":
            cols.pop()
        all_rows_raw.append(cols)

    # Supprime les lignes entièrement vides
    all_rows_raw = [r for r in all_rows_raw if any(c for c in r)]
    if not all_rows_raw:
        return []

    headers = [h.strip() for h in all_rows_raw[0]]
    rows: list[dict] = []
    for raw in all_rows_raw[1:]:
        row_dict = {}
        for i, h in enumerate(headers):
            row_dict[h] = raw[i].strip() if i < len(raw) else ""
        rows.append(row_dict)
    return rows


def parse_ods(data: bytes) -> dict[str, list[dict]]:
    """
    Parse un fichier ODS et retourne un dict {nom_feuille_normalisé: list[dict]}.
    Les noms de feuilles sont normalisés en minuscules.
    Lève ValueError si le fichier est illisible.
    """
    try:
        doc = odf_load(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Impossible de lire le fichier ODS : {exc}") from exc

    result: dict[str, list[dict]] = {}
    for table in doc.spreadsheet.getElementsByType(Table):
        name = table.getAttribute("name") or ""
        key = name.strip().lower()
        result[key] = _sheet_to_rows(table)
    return result


# ── Génération ODS modèle ─────────────────────────────────────────────────────

def _make_cell(doc: OpenDocumentSpreadsheet, value: str,
               style_name: str | None = None) -> TableCell:
    kwargs: dict = {"valuetype": "string"}
    if style_name:
        kwargs["stylename"] = style_name
    cell = TableCell(**kwargs)
    cell.addElement(P(text=value))
    return cell


def _make_number_cell(doc: OpenDocumentSpreadsheet, value: int | str) -> TableCell:
    cell = TableCell(valuetype="float", value=str(value))
    cell.addElement(P(text=str(value)))
    return cell


def _add_header_style(doc: OpenDocumentSpreadsheet) -> str:
    style = Style(name="HeaderCell", family="table-cell")
    style.addElement(TextProperties(fontweight="bold"))
    style.addElement(TableCellProperties(
        backgroundcolor="#D9E1F2",
        border="0.5pt solid #4472C4",
    ))
    doc.automaticstyles.addElement(style)
    return "HeaderCell"


def _build_validation_list(names: list[str]) -> str:
    """Formate une liste de valeurs pour ODS content-validation."""
    quoted = [f'"{v}"' for v in names]
    return ";".join(quoted)


def generate_ods_modele(preps_rows: list[dict] | None = None) -> bytes:
    """
    Génère le fichier ODS modèle avec 3 feuilles :
      - preps        : pré-remplie (données ou valeurs par défaut)
      - examinateurs : en-têtes + validation disciplines / heure
      - candidats    : en-têtes + validation disciplines / TT
    Retourne les bytes du fichier .ods.
    """
    doc = OpenDocumentSpreadsheet()
    header_style = _add_header_style(doc)

    # ── Feuille preps ──────────────────────────────────────────────────────────
    sheet_preps = Table(name="preps")
    doc.spreadsheet.addElement(sheet_preps)

    # En-tête
    hr = TableRow()
    for h in PREPS_HEADERS:
        hr.addElement(_make_cell(doc, h, header_style))
    sheet_preps.addElement(hr)

    # Données
    if preps_rows:
        data_rows = [
            (
                r.get("Matiere", ""),
                r.get("Matière court", ""),
                r.get("Temps preparation (min)", ""),
                r.get("Duree (min)", ""),
            )
            for r in preps_rows
            if r.get("Matiere")
        ]
    else:
        data_rows = [(m, c, str(t), str(d)) for m, c, t, d in _DEFAULT_PREPS]

    short_names = [row[1] for row in data_rows if row[1]]

    for mat, court, tprep, duree in data_rows:
        row = TableRow()
        row.addElement(_make_cell(doc, mat))
        row.addElement(_make_cell(doc, court))
        try:
            row.addElement(_make_number_cell(doc, int(tprep)))
        except (ValueError, TypeError):
            row.addElement(_make_cell(doc, str(tprep)))
        try:
            row.addElement(_make_number_cell(doc, int(duree)))
        except (ValueError, TypeError):
            row.addElement(_make_cell(doc, str(duree)))
        sheet_preps.addElement(row)

    # ── Validations ODS ────────────────────────────────────────────────────────
    validations = ContentValidations()
    doc.spreadsheet.addElement(validations)

    disc_list = _build_validation_list(short_names) if short_names else '""'
    tt_list   = _build_validation_list(["0", "1"])

    # Validation disciplines (liste déroulante)
    val_disc = ContentValidation(
        name="vDisc",
        condition=f"of:cell-content-is-in-list({disc_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_disc)

    # Validation TT (0 ou 1)
    val_tt = ContentValidation(
        name="vTT",
        condition=f"of:cell-content-is-in-list({tt_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_tt)

    # Validation heure (entier 0-23)
    val_heure = ContentValidation(
        name="vHeure",
        condition="of:cell-content-is-between(0;23)",
        allowemptycell="true",
    )
    validations.addElement(val_heure)

    # ── Feuille examinateurs ──────────────────────────────────────────────────
    sheet_exam = Table(name="examinateurs")
    doc.spreadsheet.addElement(sheet_exam)

    # Colonnes avec validation
    # Nom(0) Disc.poste(1) Salle(2) Heure mini(3) Etab(4) Loge(5)
    exam_col_validations = {1: "vDisc", 3: "vHeure"}

    hr2 = TableRow()
    for h in EXAM_HEADERS:
        hr2.addElement(_make_cell(doc, h, header_style))
    sheet_exam.addElement(hr2)

    _add_empty_rows_with_validation(doc, sheet_exam, EXAM_HEADERS, exam_col_validations, 50)

    # ── Feuille candidats ─────────────────────────────────────────────────────
    sheet_cands = Table(name="candidats")
    doc.spreadsheet.addElement(sheet_cands)

    # CANDIDAT(0) CHOIX1(1) CHOIX2(2) TT(3) Etab(4) Profs(5)
    cand_col_validations = {1: "vDisc", 2: "vDisc", 3: "vTT"}

    hr3 = TableRow()
    for h in CANDIDATS_HEADERS:
        hr3.addElement(_make_cell(doc, h, header_style))
    sheet_cands.addElement(hr3)

    _add_empty_rows_with_validation(doc, sheet_cands, CANDIDATS_HEADERS, cand_col_validations, 200)

    # Sérialisation
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_empty_rows_with_validation(
    doc: OpenDocumentSpreadsheet,
    sheet: Table,
    headers: list[str],
    col_validations: dict[int, str],
    n_rows: int,
) -> None:
    """Ajoute n_rows lignes vides avec validation sur les colonnes indiquées."""
    for _ in range(n_rows):
        row = TableRow()
        for col_idx in range(len(headers)):
            if col_idx in col_validations:
                cell = TableCell(valuetype="string",
                                 contentvalidationname=col_validations[col_idx])
            else:
                cell = TableCell(valuetype="string")
            row.addElement(cell)
        sheet.addElement(row)
