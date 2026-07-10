"""
Génération des documents PDF pour les oraux de second groupe.

Utilise ReportLab (Platypus + canvas direct) pour l'ensemble des documents.
La palette de couleurs est dérivée depuis ACCENT_COLOR (app_secrets.py) via theme.py,
ce qui permet d'adapter la charte graphique au moment du setup sans toucher au code.
"""
import datetime
import tempfile
from io import BytesIO
from os.path import join as path_join
from pathlib import Path
from base64 import b64decode

from PIL import Image as PilImage, ImageDraw, ImageFont
from pypdf import PdfWriter as _PdfWriter
import segno
from flask import url_for
from reportlab.lib import colors, pagesizes
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    LongTable, TableStyle, Paragraph, Spacer, Table, Image, HRFlowable
)
from reportlab.platypus.doctemplate import SimpleDocTemplate

# reports.py est importé depuis webserver/ (Flask) ET depuis la racine (algo.py).
# Dans les deux cas, webserver/ doit être sur sys.path pour trouver app_secrets et theme.
import sys as _sys
from pathlib import Path as _Path
_WEBSERVER_DIR = str(_Path(__file__).resolve().parent)
if _WEBSERVER_DIR not in _sys.path:
    _sys.path.insert(0, _WEBSERVER_DIR)

import pytz

import app_secrets as _app_secrets
from theme import derive_palette

ACCENT_COLOR: str = getattr(_app_secrets, 'ACCENT_COLOR', '#6c63ff')
# Horodatage des PDF (footer "Édité le ...") — datetime.now() naïf renvoie
# l'heure du serveur (UTC en conteneur Docker), pas l'heure de Paris.
TIMEZONE = getattr(_app_secrets, 'TIMEZONE', pytz.timezone('Europe/Paris'))

_FONT_DIR = Path(__file__).resolve().parent / 'static'
_ICON_PATH = str(_FONT_DIR / 'icon.png')

pdfmetrics.registerFont(TTFont('BodyFont',    str(_FONT_DIR / 'PoppinsLatin-Regular.ttf')))
pdfmetrics.registerFont(TTFont('PapillonFont', str(_FONT_DIR / 'DejaVuSerif.ttf')))
pdfmetrics.registerFont(TTFont('MonoFont',    str(_FONT_DIR / 'DejaVuSansMono.ttf')))

WARNING_CHAR = "(!)"

# ── Palette dérivée de la couleur d'accent du site ───────────────────────────
_pal = derive_palette(ACCENT_COLOR)

C_PRIMARY    = colors.HexColor(_pal['primary'])
C_PRI_DK     = colors.HexColor(_pal['primary_dk'])
C_PRI_LT     = colors.HexColor(_pal['primary_lt'])
C_SURFACE    = colors.HexColor(_pal['surface'])
C_SURFACE2   = colors.HexColor(_pal['surface_2'])
C_ROW_ALT    = colors.HexColor(_pal['row_alt'])
C_BORDER     = colors.HexColor(_pal['border'])
C_BORDER_DK  = colors.HexColor(_pal['border_dk'])
C_TEXT       = colors.HexColor(_pal['text'])
C_TEXT_MD    = colors.HexColor(_pal['text_md'])
C_TEXT_SM    = colors.HexColor(_pal['text_sm'])
C_WHITE      = colors.white
C_DANGER     = colors.HexColor('#c0392b')
C_WARN_BG    = colors.HexColor('#fff3f3')

# Couleurs RGB pour canvas direct (papillons)
_PR, _PG, _PB   = (int(_pal['primary'][i:i+2], 16)/255 for i in (1, 3, 5))
_PDR,_PDG,_PDB  = (int(_pal['primary_dk'][i:i+2], 16)/255 for i in (1, 3, 5))
_SMR,_SMG,_SMB  = (int(_pal['text_sm'][i:i+2], 16)/255 for i in (1, 3, 5))
_TXR,_TXG,_TXB  = (int(_pal['text'][i:i+2], 16)/255 for i in (1, 3, 5))
_SFR,_SFG,_SFB  = (int(_pal['surface'][i:i+2], 16)/255 for i in (1, 3, 5))


def _ps(name: str, **kw) -> ParagraphStyle:
    """Crée un style Paragraph avec les défauts de la palette."""
    kw.setdefault('fontName', 'BodyFont')
    kw.setdefault('textColor', C_TEXT)
    return ParagraphStyle(name, **kw)


def _cell(value, font_size: int = 10, alignment=TA_CENTER) -> Paragraph:
    """Enveloppe une valeur de cellule de tableau dans un Paragraph.

    ReportLab ne fait passer le texte à la ligne (word-wrap) que pour des
    flowables Paragraph — une cellule contenant une simple str déborde ou
    est tronquée si elle dépasse la largeur de colonne (colWidths est déjà
    fixé partout). Les cellules déjà construites (Paragraph, Image
    d'émargement...) passent inchangées.
    """
    if isinstance(value, (Paragraph, Image)):
        return value
    # ParagraphStyle.leading vaut 12pt par défaut, quelle que soit fontSize —
    # sur les fiches en A3 (font_size=16), ça produit un interligne plus
    # petit que la police elle-même et les lignes wrappées se chevauchent.
    return Paragraph(str(value), _ps('cell', fontSize=font_size,
                                      leading=font_size * 1.25, alignment=alignment))


# ── Canvas avec footer ────────────────────────────────────────────────────────

class PageNumCanvas(canvas.Canvas):
    """Canvas ReportLab avec footer : logo + '2ndOral' à gauche, date + page X/Y à droite."""

    pagesize = pagesizes.portrait(pagesizes.A4)

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self.pages: list = []
        self.today = datetime.datetime.now(TIMEZONE).strftime("Édité le %d/%m/%Y à %H:%M")

    def showPage(self):
        """Sauvegarde l'état de la page courante avant d'en démarrer une nouvelle."""
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        """Dessine le footer sur chaque page puis enregistre le PDF."""
        page_count = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self._draw_footer(page_count)
            super().showPage()
        super().save()

    def _draw_footer(self, page_count: int) -> None:
        """Dessine le footer en bas de page : logo + 2ndOral à gauche, date/page à droite."""
        icon_s = 5 * mm
        fy = 8 * mm
        lx = 15 * mm
        pw = self.pagesize[0]

        # Logo
        try:
            self.drawImage(_ICON_PATH, lx, fy, width=icon_s, height=icon_s,
                           preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

        # "2ndOral"
        self.setFont('BodyFont', 8)
        self.setFillColorRGB(_PR, _PG, _PB)
        self.drawString(lx + icon_s + 1.5 * mm, fy + 1 * mm, '2ndOral')

        # Date + numéro de page
        page_info = f"{self.today} - page {self._pageNumber} / {page_count}"
        self.setFillColorRGB(_SMR, _SMG, _SMB)
        self.drawRightString(pw - 15 * mm, fy + 1 * mm, page_info)


class PageNumCanvasA3L(PageNumCanvas):
    """Variante A3 paysage du canvas avec footer."""
    pagesize = pagesizes.landscape(pagesizes.A3)


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _concat_pdfs(input_files: list[str], output_file: str) -> None:
    """Concatène plusieurs fichiers PDF en un seul (remplace pypdftk.concat).

    Utilise pypdf (pure Python) au lieu de pdftk-java, évitant le démarrage
    d'une JVM et réduisant significativement l'image Docker.
    Sans effet si la liste d'entrée est vide.
    """
    if not input_files:
        return
    writer = _PdfWriter()
    for f in input_files:
        writer.append(f)
    with open(output_file, 'wb') as out:
        writer.write(out)


def make_qr_image(data: str, directory: str, dpi: int = 300) -> str:
    """Génère un QR code PNG dans un répertoire temporaire et retourne son chemin."""
    qr = segno.make_qr(data)
    qr_tempfile = tempfile.NamedTemporaryFile(
        dir=directory, suffix='.png', delete_on_close=False, delete=False
    )
    qr.save(qr_tempfile, scale=20, dpi=dpi, dark=_pal['text'], light='#ffffff')
    qr_tempfile.close()
    return qr_tempfile.name


def image_signature(img: str, horodatage: str) -> BytesIO:
    """Ajoute l'horodatage en surimpression sur une image de signature base64."""
    font = ImageFont.truetype(str(_FONT_DIR / 'PoppinsLatin-Regular.ttf'), 25)
    img_out = BytesIO()
    img_data = b64decode(img.split(",")[1])
    imagefile = BytesIO(img_data)
    imagefile.seek(0)
    image = PilImage.open(imagefile)
    imagedraw = ImageDraw.Draw(image)
    imagedraw.text((0, 0), horodatage, font=font, fill=(0, 0, 0))
    image.save(img_out, format="PNG")
    return img_out


def _header_band(W: float, title: str, right_text: str = '',
                 subtitle: str = '') -> list:
    """Retourne le bandeau d'en-tête (primaire + sous-bande) pour un document."""
    hdr = Table(
        [[Paragraph(title, _ps('ht', fontSize=18, textColor=C_WHITE, leading=22)),
          Paragraph(right_text,
                    _ps('hr', fontSize=9, textColor=colors.HexColor('#ccc9ff'),
                        alignment=TA_RIGHT))]],
        colWidths=[W * 0.6, W * 0.4],
    )
    hdr.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_PRIMARY),
        ('TOPPADDING',    (0, 0), (-1, -1), 5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5 * mm),
        ('LEFTPADDING',   (0, 0), (0, -1),  6 * mm),
        ('RIGHTPADDING',  (-1, 0), (-1, -1), 6 * mm),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('ROUNDEDCORNERS', [3 * mm, 3 * mm, 0, 0]),
    ]))
    items: list = [hdr]
    if subtitle:
        sub = Table(
            [[Paragraph(subtitle, _ps('sub', fontSize=8, textColor=C_TEXT_SM))]],
            colWidths=[W],
        )
        sub.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), C_SURFACE2),
            ('TOPPADDING',    (0, 0), (-1, -1), 2 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2 * mm),
            ('LEFTPADDING',   (0, 0), (-1, -1), 6 * mm),
            ('LINEBELOW',     (0, 0), (-1, -1), 0.7, C_BORDER_DK),
            ('ROUNDEDCORNERS', [0, 0, 3 * mm, 3 * mm]),
        ]))
        items.append(sub)
    return items


def _table_style_base(data_rows: int, span_col0: bool = False,
                      font_size: int = 10) -> TableStyle:
    """Retourne le TableStyle commun aux tableaux de données (hors papillons)."""
    style = [
        ('FONT',          (0, 0), (-1, -1), 'BodyFont', font_size),
        ('BACKGROUND',    (0, 0), (-1, 0),  C_SURFACE),
        ('LINEBELOW',     (0, 0), (-1, 0),  1.5, C_PRIMARY),
        ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4 * mm),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('LINEBELOW',     (0, 1), (-1, -1), 0.5, C_BORDER),
        ('BOX',           (0, 0), (-1, -1), 0.7, C_BORDER_DK),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]
    # Alternance lignes blanc / gris (visible en impression N&B)
    for i in range(1, data_rows + 1):
        bg = C_WHITE if i % 2 == 1 else C_ROW_ALT
        style.append(('BACKGROUND', (0, i), (-1, i), bg))
    # Fusion cellule candidat sur 2 lignes (liste générale)
    if span_col0:
        for i in range(1, data_rows, 2):
            style.append(('SPAN', (0, i), (0, i + 1)))
    return TableStyle(style)


# ── Fiche candidat ────────────────────────────────────────────────────────────

def fiche_candidat(infos_candidat: dict, tempdirname: str, file_dir: str = '.',
                   filename_root: str = '', centre_examen: str = '') -> str:
    """PDF : fiche individuelle d'un candidat avec ses horaires et identifiants de connexion."""
    buffer = BytesIO()
    canvas_cls = PageNumCanvas
    canvas_cls.pagesize = pagesizes.portrait(pagesizes.A4)
    W_page = pagesizes.A4[0]
    W = W_page - 30 * mm

    doc = SimpleDocTemplate(
        buffer,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm,  bottomMargin=22 * mm,
        pagesize=pagesizes.portrait(pagesizes.A4),
        title=f"{infos_candidat['nom']} - {infos_candidat['numero']}",
    )
    story = []

    # En-tête
    story += _header_band(W,
        title='Oraux de second Groupe',
        right_text=centre_examen,
        subtitle='Fiche individuelle de candidat',
    )
    story.append(Spacer(1, 6 * mm))

    # Identité
    story.append(Paragraph(
        infos_candidat['nom'],
        _ps('h1', fontSize=18, leading=22, spaceAfter=1 * mm),
    ))
    story.append(Paragraph(
        f"N° candidat : {infos_candidat['numero']}",
        _ps('numero', fontSize=10, textColor=C_TEXT_MD, spaceAfter=2 * mm),
    ))
    story.append(Paragraph(
        f"Établissement : {infos_candidat['etablissement']}",
        _ps('etab', fontSize=10, textColor=C_TEXT_MD, spaceAfter=4 * mm),
    ))

    # Aménagement — bordure gauche rouge, visible en N&B
    if infos_candidat['tiers_temps']:
        amen = Table(
            [[Paragraph(
                "~ Candidat disposant d'un aménagement d'épreuve ~",
                _ps('am', fontSize=10, textColor=C_DANGER))]],
            colWidths=[W],
        )
        amen.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), C_WARN_BG),
            ('TOPPADDING',    (0, 0), (-1, -1), 2.5 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
            ('LINEBEFORE',    (0, 0), (0, -1),  4, C_DANGER),
            ('BOX',           (0, 0), (-1, -1), 0.5, colors.HexColor('#e0a0a0')),
            ('ROUNDEDCORNERS', [2 * mm]),
        ]))
        story.append(amen)
        story.append(Spacer(1, 4 * mm))

    # Tableau des oraux (Matière / Salle / Heure)
    col_w = W / 3.0
    data = [[_cell(h) for h in ("Matière", "Salle", "Heure")]]
    for o in infos_candidat['oraux']:
        data.append([_cell(o['matiere']), _cell(o['salle']), _cell(o['heure'])])

    t = LongTable(data, colWidths=[col_w, col_w, col_w], repeatRows=1)
    t.setStyle(_table_style_base(len(data) - 1))
    story.append(t)
    story.append(Spacer(1, 4 * mm))

    # Instructions (texte verbatim de l'ancienne version)
    story.append(Paragraph(
        "Les choix inscrits sur cette feuille sont ceux que vous avez donnés en amont "
        "de cette épreuve. En cas de changement, il vous appartient de nous en faire "
        "part dès votre arrivée dans l'établissement.",
        _ps('body', fontSize=9, textColor=C_TEXT_MD, leading=13,
            alignment=TA_JUSTIFY, spaceAfter=2 * mm),
    ))

    # Instruction "rester dans l'établissement" — encadrée, bordure noire visible N&B
    instr = Table(
        [[Paragraph(
            "Les informations présentes sur cette feuille sont susceptibles de changer au "
            "cours de la journée. Il vous est fortement recommandé de rester dans "
            "l'établissement et de vérifier périodiquement les affichages ou de consulter "
            "l'adresse ci-dessous.",
            _ps('instr', fontSize=9, textColor=C_TEXT, leading=13,
                alignment=TA_JUSTIFY))]],
        colWidths=[W],
    )
    instr.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_SURFACE),
        ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4 * mm),
        ('LINEBEFORE',    (0, 0), (0, -1),  3, C_TEXT),
        ('BOX',           (0, 0), (-1, -1), 0.5, C_BORDER),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]))
    story.append(instr)
    story.append(Spacer(1, 5 * mm))

    # QR code — connexion automatique (token à usage unique, cf. app.py
    # _get_or_create_login_token) plutôt qu'un simple lien de navigation.
    url_candidat = url_for('login_candidat_qr',
                           token=infos_candidat['token'], _external=True)
    story.append(Image(make_qr_image(url_candidat, tempdirname, dpi=500),
                       width=36 * mm, height=36 * mm, useDPI=True))
    story.append(Paragraph(
        "Scannez pour vous connecter directement (usage unique).",
        _ps('url', fontSize=8, textColor=C_TEXT_MD, spaceBefore=2 * mm,
            spaceAfter=4 * mm),
    ))

    # Identifiants de connexion
    login_key = infos_candidat.get('login_key', '')
    if login_key:
        story.append(HRFlowable(width=W, thickness=0.7, color=C_BORDER_DK))
        story.append(Paragraph(
            "Identifiants de connexion :",
            _ps('id_title', fontSize=9, textColor=C_TEXT_MD,
                spaceBefore=3 * mm, spaceAfter=1 * mm),
        ))
        creds = Table(
            [[Paragraph(
                f'N° candidat : <font name="MonoFont"><b>{infos_candidat["numero"]}</b></font>'
                f'&nbsp;&nbsp;—&nbsp;&nbsp;'
                f'Mot de passe : <font name="MonoFont"><b>{login_key}</b></font>',
                _ps('creds', fontSize=11, textColor=C_TEXT)
            )]],
            colWidths=[W],
        )
        creds.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), C_SURFACE),
            ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
            ('LINEBEFORE',    (0, 0), (0, -1),  4, C_PRIMARY),
            ('BOX',           (0, 0), (-1, -1), 0.5, C_BORDER),
            ('ROUNDEDCORNERS', [2 * mm]),
        ]))
        story.append(creds)

    doc.build(story, canvasmaker=canvas_cls)
    buffer.seek(0)
    safe_nom = infos_candidat['nom'].replace(" ", "_")
    filename = f"{filename_root}{safe_nom}.pdf"
    with open(path_join(file_dir, filename), "wb") as f:
        f.write(buffer.read())
    return filename


def liste_fiches_candidats(candidats: list, file_dir: str = '.',
                            filename_root: str = 'candidat_',
                            centre_examen: str = '') -> str:
    """PDF : concaténation des fiches de tous les candidats."""
    files = []
    with tempfile.TemporaryDirectory() as tempdirname:
        for c in candidats:
            files.append(
                path_join(file_dir,
                          fiche_candidat(c, tempdirname, file_dir,
                                         filename_root, centre_examen))
            )
        _concat_pdfs(files, path_join(file_dir, "liste_candidats.pdf"))
    return path_join(file_dir, "liste_candidats.pdf")


# ── Fiche générique (salle + loge + liste générale) ──────────────────────────

def liste_pdf(title: str, headers: list, data: list, subtitle: str | None = None,
              cols: list | None = None, filename: str = '',
              centre_examen: str = '',
              pagesize=pagesizes.landscape(pagesizes.A3),
              should_span: bool = True,
              cell_backgrounds: tuple = (C_WHITE, C_ROW_ALT),
              end: list | None = None) -> None:
    """Génère un PDF tabulaire générique avec en-tête, données et pied de page optionnel."""
    data.insert(0, headers)
    buffer = BytesIO()
    W_page = pagesize[0]
    W = W_page - 30 * mm
    font_size = 16 if pagesize == pagesizes.landscape(pagesizes.A3) else 10
    data = [[_cell(v, font_size=font_size) for v in row] for row in data]

    doc = SimpleDocTemplate(
        buffer,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm,  bottomMargin=22 * mm,
        pagesize=pagesize,
        title=title,
    )
    story = []

    # En-tête
    story += _header_band(W,
        title=title,
        right_text=centre_examen,
        subtitle=subtitle or '',
    )
    story.append(Spacer(1, 5 * mm))

    # Tableau
    one_col_w = W / len(headers)
    col_widths = [one_col_w * c for c in cols] if cols else [one_col_w] * len(headers)

    table = LongTable(data, repeatRows=1, colWidths=col_widths,
                      minRowHeights=[0] + [40] * len(data))

    style = [
        ('FONT',          (0, 0), (-1, -1), 'BodyFont', font_size),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND',    (0, 0), (-1, 0),  C_SURFACE),
        ('LINEBELOW',     (0, 0), (-1, 0),  1.5, C_PRIMARY),
        ('LINEBELOW',     (0, 1), (-1, -1), 0.5, C_BORDER),
        ('BOX',           (0, 0), (-1, -1), 0.7, C_BORDER_DK),
        ('TOPPADDING',    (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4 * mm),
        ('ROUNDEDCORNERS', [2 * mm]),
    ]
    # Alternance ROWBACKGROUNDS (compatible N&B)
    style.append(('ROWBACKGROUNDS', (0, 1), (-1, -1), list(cell_backgrounds)))

    if should_span:
        for i in range(1, len(data) - 1, 2):
            style.append(('SPAN', (0, i), (0, i + 1)))

    table.setStyle(TableStyle(style))
    story.append(table)

    if end:
        story.append(Spacer(1, 3 * mm))
        for element in end:
            story.append(element)

    canvas_cls = PageNumCanvasA3L if pagesize == pagesizes.landscape(pagesizes.A3) \
        else PageNumCanvas
    canvas_cls.pagesize = pagesize
    doc.build(story, canvasmaker=canvas_cls)

    buffer.seek(0)
    with open(filename, "wb") as f:
        f.write(buffer.read())


def liste_generale_oraux(infos_oraux: list, filename: str = '',
                          centre_examen: str = '') -> None:
    """PDF : liste générale des oraux par candidat (A3 paysage)."""
    data = [
        [oral['candidat'], oral['matiere'], oral['heure'], oral['salle']]
        for oral in infos_oraux
    ]
    liste_pdf(
        title='Liste des oraux de second groupe par candidat',
        headers=["Candidat", "Matière", "Heure", "Salle"],
        data=data,
        cols=[1.7, 1.3, 0.5, 0.5],
        filename=filename,
        centre_examen=centre_examen,
        cell_backgrounds=(C_WHITE, C_WHITE, C_ROW_ALT, C_ROW_ALT),
    )


def loge_oraux(infos_loge: dict, tempdir: str = ".", file_dir: str = '.',
               filename_root: str = '', centre_examen: str = '') -> str:
    """PDF : fiche d'une loge (salle de préparation), A3 paysage."""
    data = []
    filename = f"{filename_root}-{infos_loge['salle']}.pdf"
    for o in infos_loge['oraux']:
        nom = o['candidat']
        if o['tiers_temps']:
            nom += " " + WARNING_CHAR
        nom += f"<br/>N° {o['numero']}"
        data.append([nom, o['salle'], o['matiere_court'], o['examinateur'],
                     o['sujet'], o['oral'], "", ""])
    liste_pdf(
        title=f"Loge {infos_loge['salle']}",
        headers=["Candidat", "Salle", "Matière", "Examinateur",
                 "Sujet", "Oral", "H. arrivée", "H. départ"],
        data=data,
        cols=[2.5, 0.5, 1, 1.5, 0.5, 0.5, 0.75, 0.75],
        filename=path_join(file_dir, filename),
        centre_examen=centre_examen,
        pagesize=pagesizes.landscape(pagesizes.A3),
        should_span=False,
        cell_backgrounds=(C_WHITE, C_ROW_ALT),
        end=[
            Paragraph(WARNING_CHAR + " : candidat disposant d'un aménagement",
                      _ps('end', fontSize=10, textColor=C_TEXT_MD,
                           spaceAfter=3 * mm)),
            Image(
                make_qr_image(
                    url_for('loge_court', id_loge=infos_loge['salle'], _external=True),
                    tempdir, dpi=500,
                ),
                width=36 * mm, height=36 * mm, useDPI=True,
            ),
            Paragraph(
                "adresse pour les mises à jour : " + url_for(
                    'loge_court', id_loge=infos_loge['salle'], _external=True
                ),
                _ps('url', fontSize=9, textColor=C_TEXT_MD),
            ),
        ],
    )
    return filename


def liste_loge_oraux(liste_loges: list, file_dir: str = '.', filename_root: str = '',
                     centre_examen: str = '') -> str:
    """PDF : concaténation des fiches de toutes les loges."""
    liste_fichiers = []
    with tempfile.TemporaryDirectory() as tempdir:
        for loge in liste_loges:
            liste_fichiers.append(
                path_join(file_dir, loge_oraux(loge, tempdir, file_dir,
                                               filename_root, centre_examen))
            )
        _concat_pdfs(liste_fichiers, path_join(file_dir, "liste_loges.pdf"))
    return path_join(file_dir, "liste_loges.pdf")


def salle_oraux(infos_examinateur: dict, tempdir: str = ".", file_dir: str = '.',
                filename_root: str = '', centre_examen: str = '') -> str:
    """PDF : fiche d'émargement d'une salle (examinateur), portrait A4."""
    data = []
    safe_nom = infos_examinateur['nom'].replace(" ", "_")
    filename = f"{filename_root}-{infos_examinateur['salle']}-{safe_nom}.pdf"
    for o in infos_examinateur['oraux']:
        nom = f"{o['candidat']} ({o['numero']})"
        if o['tiers_temps']:
            nom += " " + WARNING_CHAR
        line = [nom, o['sujet'], o['oral']]
        if o['emargement'] == "":
            line.append("")
        else:
            buff = image_signature(o['emargement'], o['heure_emargement'])
            buff.seek(0)
            line.append(Image(buff, 30 * mm, 30 * mm))
        data.append(line)
    liste_pdf(
        title=f"{infos_examinateur['salle']} - {infos_examinateur['nom']}",
        subtitle=infos_examinateur['matiere'],
        headers=["Candidat", "Sujet", "Oral", "Émargement"],
        data=data,
        cols=[2, 0.5, 0.5, 1],
        filename=path_join(file_dir, filename),
        centre_examen=centre_examen,
        pagesize=pagesizes.portrait(pagesizes.A4),
        should_span=False,
        cell_backgrounds=(C_WHITE, C_ROW_ALT),
        end=[
            Paragraph(f"Loge : {infos_examinateur['loge']}",
                      _ps('end', fontSize=9, textColor=C_TEXT_MD,
                           spaceAfter=1 * mm)),
            Paragraph(WARNING_CHAR + " : candidat disposant d'un aménagement",
                      _ps('end2', fontSize=9, textColor=C_TEXT_MD,
                           spaceAfter=3 * mm)),
            Image(
                make_qr_image(
                    url_for('salle_court',
                            id_salle=infos_examinateur['salle'], _external=True),
                    tempdir, dpi=500,
                ),
                width=36 * mm, height=36 * mm, useDPI=True,
            ),
            Paragraph(
                "adresse pour les mises à jour : " + url_for(
                    'salle_court', id_salle=infos_examinateur['salle'], _external=True
                ),
                _ps('url', fontSize=9, textColor=C_TEXT_MD),
            ),
        ],
    )
    return filename


def liste_salle_oraux(liste_examinateurs: list, file_dir: str = '.',
                      filename_root: str = '', centre_examen: str = '') -> str:
    """PDF : concaténation des fiches de toutes les salles."""
    liste_fichiers = []
    with tempfile.TemporaryDirectory() as tempdir:
        for ex in liste_examinateurs:
            liste_fichiers.append(
                path_join(file_dir, salle_oraux(ex, tempdir, file_dir,
                                               filename_root, centre_examen))
            )
        _concat_pdfs(liste_fichiers, path_join(file_dir, "liste_salles.pdf"))
    return path_join(file_dir, "liste_salles.pdf")


# ── Papillons de connexion ────────────────────────────────────────────────────

def _draw_papillon(c_canvas, x: float, y: float, slip_w: float, slip_h: float,
                   title_line1: str, title_line2: str, name: str,
                   id_label: str, id_value: str, pwd_value: str,
                   url: str = '', qr_size: float = 35 * mm) -> None:
    """Dessine un papillon (slip de connexion) à la position (x, y) sur le canvas.

    Layout : en-tête 2 lignes (rôle + centre) ; colonne gauche pour le texte ;
    colonne droite pour le QR. Toutes les zones sont calculées pour ne pas se
    chevaucher quelle que soit la taille du QR demandée.
    """
    sw = slip_w - 4 * mm   # largeur utile du slip (~91 mm pour 2 cols A4)
    sh = slip_h - 4 * mm   # hauteur utile (~51 mm pour 5 lignes A4)
    pad = 3 * mm

    # ── Fond blanc + contour ──────────────────────────────────────────────────
    c_canvas.setFillColorRGB(1, 1, 1)
    c_canvas.setStrokeColorRGB(0.55, 0.55, 0.55)
    c_canvas.setLineWidth(0.6)
    c_canvas.roundRect(x, y, sw, sh, 3 * mm, fill=1, stroke=1)

    # ── En-tête 2 lignes (rôle + centre) ─────────────────────────────────────
    # 2 lignes pour éviter le débordement quand titre + centre sont tous deux longs.
    hh = 11 * mm if title_line2 else 7 * mm
    c_canvas.setFillColorRGB(_PR, _PG, _PB)
    c_canvas.roundRect(x, y + sh - hh, sw, hh, 3 * mm, fill=1, stroke=0)
    c_canvas.rect(x, y + sh - hh, sw, hh / 2, fill=1, stroke=0)

    c_canvas.setFillColorRGB(1, 1, 1)
    c_canvas.setFont('PapillonFont', 7)
    c_canvas.drawString(x + pad, y + sh - 5.5 * mm, title_line1)

    if title_line2:
        c_canvas.setFont('BodyFont', 6)
        c_canvas.setFillColorRGB(0.82, 0.80, 1.0)
        c_canvas.drawString(x + pad, y + sh - 9.5 * mm, title_line2)

    # ── Colonnes : texte (gauche) | QR (droite) ───────────────────────────────
    # La colonne texte ne s'étend jamais sur la colonne QR.
    text_w = sw - qr_size - pad * 2   # largeur colonne texte
    content_top = y + sh - hh          # haut de la zone contenu (sous en-tête)
    content_h   = sh - hh              # hauteur disponible pour le contenu

    # QR : aligné à droite, centré verticalement dans la zone contenu
    qr_x = x + sw - qr_size - pad
    qr_size_actual = min(qr_size, content_h - 2 * pad)   # jamais plus grand que la zone
    qr_y = y + (content_h - qr_size_actual) / 2           # centrage vertical
    if url:
        try:
            qr_io = BytesIO()
            segno.make_qr(url).save(qr_io, kind='png', scale=6, dpi=300,
                                    dark=_pal['text'], light='#ffffff')
            qr_io.seek(0)
            c_canvas.drawImage(ImageReader(qr_io), qr_x, qr_y,
                               width=qr_size_actual, height=qr_size_actual)
        except Exception:
            pass

    # ── Texte (colonne gauche) — positions calculées de haut en bas ──────────
    # Toutes les distances sont relatives à content_top (y du haut du contenu).
    tx    = x + pad
    pwd_h = 7 * mm
    pwd_w = text_w - pad

    # Nom
    c_canvas.setFont('PapillonFont', 10)
    c_canvas.setFillColorRGB(_TXR, _TXG, _TXB)
    c_canvas.drawString(tx, content_top - 6.5 * mm, name[:26])

    # Identifiant — label
    c_canvas.setFont('BodyFont', 7)
    c_canvas.setFillColorRGB(_SMR, _SMG, _SMB)
    c_canvas.drawString(tx, content_top - 12 * mm, id_label)

    # Identifiant — valeur
    c_canvas.setFont('MonoFont', 8.5)
    c_canvas.setFillColorRGB(_TXR, _TXG, _TXB)
    c_canvas.drawString(tx, content_top - 17 * mm, id_value[:20])

    # Séparateur (sous la valeur identifiant)
    sep_y = content_top - 21.5 * mm
    c_canvas.setStrokeColorRGB(0.75, 0.75, 0.75)
    c_canvas.setLineWidth(0.4)
    c_canvas.line(tx, sep_y, tx + text_w, sep_y)

    # Label "Mot de passe"
    lbl_y = sep_y - 4.5 * mm
    c_canvas.setFont('BodyFont', 7)
    c_canvas.setFillColorRGB(_SMR, _SMG, _SMB)
    c_canvas.drawString(tx, lbl_y, 'Mot de passe')

    # Encadré mot de passe (sous le label, avec au moins 4mm du bord inférieur)
    pwd_y = max(y + 4 * mm, lbl_y - 1.5 * mm - pwd_h)
    c_canvas.setFillColorRGB(_SFR, _SFG, _SFB)
    c_canvas.setStrokeColorRGB(_PDR, _PDG, _PDB)
    c_canvas.setLineWidth(0.8)
    c_canvas.roundRect(tx, pwd_y, pwd_w, pwd_h, 1.5 * mm, fill=1, stroke=1)
    c_canvas.setFillColorRGB(_PR * 0.8, _PG * 0.8, _PB * 0.85)
    c_canvas.setFont('MonoFont', 10)
    c_canvas.drawCentredString(tx + pwd_w / 2, pwd_y + 2 * mm, pwd_value)


def _build_papillons_pdf(items: list, filename: str, title1: str, title2: str,
                         id_label: str, get_id, get_name, get_pwd, get_url,
                         qr_size: float = 35 * mm) -> None:
    """Moteur générique de génération de papillons (2 colonnes × 5 lignes par page A4).

    :param items:    Liste des objets à imprimer.
    :param filename: Chemin de sortie PDF.
    :param title1:   Première ligne du titre (rôle).
    :param title2:   Deuxième ligne du titre (centre).
    :param id_label: Libellé de l'identifiant (ex. 'Salle', 'N° candidat').
    :param get_id/get_name/get_pwd/get_url: Callables item → valeur de chaque champ.
    :param qr_size:  Taille du QR code sur chaque papillon.
    """
    W, H = pagesizes.portrait(pagesizes.A4)
    buffer = BytesIO()
    c_canvas = canvas.Canvas(buffer, pagesize=pagesizes.portrait(pagesizes.A4))

    cols = 2
    rows_per_page = 5
    margin_x      = 10 * mm
    margin_top    = 10 * mm
    margin_bottom = 18 * mm   # espace suffisant pour le footer (logo 5mm + texte)
    slip_w = (W - 2 * margin_x) / cols
    slip_h = (H - margin_top - margin_bottom) / rows_per_page

    per_page = cols * rows_per_page
    total_pages = max(1, -(-len(items) // per_page))   # arrondi supérieur

    for i, item in enumerate(items):
        page_pos = i % per_page
        if i > 0 and page_pos == 0:
            # Footer avant de tourner la page
            _draw_papillon_footer(c_canvas, W, H, i // per_page, total_pages)
            c_canvas.showPage()

        col = page_pos % cols
        row = page_pos // cols
        x = margin_x + col * slip_w
        y = H - margin_top - (row + 1) * slip_h

        _draw_papillon(
            c_canvas, x, y, slip_w, slip_h,
            title1, title2,
            get_name(item), id_label, get_id(item),
            get_pwd(item), get_url(item), qr_size=qr_size,
        )

    # Footer de la dernière page
    _draw_papillon_footer(c_canvas, W, H, total_pages, total_pages)
    c_canvas.save()
    buffer.seek(0)
    with open(filename, 'wb') as f:
        f.write(buffer.read())


def _draw_papillon_footer(c_canvas, W: float, H: float,
                          page_num: int, total_pages: int) -> None:
    """Dessine le footer standard (logo + 2ndOral + date/page) sur la page courante."""
    icon_s = 5 * mm
    fy = 8 * mm
    lx = 15 * mm
    today = datetime.datetime.now(TIMEZONE).strftime("Édité le %d/%m/%Y à %H:%M")

    try:
        c_canvas.drawImage(_ICON_PATH, lx, fy, width=icon_s, height=icon_s,
                           preserveAspectRatio=True, mask='auto')
    except Exception:
        pass

    c_canvas.setFont('BodyFont', 8)
    c_canvas.setFillColorRGB(_PR, _PG, _PB)
    c_canvas.drawString(lx + icon_s + 1.5 * mm, fy + 1 * mm, '2ndOral')

    c_canvas.setFillColorRGB(_SMR, _SMG, _SMB)
    c_canvas.drawRightString(
        W - 15 * mm, fy + 1 * mm,
        f"{today} - page {page_num} / {total_pages}",
    )


def liste_papillons_connexion(connexions: list,
                               filename: str = 'papillons_examinateurs.pdf',
                               base_url: str = '', centre_examen: str = '') -> None:
    """Génère les papillons de connexion pour les examinateurs.

    :param connexions:    Liste de tuples (salle, nom, mot_de_passe).
    :param filename:      Chemin du PDF de sortie.
    :param base_url:      URL de base du site (ex. 'https://stex.mesoraux.fr').
    :param centre_examen: Nom du centre affiché sur chaque papillon.
    """
    _build_papillons_pdf(
        items=connexions,
        filename=filename,
        title1="Oraux de second groupe — Examinateur",
        title2=centre_examen,
        id_label="Salle",
        get_id=lambda t: t[0],
        get_name=lambda t: t[1],
        get_pwd=lambda t: t[2],
        get_url=lambda t: f"{base_url}/s/{t[0]}" if base_url else "",
    )


def liste_papillons_candidats(candidats: list,
                               filename: str = 'generated/papillons_candidats.pdf',
                               base_url: str = '', centre_examen: str = '') -> None:
    """Génère les papillons de connexion pour les candidats.

    :param candidats:     Liste de dicts {'nom', 'numero', 'login_key', 'token'}.
        Le QR encode une connexion automatique via 'token' (cf. app.py
        _get_or_create_login_token / route login_candidat_qr), pas un simple
        lien de navigation.
    :param filename:      Chemin du PDF de sortie.
    :param base_url:      URL de base du site.
    :param centre_examen: Nom du centre affiché sur chaque papillon.
    """
    _build_papillons_pdf(
        items=candidats,
        filename=filename,
        title1="Oraux de second groupe — Candidat",
        title2=centre_examen,
        id_label="N° candidat",
        get_id=lambda d: d['numero'],
        get_name=lambda d: d['nom'],
        get_pwd=lambda d: d['login_key'],
        get_url=lambda d: f"{base_url}/login-candidat/qr/{d['token']}" if base_url else "",
    )


def liste_papillons_loges(loges: list, filename: str = 'papillons_loges.pdf',
                           base_url: str = '', centre_examen: str = '') -> None:
    """Génère les papillons de connexion pour les surveillants de loge.

    :param loges:         Liste de tuples (nom_loge, mot_de_passe).
    :param filename:      Chemin du PDF de sortie.
    :param base_url:      URL de base du site.
    :param centre_examen: Nom du centre affiché sur chaque papillon.
    """
    _build_papillons_pdf(
        items=loges,
        filename=filename,
        title1="Oraux de second groupe — Surveillant de Loge",
        title2=centre_examen,
        id_label="Loge",
        get_id=lambda t: t[0],
        get_name=lambda t: f"Loge {t[0]}",
        get_pwd=lambda t: t[1],
        get_url=lambda t: f"{base_url}/l/{t[0]}" if base_url else "",
    )
