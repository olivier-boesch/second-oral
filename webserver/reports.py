"""
Génération des documents PDF pour les oraux de second groupe.

Utilise ReportLab (Platypus) pour les tableaux et canvas direct pour les papillons.
"""
import datetime
import tempfile
from io import BytesIO
from os.path import join as path_join
from pathlib import Path
from base64 import b64decode

from PIL import Image as PilImage, ImageDraw, ImageFont
import pypdftk
import segno
from flask import url_for
from reportlab.lib import colors
from reportlab.lib import pagesizes
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    LongTable, TableStyle, Paragraph, Spacer, Table, Image
)
from reportlab.platypus.doctemplate import SimpleDocTemplate

_FONT_DIR = Path(__file__).resolve().parent / 'static'

pdfmetrics.registerFont(TTFont('BodyFont', str(_FONT_DIR / 'PoppinsLatin-Regular.ttf')))
pdfmetrics.registerFont(TTFont('PapillonFont', str(_FONT_DIR / 'DejaVuSerif.ttf')))
# Monospace pour les mots de passe et identifiants : chaque caractère a la même
# largeur et les glyphes ambigus (0/O, 1/l/I) sont nettement distincts.
pdfmetrics.registerFont(TTFont('MonoFont', str(_FONT_DIR / 'DejaVuSansMono.ttf')))

WARNING_CHAR = "(!)"

# Couleurs — identiques à main.css
color1 = colors.Color(0xeb / 255, 0xe3 / 255, 0xf5 / 255)
color2 = colors.Color(0xfe / 255, 0xfe / 255, 0xff / 255)
color3 = colors.Color(0xd6 / 255, 0xcf / 255, 0xff / 255)
color4 = colors.Color(0xab / 255, 0xa0 / 255, 0xf9 / 255)
color5 = colors.Color(0x7c / 255, 0x80 / 255, 0xfc / 255)

# Styles de texte
normal_style = getSampleStyleSheet()['Normal']
normal_style.fontName = "BodyFont"
normal_style.alignment = 4

title_style = getSampleStyleSheet()['Title']
title_style.fontName = "BodyFont"

h1_style = getSampleStyleSheet()['h1']
h1_style.fontName = "BodyFont"

h4_style = getSampleStyleSheet()['h4']
h4_style.alignment = 2
h4_style.fontName = "BodyFont"

amenagement_style = getSampleStyleSheet()['Normal']
amenagement_style.fontName = "BodyFont"
amenagement_style.alignment = 1
amenagement_style.textColor = color5


class PageNumCanvas(canvas.Canvas):
    """
    Canvas ReportLab avec numérotation de pages (page X / Y).
    Source : http://code.activestate.com/recipes/546511-page-x-of-y-with-reportlab/
    """

    pagesize = pagesizes.landscape(pagesizes.A3)

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self.pages = []
        self.today = datetime.datetime.now().strftime("Édité le %d/%m/%Y à %H:%M")

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_page_number(page_count)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        page_info = f"{self.today} - page {self._pageNumber} / {page_count}"
        self.setFont("BodyFont", 8)
        self.drawRightString(70 * mm, 15 * mm, page_info)


def make_qr_image(data, directory, dpi=300):
    """Génère une image QR code dans un répertoire temporaire."""
    qr = segno.make_qr(data)
    qr_tempfile = tempfile.NamedTemporaryFile(
        dir=directory, suffix='.png', delete_on_close=False, delete=False
    )
    qr.save(qr_tempfile, scale=20, dpi=dpi)
    qr_tempfile.close()
    return qr_tempfile.name


def liste_pdf(title, headers, data, subtitle=None, cols=None, filename=None,
              centre_examen='', pagesize=pagesizes.landscape(pagesizes.A3),
              should_span=True,
              cell_backgrounds=(colors.white, colors.white, color1, color1),
              end=None):
    """Génère un PDF tabulaire générique avec entête, données et pied de page optionnel."""
    data.insert(0, headers)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=20 * mm,
        pagesize=pagesize,
        title=title,
    )
    table_width = pagesize[0] * 0.92
    story = [Paragraph(title, style=title_style)]

    if subtitle is not None:
        story.append(Paragraph(subtitle, style=title_style))

    if centre_examen:
        story.append(Paragraph(centre_examen, style=h4_style))

    one_col_width = table_width / len(data[0])
    if cols is not None:
        cols_w = [one_col_width * c for c in cols]
    else:
        cols_w = [one_col_width for _ in range(len(data[0]))]

    font_size = 16 if pagesize == pagesizes.landscape(pagesizes.A3) else 10
    table = LongTable(data, repeatRows=1, colWidths=cols_w,
                      minRowHeights=[0] + [40] * len(data))
    tab_style = TableStyle([
        ('FONT', (0, 0), (-1, -1), "BodyFont", font_size),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, 0), color3),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('GRID', (0, 0), (-1, -1), 2, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), cell_backgrounds),
    ])

    if should_span:
        for i in range(1, len(data) - 1, 2):
            tab_style.add('SPAN', (0, i), (0, i + 1))

    table.setStyle(tab_style)
    story.append(table)

    if end is not None:
        story.append(Spacer(1, 2 * mm))
        for element in end:
            story.append(element)

    page_canvas = PageNumCanvas
    page_canvas.pagesize = pagesize
    doc.build(story, canvasmaker=page_canvas)

    buffer.seek(0)
    with open(filename, "wb") as f:
        f.write(buffer.read())


def liste_generale_oraux(infos_oraux, filename=None, centre_examen=''):
    """PDF : liste générale des oraux par candidat."""
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
    )


def loge_oraux(infos_loge, tempdir=".", file_dir='.', filename_root='',
               centre_examen=''):
    """PDF : fiche d'une loge (salle de préparation)."""
    data = []
    filename = f"{filename_root}-{infos_loge['salle']}.pdf"
    for o in infos_loge['oraux']:
        nom = f"{o['candidat']} ({o['numero']})"
        if o['tiers_temps']:
            nom += " " + WARNING_CHAR
        line = [nom, o['salle'], o['matiere_court'], o['examinateur'],
                o['sujet'], o['oral'], "", ""]
        data.append(line)
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
        cell_backgrounds=(colors.white, color1),
        end=[
            Paragraph(WARNING_CHAR + " : candidat disposant d'un aménagement",
                      style=normal_style),
            Image(
                make_qr_image(
                    url_for('loge_court', id_loge=infos_loge['salle'], _external=True),
                    tempdir, dpi=500,
                ),
                useDPI=True,
            ),
            Paragraph(
                "adresse pour les mises à jour : " + url_for(
                    'loge_court', id_loge=infos_loge['salle'], _external=True
                ),
                style=normal_style,
            ),
        ],
    )
    return filename


def liste_loge_oraux(liste_loges, file_dir='.', filename_root='', centre_examen=''):
    """PDF : concaténation des fiches de toutes les loges."""
    liste_fichiers = []
    with tempfile.TemporaryDirectory() as tempdir:
        for loge in liste_loges:
            liste_fichiers.append(
                path_join(file_dir, loge_oraux(loge, tempdir, file_dir,
                                               filename_root, centre_examen))
            )
        pypdftk.concat(liste_fichiers, path_join(file_dir, "liste_loges.pdf"))
    return path_join(file_dir, "liste_loges.pdf")


def image_signature(img, horodatage):
    """Ajoute l'horodatage en surimpression sur une image de signature base64."""
    font = ImageFont.truetype(str(_FONT_DIR / 'PoppinsLatin-Regular.ttf'), 25)
    img_out = BytesIO()
    img_data = b64decode(img.split(",")[1])
    imagefile = BytesIO(img_data)
    imagefile.seek(0)
    image = PilImage.open(imagefile)
    imagedraw = ImageDraw.Draw(image)
    # 'fill' est le paramètre correct pour la couleur du texte en PIL
    imagedraw.text((0, 0), horodatage, font=font, fill=(0, 0, 0))
    image.save(img_out, format="PNG")
    return img_out


def salle_oraux(infos_examinateur, tempdir=".", file_dir='.', filename_root='',
                centre_examen=''):
    """PDF : fiche d'émargement d'une salle (examinateur)."""
    data = []
    safe_nom = infos_examinateur['nom'].replace(" ", "_")
    filename = f"{filename_root}-{infos_examinateur['salle']}-{safe_nom}.pdf"
    for o in infos_examinateur['oraux']:
        nom = f"{o['candidat']} ({o['numero']})"
        if o['tiers_temps']:
            nom += " " + WARNING_CHAR
        line = [nom, o['sujet'], o['oral']]
        image = o['emargement']
        if image == "":
            line.append("")
        else:
            buff = image_signature(image, o['heure_emargement'])
            buff.seek(0)
            line.append(Image(buff, 30 * mm, 30 * mm))
        data.append(line)
    liste_pdf(
        title=f"{infos_examinateur['salle']} - {infos_examinateur['nom']}",
        subtitle=f"{infos_examinateur['matiere']}",
        headers=["Candidat", "Sujet", "Oral", "Émargement"],
        data=data,
        cols=[2, 0.5, 0.5, 1],
        filename=path_join(file_dir, filename),
        centre_examen=centre_examen,
        pagesize=pagesizes.portrait(pagesizes.A4),
        should_span=False,
        cell_backgrounds=(colors.white, color1),
        end=[
            Paragraph(f"Loge : {infos_examinateur['loge']}", style=normal_style),
            Paragraph(WARNING_CHAR + " : candidat disposant d'un aménagement",
                      style=normal_style),
            Image(
                make_qr_image(
                    url_for('salle_court',
                            id_salle=infos_examinateur['salle'], _external=True),
                    tempdir, dpi=500,
                ),
                useDPI=True,
            ),
            Paragraph(
                "adresse pour les mises à jour : " + url_for(
                    'salle_court', id_salle=infos_examinateur['salle'], _external=True
                ),
                style=normal_style,
            ),
        ],
    )
    return filename


def liste_salle_oraux(liste_examinateurs, file_dir='.', filename_root='',
                      centre_examen=''):
    """PDF : concaténation des fiches de toutes les salles."""
    liste_fichiers = []
    with tempfile.TemporaryDirectory() as tempdir:
        for ex in liste_examinateurs:
            liste_fichiers.append(
                path_join(file_dir, salle_oraux(ex, tempdir, file_dir,
                                               filename_root, centre_examen))
            )
        pypdftk.concat(liste_fichiers, path_join(file_dir, "liste_salles.pdf"))
    return path_join(file_dir, "liste_salles.pdf")


def fiche_candidat(infos_candidat, tempdirname, file_dir='.', filename_root='',
                   centre_examen=''):
    """PDF : fiche individuelle d'un candidat avec ses horaires et identifiants de connexion."""
    buffer = BytesIO()
    canvas_a4 = PageNumCanvas
    canvas_a4.pagesize = pagesizes.portrait(pagesizes.A4)
    tab_style = TableStyle([
        ('FONT', (0, 0), (-1, -1), "BodyFont", 12),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, 0), color3),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('GRID', (0, 0), (-1, -1), 2, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, color1]),
    ])
    doc = SimpleDocTemplate(
        buffer,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=20 * mm,
        pagesize=pagesizes.portrait(pagesizes.A4),
        title=f"{infos_candidat['nom']} - {infos_candidat['numero']}",
    )
    story = [Paragraph("Oraux de second Groupe", style=title_style)]
    story.append(Paragraph(centre_examen, style=h4_style))
    story.append(Spacer(1, 30))
    story.append(
        Paragraph(f"{infos_candidat['nom']} (N° candidat : {infos_candidat['numero']})",
                  style=h1_style)
    )
    story.append(Spacer(1, 30))
    story.append(
        Paragraph(f"Établissement : {infos_candidat['etablissement']}",
                  style=normal_style)
    )
    story.append(Spacer(1, 30))
    if infos_candidat['tiers_temps']:
        story.append(
            Paragraph("~ Candidat disposant d'un aménagement d'épreuve ~",
                      style=amenagement_style)
        )
        story.append(Spacer(1, 30))

    # Tableau des oraux
    data = [["Matière", "Salle", "Heure"]]
    for o in infos_candidat['oraux']:
        data.append([o['matiere'], o['salle'], o['heure']])
    col_w = pagesizes.portrait(pagesizes.A4)[0] * 0.92 / 3.0
    story.append(Table(data=data, style=tab_style,
                       colWidths=[col_w, col_w, col_w]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Les choix inscrits sur cette feuille sont ceux que vous avez donnés en amont "
        "de cette épreuve. En cas de changement, il vous appartient de nous en faire "
        "part dès votre arrivée dans l'établissement.",
        style=normal_style,
    ))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Les informations présentes sur cette feuille sont susceptibles de changer au "
        "cours de la journée. Il vous est fortement recommandé de rester dans "
        "l'établissement et de vérifier périodiquement les affichages ou de consulter "
        "l'adresse ci-dessous.",
        style=normal_style,
    ))
    story.append(Spacer(1, 20))

    # QR code vers la fiche en ligne
    url_candidat = url_for('candidat_court', id_candidat=infos_candidat['numero'],
                           _external=True)
    story.append(Image(make_qr_image(url_candidat, tempdirname, dpi=500),
                       useDPI=True))
    story.append(
        Paragraph(f"Adresse pour consulter les mises à jour : {url_candidat}",
                  style=normal_style)
    )

    # Identifiants de connexion (si disponibles)
    login_key = infos_candidat.get('login_key', '')
    if login_key:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Identifiants de connexion :", style=normal_style))
        story.append(Paragraph(
            f'N° candidat : <font name="MonoFont"><b>{infos_candidat["numero"]}</b></font> — '
            f'Mot de passe : <font name="MonoFont"><b>{login_key}</b></font>',
            style=normal_style,
        ))

    doc.build(story, canvasmaker=canvas_a4)
    buffer.seek(0)
    safe_nom = infos_candidat['nom'].replace(" ", "_")
    filename = f"{filename_root}{safe_nom}.pdf"
    with open(path_join(file_dir, filename), "wb") as f:
        f.write(buffer.read())
    return filename


def liste_fiches_candidats(candidats, file_dir='.', filename_root='candidat_',
                           centre_examen=''):
    """PDF : concaténation des fiches de tous les candidats."""
    files = []
    with tempfile.TemporaryDirectory() as tempdirname:
        for c in candidats:
            files.append(
                path_join(file_dir,
                          fiche_candidat(c, tempdirname, file_dir, filename_root,
                                         centre_examen))
            )
        pypdftk.concat(files, path_join(file_dir, "liste_candidats.pdf"))
    return path_join(file_dir, "liste_candidats.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# Génération des papillons (slips de connexion imprimables)
# ──────────────────────────────────────────────────────────────────────────────

def _draw_papillon(c_canvas, x, y, slip_w, slip_h, title_line1, title_line2,
                  name, id_label, id_value, pwd_value, url='', qr_size=20 * mm):
    """Dessine un papillon (slip de connexion) à la position (x, y) sur le canvas."""
    pad = 3 * mm

    # Bande d'en-tête colorée — coins du haut arrondis pour suivre la bordure
    # (un roundRect arrondirait aussi les coins du bas, qui doivent rester
    # droits pour s'aligner avec le corps du papillon ; on les aplatit en
    # recouvrant la moitié inférieure de la bande d'un rectangle classique).
    band_radius = 2 * mm
    band_x = x + 1 * mm
    band_y = y + slip_h - 10 * mm - 1 * mm
    band_w = slip_w - 2 * mm
    band_h = 10 * mm
    c_canvas.setFillColorRGB(0.84, 0.81, 0.96)
    c_canvas.roundRect(band_x, band_y, band_w, band_h, band_radius, fill=1, stroke=0)
    c_canvas.rect(band_x, band_y, band_w, band_h / 2, fill=1, stroke=0)
    
    # Bordure
    c_canvas.setStrokeColorRGB(0.4, 0.35, 0.8)
    c_canvas.setLineWidth(1)
    c_canvas.roundRect(x + 1 * mm, y + 1 * mm,
                       slip_w - 2 * mm, slip_h - 2 * mm, 2 * mm)
    
    c_canvas.setFillColorRGB(0, 0, 0)

    # Titre
    c_canvas.setFont("PapillonFont", 9)
    c_canvas.drawCentredString(x + slip_w / 2, y + slip_h - 6 * mm, title_line1)
    if title_line2:
        c_canvas.setFont("PapillonFont", 8)
        c_canvas.drawCentredString(x + slip_w / 2, y + slip_h - 9.5 * mm, title_line2)

    # Nom
    c_canvas.setFont("PapillonFont", 12)
    c_canvas.drawString(x + pad, y + slip_h - 16 * mm, name[:35])

    # Identifiant — monospace pour distinguer les caractères ambigus
    c_canvas.setFont("PapillonFont", 10)
    c_canvas.drawString(x + pad, y + slip_h - 22 * mm, f"{id_label} : ")
    c_canvas.setFont("MonoFont", 10)
    label_w = c_canvas.stringWidth(f"{id_label} : ", "PapillonFont", 10)
    c_canvas.drawString(x + pad + label_w, y + slip_h - 22 * mm, id_value)

    # Mot de passe — monospace pour distinguer les caractères ambigus
    c_canvas.setFont("PapillonFont", 11)
    c_canvas.drawString(x + pad, y + slip_h - 27 * mm, "Mot de passe : ")
    c_canvas.setFont("MonoFont", 11)
    pwd_label_w = c_canvas.stringWidth("Mot de passe : ", "PapillonFont", 11)
    c_canvas.drawString(x + pad + pwd_label_w, y + slip_h - 27 * mm, pwd_value)

    # QR code + URL texte (coin inférieur droit)
    if url:
        try:
            qr_io = BytesIO()
            segno.make_qr(url).save(qr_io, kind='png', scale=4, dpi=150,
                                    dark='#1e1b4b', light='#ffffff')
            qr_io.seek(0)
            c_canvas.drawImage(
                ImageReader(qr_io),
                x + slip_w - qr_size - 2 * mm,
                y + 2 * mm,
                width=qr_size,
                height=qr_size,
            )
        except Exception:
            pass
        c_canvas.setFont("MonoFont", 6)
        c_canvas.drawString(x + pad, y + 3 * mm, url[:45])


def _build_papillons_pdf(items, filename, title1, title2, id_label,
                        get_id, get_name, get_pwd, get_url, qr_size=14 * mm):
    """
    Moteur générique de génération de papillons.

    :param items: liste des objets à imprimer
    :param filename: chemin de sortie PDF
    :param title1: première ligne du titre de chaque papillon
    :param title2: deuxième ligne du titre (souvent le nom du centre)
    :param id_label: libellé de l'identifiant (ex. 'Salle', 'N° candidat')
    :param get_id/get_name/get_pwd/get_url: callables item → valeur
    :param qr_size: taille du QR code sur chaque papillon
    """
    W, H = pagesizes.portrait(pagesizes.A4)
    buffer = BytesIO()
    c_canvas = canvas.Canvas(buffer, pagesize=pagesizes.portrait(pagesizes.A4))

    cols = 2
    rows_per_page = 5
    margin_x = 10 * mm
    margin_y = 10 * mm
    slip_w = (W - 2 * margin_x) / cols
    slip_h = (H - 2 * margin_y) / rows_per_page

    per_page = cols * rows_per_page
    for i, item in enumerate(items):
        page_pos = i % per_page
        if i > 0 and page_pos == 0:
            c_canvas.showPage()

        col = page_pos % cols
        row = page_pos // cols
        x = margin_x + col * slip_w
        y = H - margin_y - (row + 1) * slip_h

        _draw_papillon(
            c_canvas, x, y, slip_w, slip_h,
            title1, title2,
            get_name(item), id_label, get_id(item),
            get_pwd(item), get_url(item), qr_size=qr_size,
        )

    c_canvas.save()
    buffer.seek(0)
    with open(filename, 'wb') as f:
        f.write(buffer.read())


def liste_papillons_connexion(connexions, filename='papillons_examinateurs.pdf',
                               base_url='', centre_examen=''):
    """
    Génère les papillons de connexion pour les examinateurs.

    :param connexions: liste de tuples (salle, nom, mot_de_passe)
    :param filename: chemin du PDF de sortie
    :param base_url: URL de base du site (ex. 'https://stex.mesoraux.fr')
    :param centre_examen: nom du centre affiché sur chaque papillon
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


def liste_papillons_candidats(candidats, filename='static/docs/papillons_candidats.pdf',
                              base_url='', centre_examen=''):
    """
    Génère les papillons de connexion pour les candidats (élèves).

    :param candidats: liste de dicts {'nom', 'numero', 'login_key'}
    :param filename: chemin du PDF de sortie
    :param base_url: URL de base du site (ex. 'https://stex.mesoraux.fr')
    :param centre_examen: nom du centre affiché sur chaque papillon
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
        get_url=lambda d: f"{base_url}/c/{d['numero']}" if base_url else "",
        qr_size=18 * mm,
    )


def liste_papillons_loges(loges, filename='papillons_loges.pdf',
                          base_url='', centre_examen=''):
    """
    Génère les papillons de connexion pour les surveillants de loge.

    :param loges: liste de tuples (nom_loge, mot_de_passe)
    :param filename: chemin du PDF de sortie
    :param base_url: URL de base du site (ex. 'https://stex.mesoraux.fr')
    :param centre_examen: nom du centre affiché sur chaque papillon
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
