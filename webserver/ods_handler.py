"""
Lecture et génération de fichiers ODS pour le workflow CSV de l'algo.

parse_ods(data)          -> dict de feuilles : {nom_feuille: list[dict]}
generate_ods_modele(...) -> bytes du fichier ODS modèle (4 feuilles)
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

# <<< LYCEES_AIM_BEGIN >>>
# Lycées GT, généraux, technologiques et polyvalents académie Aix-Marseille
# Source : data.education.gouv.fr, annuaire éducation (code_nature 300/301/302/306)
# Mis à jour le 2026-06-19 via : python update_lycees.py
# Format : (UAI, NomCourt, Ville, Téléphone)
_LYCEES_AIM: list[tuple[str, str, str, str]] = [
    ('0133425C', 'Célony', 'Aix-en-Provence', '0442235965'),
    ('0131669U', 'Ecole privée Val Saint André', 'Aix-en-Provence', '0442271447'),
    ('0133525L', 'Georges Duby', 'Aix-en-Provence', '0442608600'),
    ('0133148B', 'IBS Of Provence', 'Aix-en-Provence', '0442240340'),
    ('0131319N', 'La Nativité', 'Aix-en-Provence', '0442934570'),
    ('0130002G', 'Paul Cézanne', 'Aix-en-Provence', '0442171400'),
    ('0131391S', 'Saint-Éloi', 'Aix-en-Provence', '0442234499'),
    ('0131320P', 'Sainte-Catherine de Sienne', 'Aix-en-Provence', '0442234898'),
    ('0133395V', 'Sainte-Marie', 'Aix-en-Provence', '0442231112'),
    ('0130003H', 'Vauvenargues', 'Aix-en-Provence', '0442174040'),
    ('0131596P', "d'Aix-en-Provence", 'Aix-en-Provence', '0442238958'),
    ('0131862D', 'du Sacré-Coeur', 'Aix-en-Provence', '0442384132'),
    ('0130001F', 'Émile Zola', 'Aix-en-Provence', '0442938700'),
    ('0134253C', 'Monte-Cristo', 'Allauch', '0491641923'),
    ('0840001V', 'Charles de Gaulle', 'Apt', '0490741119'),
    ('0130011S', 'Louis Pasquet', 'Arles', '0490183515'),
    ('0130010R', 'Montmajour', 'Arles', '0490968050'),
    ('0131549N', 'Irène et Frédéric Joliot-Curie', 'Aubagne', '0442185151'),
    ('0132810J', 'Sainte-Marie', 'Aubagne', '0442031540'),
    ('0840110N', 'François Pétrarque', 'Avignon', '0490134313'),
    ('0840003X', 'Frédéric Mistral', 'Avignon', '0490804500'),
    ('0840940R', 'La Salle', 'Avignon', '0490145656'),
    ('0840059H', 'Louis Pasteur', 'Avignon', '0490145777'),
    ('0840005Z', 'Philippe de Girard', 'Avignon', '0413951000'),
    ('0840935K', 'René Char', 'Avignon', '0490880404'),
    ('0840072X', 'Saint-Joseph', 'Avignon', '0490145600'),
    ('0841230F', 'TSGE PR HC - COURS PRIVE PYTHAGORE', 'Avignon', '0432763562'),
    ('0840004Y', 'Théodore Aubanel', 'Avignon', '0490163600'),
    ('0040003G', 'André Honnorat', 'Barcelonnette', '0492807010'),
    ('0841093G', 'Lucie Aubrac', 'Bollène', '0432803190'),
    ('0050003B', "d'altitude - Suzanne Joulié Roos", 'Briançon', '0492213084'),
    ('0840015K', 'Jean-Henri Fabre', 'Carpentras', '0490630583'),
    ('0840607D', 'Louis Giraud', 'Carpentras', '0490608080'),
    ('0840078D', 'Marie Pila', 'Carpentras', '0490630093'),
    ('0840016L', 'Victor Hugo', 'Carpentras', '0490631232'),
    ('0840017M', 'Ismaël Dauphin', 'Cavaillon', '0490710981'),
    ('0134252B', "Jean D'Ormesson", 'Châteaurenard', '0490205950'),
    ('0040027H', 'Alexandra David-Néel', 'Digne-les-Bains', '0492303580'),
    ('0040490L', 'Pierre-Gilles de Gennes', 'Digne-les-Bains', '0492367190'),
    ('0040034R', 'du Sacré-Coeur', 'Digne-les-Bains', '0492305860'),
    ('0134000C', 'Sainte Victoire International School', 'Fuveau', '0647007672'),
    ('0050012L', 'Agricampus Hautes-Alpes', 'Gap', '0492510436'),
    ('0050007F', 'Aristide Briand', 'Gap', '0492522805'),
    ('0050006E', 'Dominique Villars', 'Gap', '0492522691'),
    ('0050035L', 'Saint-Joseph', 'Gap', '0492538444'),
    ('0133244F', 'Marie-Madeleine Fourcade', 'Gardanne', '0442659070'),
    ('0131656E', "d'Aix-Valabre", 'Gardanne', '0442654320'),
    ('0133314G', 'Saint-Louis-Sainte-Marie', 'Gignac-la-Nerthe', '0442317300'),
    ('0133822J', 'Saint-Jean de Garguier', 'Gémenos', '0442188818'),
    ('0132495S', 'Arthur Rimbaud', 'Istres', '0442411096'),
    ('0840021S', 'Alphonse Benoît', "L'Isle-sur-la-Sorgue", '0490206420'),
    ('0131747D', 'Auguste et Louis Lumière', 'La Ciotat', '0442083838'),
    ('0133406G', 'de la Méditerranée', 'La Ciotat', '0442088020'),
    ('0040056P', 'Carmejane - Maurice Plantier', 'Le Chaffaut-Saint-Jurson', '0492303570'),
    ('0040010P', 'Félix Esclangon', 'Manosque', '0492705470'),
    ('0040533H', 'Les Iscles', 'Manosque', '0492734110'),
    ('0132410Z', 'Maurice Genevoix', 'Marignane', '0442887690'),
    ('0133555U', 'Ami', 'Marseille', '0496100850'),
    ('0132733A', 'Antonin Artaud', 'Marseille', '0491122250'),
    ('0133286B', 'Belsunce', 'Marseille', '0491905114'),
    ('0134250Z', 'Bnei Elazar', 'Marseille', '0491202913'),
    ('0131402D', 'Charles Péguy', 'Marseille', '0491157640'),
    ('0131335F', 'Chevreul - Blancarde', 'Marseille', '0491491073'),
    ('0131344R', 'Cours Bastide', 'Marseille', '0491486796'),
    ('0130049H', 'César Baldaccini', 'Marseille', '0491143280'),
    ('0130050J', 'Denis Diderot', 'Marseille', '0491100700'),
    ('0133396W', 'Don Bosco', 'Marseille', '0491140000'),
    ('0133446A', 'Hamaskaïne', 'Marseille', '0491937525'),
    ('0130175V', 'Honoré Daumier', 'Marseille', '0491760120'),
    ('0134107U', 'Ibn Khaldoun', 'Marseille', '0491489568'),
    ('0134472R', 'Jacques Chirac', 'Marseille', '0486830940'),
    ('0130053M', 'Jean Perrin', 'Marseille', '0491742930'),
    ('0131345S', "L'Olivier - Robert Coffy", 'Marseille', '0491939550'),
    ('0132828D', 'La Cadenelle', 'Marseille', '0491181050'),
    ('0131681G', 'La Forbine', 'Marseille', '0491446048'),
    ('0131324U', 'Lacordaire', 'Marseille', '0491122080'),
    ('0130037V', 'Marcel Pagnol', 'Marseille', '0491876400'),
    ('0130051K', 'Marie Curie', 'Marseille', '0491365210'),
    ('0133474F', 'Marie Gasquet', 'Marseille', '0491851081'),
    ('0130038W', 'Marseilleveyre', 'Marseille', '0491176700'),
    ('0131398Z', 'Maximilien de Sully', 'Marseille', '0491482787'),
    ('0130042A', 'Montgrand', 'Marseille', '0496112530'),
    ('0134003F', 'Nelson Mandela', 'Marseille', '0491180250'),
    ('0131333D', 'Notre-Dame de France', 'Marseille', '0491371755'),
    ('0131341M', 'Notre-Dame de Sion', 'Marseille', '0491157450'),
    ('0133931C', 'Notre-Dame de la Viste', 'Marseille', '0491609057'),
    ('0133334D', 'ORT Léon Bramson', 'Marseille', '0491296133'),
    ('0131456M', 'Pastré - Grande Bastide', 'Marseille', '0496190606'),
    ('0131328Y', 'Paul Melizan', 'Marseille', '0491188070'),
    ('0130036U', 'Périer', 'Marseille', '0491133900'),
    ('0130039X', 'Saint-Charles', 'Marseille', '0491082050'),
    ('0131342N', 'Saint-Charles Camas', 'Marseille', '0495081240'),
    ('0130048G', 'Saint-Exupéry', 'Marseille', '0491096900'),
    ('0131339K', 'Saint-Joseph de la Madeleine', 'Marseille', '0496121360'),
    ('0131331B', 'Saint-Joseph les Maristes', 'Marseille', '0496101330'),
    ('0134101M', 'Saint-Louis', 'Marseille', '0491658820'),
    ('0131403E', 'Saint-Vincent de Paul', 'Marseille', '0491374886'),
    ('0131347U', 'Sainte-Trinité', 'Marseille', '0491411198'),
    ('0134155W', 'Simone Veil', 'Marseille', '0491815911'),
    ('0131348V', 'Sévigné', 'Marseille', '0491662275'),
    ('0130040Y', 'Thiers', 'Marseille', '0491189218'),
    ('0130043B', 'Victor Hugo', 'Marseille', '0491110500'),
    ('0132472S', 'Yavné', 'Marseille', '0491661477'),
    ('0131323T', 'de Provence', 'Marseille', '0491772846'),
    ('0131327X', 'de Tour Sainte', 'Marseille', '0491215300'),
    ('0131436R', 'modèle électronique', 'Marseille', '0491446537'),
    ('0132965C', 'Ecole privée Beth-Myriam', 'Marseille 10e Arrondissement', '0491757104'),
    ('0134605K', 'HORS CONTRAT COLLEL', 'Marseille 10e Arrondissement', ''),
    ('0134522V', 'GT PR.HC HEDER KEHILA LECHEM CHAMAIM', 'Marseille 6e Arrondissement', '0484188953'),
    ('0134514L', 'LYC PR  HAYA MOUCHKA', 'Marseille 8e Arrondissement', '0491453334'),
    ('0132210G', 'Jean Lurçat', 'Martigues', '0442413180'),
    ('0130143K', 'Paul Langevin', 'Martigues', '0442800875'),
    ('0133195C', 'Jean Cocteau', 'Miramas', '0490500298'),
    ('0840075A', 'Saint-Louis', 'Orange', '0490340150'),
    ('0840026X', "de l'Arc", 'Orange', '0490118300'),
    ('0840918S', 'Val de Durance - Henri Silvy', 'Pertuis', '0490092500'),
    ('0132280H', 'Henri Leroy', 'Port-Saint-Louis-du-Rhône', '0442860157'),
    ('0134004G', 'Saint-Charles', 'Saint-Martin-de-Crau', '0490185903'),
    ('0130161E', 'Adam de Craponne', 'Salon-de-Provence', '0490562468'),
    ('0131360H', 'Viala Lacoste', 'Salon-de-Provence', '0490568969'),
    ('0130160D', "de l'Empéri", 'Salon-de-Provence', '0490447900'),
    ('0040023D', 'Paul Arène', 'Sisteron', '0492610299'),
    ('0841249B', 'STEINER', 'Sorgues', '0490833707'),
    ('0130164H', 'Alphonse Daudet', 'Tarascon', '0490911823'),
    ('0841117H', 'Stéphane Hessel', 'Vaison-la-Romaine', '0490360203'),
    ('0841158C', 'Saint-Jean le Baptiste', 'Valréas', '0490350165'),
    ('0133424B', 'Caucadis', 'Vitrolles', '0442894202'),
    ('0133288D', 'Jean Monnet', 'Vitrolles', '0442151460'),
    ('0133015G', 'Pierre Mendès-France', 'Vitrolles', '0442898979'),
]
# <<< LYCEES_AIM_END >>>

LYCEES_SHEET_NAME = "lycees"
LYCEES_N_DATA_ROWS = len(_LYCEES_AIM)
LYCEES_HEADERS = ["UAI", "Nom", "Ville", "Téléphone", "Etab"]

# Liste affichable : même format que la colonne E de l'ODS ("Nom — Ville (UAI)")
LYCEES_DISPLAY = [f"{nom} — {ville} ({uai})" for uai, nom, ville, _ in _LYCEES_AIM]

PREPS_HEADERS     = ["Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"]
EXAM_HEADERS      = ["Nom", "Disc.poste", "Salle", "Heure mini", "Etab", "Loge"]
CANDIDATS_HEADERS = ["CANDIDAT", "CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2", "TT", "Etab", "Profs", "Téléphone"]

# En-têtes de la feuille ODS examinateurs : 3 colonnes Etab distinctes,
# fusionnées en un seul champ "Etab" lors de la lecture (parse_ods).
_EXAM_ODS_HEADERS = ["Nom", "Disc.poste", "Salle", "Heure mini", "Etab1", "Etab2", "Etab3", "Loge"]


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


def _merge_exam_etabs(rows: list[dict]) -> list[dict]:
    """
    Convertit les colonnes Etab1/Etab2/Etab3 en un seul champ Etab (virgule-séparé).
    Appelé sur la feuille examinateurs quand le modèle ODS à 3 colonnes est utilisé.
    """
    if not rows or "Etab1" not in rows[0]:
        return rows
    merged = []
    for r in rows:
        new_row: dict = {}
        for k, v in r.items():
            if k == "Etab1":
                new_row["Etab"] = ",".join(
                    r.get(f"Etab{n}", "").strip()
                    for n in (1, 2, 3)
                    if r.get(f"Etab{n}", "").strip()
                )
            elif k in ("Etab2", "Etab3"):
                continue
            else:
                new_row[k] = v
        merged.append(new_row)
    return merged


def parse_ods(data: bytes) -> dict[str, list[dict]]:
    """
    Parse un fichier ODS et retourne un dict {nom_feuille_normalisé: list[dict]}.
    Les noms de feuilles sont normalisés en minuscules.
    Pour la feuille examinateurs, fusionne Etab1/Etab2/Etab3 → Etab.
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
        rows = _sheet_to_rows(table)
        result[key] = _merge_exam_etabs(rows) if key == "examinateurs" else rows
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


def _make_formula_cell(formula: str, cached_text: str = "") -> TableCell:
    """Cellule avec formule ODF (résultat de type string) et valeur cachée."""
    cell = TableCell(valuetype="string", formula=formula)
    if cached_text:
        cell.addElement(P(text=cached_text))
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
    Génère le fichier ODS modèle avec 4 feuilles :
      - candidats    : en-têtes + validation disciplines / TT / Etab (lycees)
      - examinateurs : en-têtes + validation disciplines / heure / Etab (lycees)
      - preps        : pré-remplie (données ou valeurs par défaut)
      - lycees       : 249 lycées académie Aix-Marseille avec formule Ville—Nom (UAI)
    Retourne les bytes du fichier .ods.
    """
    doc = OpenDocumentSpreadsheet()
    header_style = _add_header_style(doc)

    # ── Feuille preps ──────────────────────────────────────────────────────────
    sheet_preps = Table(name="preps")

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

    # ── Feuille lycees ─────────────────────────────────────────────────────────
    sheet_lycees = Table(name=LYCEES_SHEET_NAME)

    hr_l = TableRow()
    for h in LYCEES_HEADERS:
        hr_l.addElement(_make_cell(doc, h, header_style))
    sheet_lycees.addElement(hr_l)

    for i, (uai, nom, ville, tel) in enumerate(_LYCEES_AIM):
        # Ligne ODS = i+2 (la ligne 1 est l'en-tête)
        ods_row = i + 2
        formula = f"of:=B{ods_row}&\" — \"&C{ods_row}&\" (\"&A{ods_row}&\")\""
        cached = f"{nom} — {ville} ({uai})"
        row = TableRow()
        row.addElement(_make_cell(doc, uai))
        row.addElement(_make_cell(doc, nom))
        row.addElement(_make_cell(doc, ville))
        row.addElement(_make_cell(doc, tel))
        row.addElement(_make_formula_cell(formula, cached))
        sheet_lycees.addElement(row)

    # ── Validations ODS ────────────────────────────────────────────────────────
    validations = ContentValidations()
    doc.spreadsheet.addElement(validations)

    disc_list = _build_validation_list(short_names) if short_names else '""'
    tt_list   = _build_validation_list(["0", "1"])
    # Validation Etab : liste déroulante depuis la colonne E (Etab) de la feuille lycees
    etab_range = (
        f"of:cell-content-is-in-list(${LYCEES_SHEET_NAME}.$E$2:"
        f"${LYCEES_SHEET_NAME}.$E${LYCEES_N_DATA_ROWS + 1})"
    )

    val_disc = ContentValidation(
        name="vDisc",
        condition=f"of:cell-content-is-in-list({disc_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_disc)

    val_tt = ContentValidation(
        name="vTT",
        condition=f"of:cell-content-is-in-list({tt_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_tt)

    val_etab = ContentValidation(
        name="vEtab",
        condition=etab_range,
        allowemptycell="true",
        displaylist="sorted-ascending",
    )
    validations.addElement(val_etab)

    # ── Feuille candidats ─────────────────────────────────────────────────────
    # CANDIDAT(0) CHOIX1(1) CHOIX2(2) TT(3) Etab(4) Profs(5) Téléphone(6)
    sheet_cands = Table(name="candidats")
    doc.spreadsheet.addElement(sheet_cands)

    cand_col_validations = {1: "vDisc", 2: "vDisc", 3: "vTT", 4: "vEtab"}

    hr3 = TableRow()
    for h in CANDIDATS_HEADERS:
        hr3.addElement(_make_cell(doc, h, header_style))
    sheet_cands.addElement(hr3)

    _add_empty_rows_with_validation(doc, sheet_cands, CANDIDATS_HEADERS, cand_col_validations, 200)

    # ── Feuille examinateurs ──────────────────────────────────────────────────
    # Nom(0) Disc.poste(1) Salle(2) Heure mini(3) Etab1(4) Etab2(5) Etab3(6) Loge(7)
    sheet_exam = Table(name="examinateurs")
    doc.spreadsheet.addElement(sheet_exam)

    # 'Heure mini' (3) : pas de validation ODS stricte (accepte 'H' ou 'H:MM',
    # un intervalle numérique bloquerait la saisie des minutes) — vérifié par
    # csv_validator.py à l'upload.
    exam_col_validations = {1: "vDisc", 4: "vEtab", 5: "vEtab", 6: "vEtab"}

    hr2 = TableRow()
    for h in _EXAM_ODS_HEADERS:
        hr2.addElement(_make_cell(doc, h, header_style))
    sheet_exam.addElement(hr2)

    _add_empty_rows_with_validation(doc, sheet_exam, _EXAM_ODS_HEADERS, exam_col_validations, 50)

    # ── Feuille preps (ajout au doc après candidats/examinateurs) ─────────────
    doc.spreadsheet.addElement(sheet_preps)
    doc.spreadsheet.addElement(sheet_lycees)

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
