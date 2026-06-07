"""
Validation et normalisation des fichiers CSV pour algo.py.

Usage :
    from csv_validator import normalize_csv, validate_all

    report = validate_all(candidats_path, profs_path, preps_path)
    # report["ok"]       -> bool
    # report["errors"]   -> [{"file": ..., "row": ..., "message": ...}]
    # report["warnings"] -> [{"file": ..., "row": ..., "message": ...}]
    # report["stats"]    -> {"candidats": int, "profs": int, "matieres": int}
"""

from __future__ import annotations

import io
import re
from csv import DictReader
from pathlib import Path
from typing import IO


# ── Normalisation ─────────────────────────────────────────────────────────────

def _decode(raw: bytes) -> str:
    """Décode bytes en str : UTF-8 BOM, UTF-8, puis latin-1 en fallback."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _detect_delimiter(text: str) -> str:
    """Renvoie ';' ou ',' selon lequel produit le plus de colonnes sur la 1ère ligne."""
    first = text.splitlines()[0] if text.strip() else ""
    return ";" if first.count(";") >= first.count(",") else ","


def normalize_csv(source: bytes | str | IO) -> tuple[list[dict], str]:
    """
    Normalise un CSV et retourne (rows, delimiter_utilisé).
    - Gère BOM UTF-8, encodage latin-1
    - Détecte automatiquement ';' vs ','
    - Strip les espaces des clés et valeurs
    """
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, str):
            text = raw
        else:
            text = _decode(raw)
    elif isinstance(source, bytes):
        text = _decode(source)
    else:
        text = source

    delim = _detect_delimiter(text)
    reader = DictReader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        rows.append({k.strip(): v.strip() if isinstance(v, str) else v
                     for k, v in row.items()})
    return rows, delim


def normalize_csv_file(path: Path) -> tuple[list[dict], str]:
    return normalize_csv(Path(path).read_bytes())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _issue(level: str, file: str, row: int | None, message: str) -> dict:
    return {"level": level, "file": file, "row": row, "message": message}

def _err(file, row, msg):  return _issue("error",   file, row, msg)
def _warn(file, row, msg): return _issue("warning", file, row, msg)

_INE_RE = re.compile(r'\(.+\)\s*$')
_HOUR_RE = re.compile(r'^\d{1,2}$')


# ── Validation preps.csv ──────────────────────────────────────────────────────

PREPS_COLS = {"Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"}

def validate_preps(rows: list[dict]) -> list[dict]:
    issues = []
    if not rows:
        return [_err("preps", None, "Fichier vide.")]

    missing = PREPS_COLS - set(rows[0].keys())
    if missing:
        issues.append(_err("preps", None,
            f"Colonnes manquantes : {', '.join(sorted(missing))}"))
        return issues

    noms, noms_courts = set(), set()
    for i, r in enumerate(rows, 1):
        nom = r.get("Matiere", "").strip()
        court = r.get("Matière court", "").strip()
        if not nom:
            issues.append(_err("preps", i, "Colonne 'Matiere' vide."))
        elif nom in noms:
            issues.append(_err("preps", i, f"Matière en double : '{nom}'."))
        else:
            noms.add(nom)

        if not court:
            issues.append(_warn("preps", i, f"Colonne 'Matière court' vide pour '{nom}'."))
        elif court in noms_courts:
            issues.append(_warn("preps", i, f"Nom court en double : '{court}'."))
        else:
            noms_courts.add(court)

        for col in ("Temps preparation (min)", "Duree (min)"):
            val = r.get(col, "").strip()
            if not val.isdigit() or int(val) <= 0:
                issues.append(_err("preps", i,
                    f"'{col}' doit être un entier positif (valeur : '{val}')."))

    return issues


# ── Validation profs_total.csv ────────────────────────────────────────────────

PROFS_COLS = {"Nom", "Disc.poste", "Salle", "Heure mini", "Etab", "Loge"}

def _mat_match(disc: str, matieres: set[str], noms_courts: set[str]) -> bool:
    """Correspondance insensible à la casse, identique à chercher_par_nom() d'algo.py."""
    d = disc.lower()
    return any(m.lower() == d for m in matieres | noms_courts)


def validate_profs(rows: list[dict], matieres: set[str], noms_courts: set[str]) -> list[dict]:
    issues = []
    if not rows:
        return [_err("profs", None, "Fichier vide.")]

    missing = PROFS_COLS - set(rows[0].keys())
    if missing:
        issues.append(_err("profs", None,
            f"Colonnes manquantes : {', '.join(sorted(missing))}"))
        return issues

    salles = set()
    for i, r in enumerate(rows, 1):
        nom = r.get("Nom", "").strip()
        if not nom:
            issues.append(_err("profs", i, "Colonne 'Nom' vide."))

        disc = r.get("Disc.poste", "").strip()
        if not disc:
            issues.append(_err("profs", i, f"Colonne 'Disc.poste' vide (prof '{nom}')."))
        elif not _mat_match(disc, matieres, noms_courts):
            issues.append(_err("profs", i,
                f"Discipline '{disc}' (prof '{nom}') introuvable dans preps.csv. "
                f"Valeurs attendues : {', '.join(sorted(matieres | noms_courts))}."))

        salle = r.get("Salle", "").strip()
        if not salle:
            issues.append(_err("profs", i, f"Colonne 'Salle' vide (prof '{nom}')."))
        elif salle in salles:
            issues.append(_warn("profs", i, f"Salle '{salle}' présente plusieurs fois."))
        else:
            salles.add(salle)

        heure = r.get("Heure mini", "").strip()
        if not _HOUR_RE.match(heure) or not (0 <= int(heure) <= 23):
            issues.append(_err("profs", i,
                f"'Heure mini' doit être un entier entre 0 et 23 (valeur : '{heure}')."))

        if not r.get("Loge", "").strip():
            issues.append(_warn("profs", i, f"Colonne 'Loge' vide (prof '{nom}')."))

    return issues


# ── Validation candidats.csv ──────────────────────────────────────────────────

CANDS_COLS = {"CANDIDAT", "CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2", "TT", "Etab", "Profs"}

def validate_candidats(rows: list[dict], matieres: set[str], noms_courts: set[str]) -> list[dict]:
    issues = []
    if not rows:
        return [_err("candidats", None, "Fichier vide.")]

    missing = CANDS_COLS - set(rows[0].keys())
    if missing:
        issues.append(_err("candidats", None,
            f"Colonnes manquantes : {', '.join(sorted(missing))}"))
        return issues

    ines = set()
    for i, r in enumerate(rows, 1):
        cand = r.get("CANDIDAT", "").strip()
        if not cand:
            issues.append(_err("candidats", i, "Colonne 'CANDIDAT' vide."))
        else:
            if not _INE_RE.search(cand):
                issues.append(_err("candidats", i,
                    f"Format invalide pour '{cand}' : attendu 'Nom Prénom (INE)'."))
            else:
                ine = cand.split("(")[-1].rstrip(")")
                if ine in ines:
                    issues.append(_err("candidats", i,
                        f"INE en double : '{ine}' (candidat '{cand}')."))
                else:
                    ines.add(ine)

        tt = r.get("TT", "").strip()
        if tt not in ("0", "1"):
            issues.append(_err("candidats", i,
                f"'TT' doit être 0 ou 1 (valeur : '{tt}', candidat '{cand}')."))

        for col in ("CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2"):
            disc = r.get(col, "").strip()
            if not disc:
                issues.append(_err("candidats", i,
                    f"'{col}' vide (candidat '{cand}')."))
            elif not _mat_match(disc, matieres, noms_courts):
                issues.append(_err("candidats", i,
                    f"'{col}' = '{disc}' introuvable dans preps.csv "
                    f"(candidat '{cand}'). Valeurs : {', '.join(sorted(matieres | noms_courts))}."))

    return issues


# ── Rapport complet ───────────────────────────────────────────────────────────

def validate_all(candidats_path: Path | None,
                 profs_path: Path | None,
                 preps_path: Path | None) -> dict:
    """
    Valide les trois fichiers CSV et renvoie un rapport consolidé.
    Les fichiers absents génèrent une erreur dédiée.
    """
    issues: list[dict] = []
    stats = {"candidats": 0, "profs": 0, "matieres": 0}
    matieres: set[str] = set()
    noms_courts: set[str] = set()

    # ── preps (doit être validé en premier pour fournir les matières aux autres) ──
    if not preps_path or not Path(preps_path).exists():
        issues.append(_err("preps", None, "Fichier preps.csv absent."))
        preps_rows: list[dict] = []
    else:
        preps_rows, _ = normalize_csv_file(preps_path)
        issues.extend(validate_preps(preps_rows))
        matieres   = {r.get("Matiere", "").strip()       for r in preps_rows if r.get("Matiere")}
        noms_courts = {r.get("Matière court", "").strip() for r in preps_rows if r.get("Matière court")}
        stats["matieres"] = len(matieres)

    # ── profs ─────────────────────────────────────────────────────────────────
    if not profs_path or not Path(profs_path).exists():
        issues.append(_err("profs", None, "Fichier profs_total.csv absent."))
    else:
        profs_rows, _ = normalize_csv_file(profs_path)
        issues.extend(validate_profs(profs_rows, matieres, noms_courts))
        stats["profs"] = len(profs_rows)

    # ── candidats ─────────────────────────────────────────────────────────────
    if not candidats_path or not Path(candidats_path).exists():
        issues.append(_err("candidats", None, "Fichier candidats.csv absent."))
    else:
        cands_rows, _ = normalize_csv_file(candidats_path)
        issues.extend(validate_candidats(cands_rows, matieres, noms_courts))
        stats["candidats"] = len(cands_rows)

    errors   = [x for x in issues if x["level"] == "error"]
    warnings = [x for x in issues if x["level"] == "warning"]
    return {
        "ok":       len(errors) == 0,
        "errors":   errors,
        "warnings": warnings,
        "stats":    stats,
    }
