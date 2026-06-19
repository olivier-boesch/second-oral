#!/usr/bin/env python3
"""
update_lycees.py — Met à jour _LYCEES_AIM dans webserver/ods_handler.py.

Récupère depuis data.education.gouv.fr les lycées GT, généraux, technologiques
et polyvalents de l'académie d'Aix-Marseille (code_nature 300/301/302/306) et
réécrit le bloc délimité par les marqueurs LYCEES_AIM_BEGIN / LYCEES_AIM_END.

Usage :
    python update_lycees.py              # met à jour ods_handler.py
    python update_lycees.py --dry-run    # affiche sans modifier
    python update_lycees.py --check      # retourne 1 si la liste a changé
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
ODS_HANDLER  = PROJECT_ROOT / "webserver" / "ods_handler.py"

MARKER_BEGIN = "# <<< LYCEES_AIM_BEGIN >>>"
MARKER_END   = "# <<< LYCEES_AIM_END >>>"

API_BASE = (
    "https://data.education.gouv.fr"
    "/api/explore/v2.1/catalog/datasets/fr-en-annuaire-education/records"
)
API_WHERE  = (
    'libelle_academie="Aix-Marseille"'
    ' AND type_etablissement="Lycée"'
    ' AND code_nature in (300,301,302,306)'
    ' AND etat="OUVERT"'
)
API_FIELDS = "identifiant_de_l_etablissement,nom_etablissement,nom_commune,telephone"

# UAI à exclure (hôteliers classés polyvalent)
EXCLUDE_UAI: set[str] = {"0840083J", "0132974M", "0133366N"}


# ── Récupération API ──────────────────────────────────────────────────────────

def fetch_lycees(timeout: int = 20) -> list[dict]:
    """Récupère tous les lycées GT/polyvalents de l'académie depuis l'API."""
    records: list[dict] = []
    for offset in range(0, 300, 100):
        params = urllib.parse.urlencode({
            "where":  API_WHERE,
            "limit":  100,
            "select": API_FIELDS,
            "offset": offset,
        })
        url = f"{API_BASE}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(f"Erreur API data.education.gouv.fr : {exc}") from exc

        batch = data.get("results", [])
        records.extend(batch)
        if len(records) >= data.get("total_count", 0):
            break
    return records


# ── Simplification du nom ─────────────────────────────────────────────────────

def short_name(nom: str) -> str:
    """
    Extrait le nom propre du lycée en supprimant :
    - les suffixes administratifs entre parenthèses (LP PR HC, etc.)
    - le préfixe de type ("Lycée polyvalent", "Lycée technologique régional"…)
    - les qualificatifs résiduels ("régional", "international"…)
    """
    # Supprime suffixes entre parenthèses : types administratifs, mentions secondaires
    nom = re.sub(
        r'\s*\('
        r'(?:[A-Z]{2}[^)]*'              # LP PR HC, LT PR HC, LG PR HC…
        r'|[Ee]cole secondaire[^)'
        r']*'                            # Ecole secondaire générale…
        r'|anciennement[^)]*'            # anciennement lycée du Rempart
        r'|[Ee]tablissement[^)]*'        # Etablissement secondaire…
        r')\)\s*$',
        '', nom,
    ).strip()

    # Retire le préfixe "Lycée [qualificatifs]? "
    prefix = re.match(
        r"^Lyc[ée]e\s+"
        r"(?:(?:polyvalent|g[eé]n[eé]ral|technologique|r[eé]gional"
        r"|international|militaire|climatique|priv[eé]"
        r"|des\s+m[eé]tiers|cit[eé]\s+internationale?"
        r"|d[''']enseignement\s+g[eé]n[eé]ral\s+et\s+technologique"
        r"|g[eé]n[eé]ral\s+et\s+technologique"
        r"|de\s+chimie-biologie|hôtelier|hotelier)\s+)*",
        nom, re.I,
    )
    if prefix:
        rest = nom[prefix.end():].strip()
        # Supprime qualificatifs résiduels en tête de reste
        rest = re.sub(
            r"^(?:r[eé]gional|international|priv[eé]"
            r"|hôtelier|hotelier|agricole"
            r"|et\s+technologique|cit[eé]\s+internationale?)\s+",
            "", rest, flags=re.I,
        ).strip()
        if rest:
            return rest
    return nom.strip()


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalize_commune(commune: str) -> str:
    """Normalise les espaces multiples dans le nom de commune."""
    return re.sub(r'\s{2,}', ' ', commune.strip())


def build_aim_list(
    records: list[dict],
) -> list[tuple[str, str, str, str]]:
    """Construit la liste (UAI, NomCourt, Ville, Tel) triée par ville puis nom."""
    result = []
    for r in records:
        uai = r["identifiant_de_l_etablissement"]
        if uai in EXCLUDE_UAI:
            continue
        nom   = short_name(r["nom_etablissement"])
        ville = normalize_commune(r["nom_commune"])
        tel   = (r.get("telephone") or "").replace(" ", "")
        result.append((uai, nom, ville, tel))
    result.sort(key=lambda t: (t[2], t[1]))
    return result


# ── Sérialisation du bloc Python ──────────────────────────────────────────────

def _format_tuple(uai: str, nom: str, ville: str, tel: str) -> str:
    return f"    ({uai!r}, {nom!r}, {ville!r}, {tel!r}),"


def build_block(aim: list[tuple[str, str, str, str]]) -> str:
    """Génère le texte complet du bloc délimité par les marqueurs."""
    today = date.today().isoformat()
    lines = [
        MARKER_BEGIN,
        "# Lycées GT, généraux, technologiques et polyvalents académie Aix-Marseille",
        "# Source : data.education.gouv.fr, annuaire éducation (code_nature 300/301/302/306)",
        f"# Mis à jour le {today} via : python update_lycees.py",
        "# Format : (UAI, NomCourt, Ville, Téléphone)",
        "_LYCEES_AIM: list[tuple[str, str, str, str]] = [",
    ]
    lines.extend(_format_tuple(*t) for t in aim)
    lines.append("]")
    lines.append(MARKER_END)
    return "\n".join(lines)


# ── Réécriture de ods_handler.py ──────────────────────────────────────────────

def update_file(new_block: str, dry_run: bool = False) -> bool:
    """
    Remplace le bloc LYCEES_AIM dans ODS_HANDLER par new_block.
    Retourne True si le fichier a changé (ou aurait changé en dry-run).
    """
    source = ODS_HANDLER.read_text(encoding="utf-8")

    pattern = re.compile(
        rf"{re.escape(MARKER_BEGIN)}.*?{re.escape(MARKER_END)}",
        re.DOTALL,
    )
    if not pattern.search(source):
        raise ValueError(
            f"Marqueurs introuvables dans {ODS_HANDLER}.\n"
            f"Attendus : {MARKER_BEGIN!r} … {MARKER_END!r}"
        )

    updated = pattern.sub(new_block, source)
    changed = updated != source

    if not dry_run and changed:
        ODS_HANDLER.write_text(updated, encoding="utf-8")

    return changed


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run(dry_run: bool = False, check: bool = False,
        verbose: bool = True) -> int:
    """
    Exécute la mise à jour.
    Retourne 0 si OK, 1 si erreur ou (en mode --check) si la liste a changé.
    """
    if verbose:
        print("  Récupération des lycées depuis data.education.gouv.fr…", flush=True)
    try:
        records = fetch_lycees()
    except RuntimeError as exc:
        print(f"  ✘ {exc}", file=sys.stderr)
        return 1

    aim = build_aim_list(records)
    if verbose:
        print(f"  {len(aim)} lycées récupérés.", flush=True)

    new_block = build_block(aim)

    try:
        changed = update_file(new_block, dry_run=dry_run or check)
    except (ValueError, OSError) as exc:
        print(f"  ✘ {exc}", file=sys.stderr)
        return 1

    if check:
        if changed:
            print("  ⚠ La liste a changé — relancez update_lycees.py pour mettre à jour.")
            return 1
        print("  ✔ La liste est à jour.")
        return 0

    if dry_run:
        print(new_block)
        return 0

    if changed:
        if verbose:
            print(f"  ✔ {ODS_HANDLER.name} mis à jour ({len(aim)} lycées).")
    else:
        if verbose:
            print(f"  ✔ {ODS_HANDLER.name} déjà à jour — aucune modification.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Met à jour _LYCEES_AIM dans webserver/ods_handler.py",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche le nouveau bloc sans modifier le fichier",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Retourne 1 si la liste a changé (utile en CI)",
    )
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, check=args.check))


if __name__ == "__main__":
    main()
