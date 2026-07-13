"""
Validation et normalisation des fichiers CSV pour algo.py.

Usage :
    from csv_validator import normalize_csv, validate_all

    report = validate_all(candidats_path, examinateurs_path, preps_path)
    # report["ok"]       -> bool
    # report["errors"]   -> [{"file": ..., "row": ..., "message": ...}]
    # report["warnings"] -> [{"file": ..., "row": ..., "message": ...}]
    # report["stats"]    -> {"candidats": int, "examinateurs": int, "matieres": int}
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
# 'Heure mini' : heure entière ('9') ou heure:minute ('9:30'), cf. algo.parser_heure_mini
_HOUR_RE = re.compile(r'^(\d{1,2})(?::(\d{1,2}))?$')
# 'Téléphone' : chiffres/espaces/+/./- , tolérant (formats FR et international)
_TEL_RE = re.compile(r'^[\d +().-]{6,20}$')


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


# ── Validation examinateurs.csv ──────────────────────────────────────────────

PROFS_COLS = {"Nom", "Disc.poste", "Salle", "Heure mini", "Etab", "Loge"}

def _mat_match(disc: str, matieres: set[str], noms_courts: set[str]) -> bool:
    """Correspondance insensible à la casse, identique à chercher_par_nom() d'algo.py."""
    d = disc.lower()
    return any(m.lower() == d for m in matieres | noms_courts)


def validate_profs(rows: list[dict], matieres: set[str], noms_courts: set[str]) -> list[dict]:
    issues = []
    if not rows:
        return [_err("examinateurs", None, "Fichier vide.")]

    missing = PROFS_COLS - set(rows[0].keys())
    if missing:
        issues.append(_err("examinateurs", None,
            f"Colonnes manquantes : {', '.join(sorted(missing))}"))
        return issues

    salles = set()
    for i, r in enumerate(rows, 1):
        nom = r.get("Nom", "").strip()
        if not nom:
            issues.append(_err("examinateurs", i, "Colonne 'Nom' vide."))

        disc = r.get("Disc.poste", "").strip()
        if not disc:
            issues.append(_err("examinateurs", i, f"Colonne 'Disc.poste' vide (prof '{nom}')."))
        elif not _mat_match(disc, matieres, noms_courts):
            issues.append(_err("examinateurs", i,
                f"Discipline '{disc}' (prof '{nom}') introuvable dans preps.csv. "
                f"Valeurs attendues : {', '.join(sorted(matieres | noms_courts))}."))

        salle = r.get("Salle", "").strip()
        if not salle:
            issues.append(_err("examinateurs", i, f"Colonne 'Salle' vide (prof '{nom}')."))
        elif salle in salles:
            # Cas légitime : plusieurs examinateurs peuvent partager une même
            # salle à des horaires différents dans la journée (chacun reçoit
            # un identifiant de connexion distinct, indépendant de la salle).
            # Le simple avertissement rappelle de vérifier l'absence de
            # chevauchement horaire entre eux, non garantie automatiquement.
            issues.append(_warn(
                "examinateurs", i,
                f"Salle '{salle}' présente plusieurs fois — vérifier que les "
                "examinateurs concernés ne se chevauchent pas dans le planning.",
            ))
        else:
            salles.add(salle)

        heure = r.get("Heure mini", "").strip()
        m = _HOUR_RE.match(heure)
        heure_ok = (
            m is not None and 0 <= int(m.group(1)) <= 23
            and (m.group(2) is None or 0 <= int(m.group(2)) <= 59)
        )
        if not heure_ok:
            issues.append(_err("examinateurs", i,
                f"'Heure mini' doit être une heure entre 0 et 23, au format 'H' ou 'H:MM' "
                f"(valeur : '{heure}')."))

        if not r.get("Loge", "").strip():
            issues.append(_warn("examinateurs", i, f"Colonne 'Loge' vide (prof '{nom}')."))

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
                    f"Format invalide pour '{cand}' : attendu 'Nom Prénom (numéro)'."))
            else:
                numero = cand.split("(")[-1].rstrip(")")
                if numero in ines:
                    issues.append(_err("candidats", i,
                        f"Numéro en double : '{numero}' (candidat '{cand}')."))
                else:
                    ines.add(numero)

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

        # 'Téléphone' : colonne optionnelle (absente des anciens modèles CSV/ODS),
        # non bloquante — seulement un avertissement si renseignée mais suspecte.
        tel = r.get("Téléphone", "").strip()
        if tel and not _TEL_RE.match(tel):
            issues.append(_warn("candidats", i,
                f"Format de téléphone suspect : '{tel}' (candidat '{cand}')."))

    return issues


# ── Rapport complet ───────────────────────────────────────────────────────────

def validate_all(candidats_path: Path | None,
                 examinateurs_path: Path | None,
                 preps_path: Path | None) -> dict:
    """
    Valide les trois fichiers CSV et renvoie un rapport consolidé.
    Les fichiers absents génèrent une erreur dédiée.
    """
    issues: list[dict] = []
    stats = {"candidats": 0, "examinateurs": 0, "matieres": 0}
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

    # ── examinateurs ──────────────────────────────────────────────────────────
    if not examinateurs_path or not Path(examinateurs_path).exists():
        issues.append(_err("examinateurs", None, "Fichier examinateurs.csv absent."))
    else:
        profs_rows, _ = normalize_csv_file(examinateurs_path)
        issues.extend(validate_profs(profs_rows, matieres, noms_courts))
        stats["examinateurs"] = len(profs_rows)

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
