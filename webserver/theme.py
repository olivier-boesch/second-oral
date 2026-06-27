"""
Dérivation de la palette de couleurs depuis la couleur d'accent du site.

Utilisé par :
- app.py : route /theme.css (variables CSS)
- reports.py : génération des PDFs
- setup_new_site.py : PDF administrateur

Usage :
    from webserver.theme import derive_palette
    palette = derive_palette('#6c63ff')
    primary = palette['primary']       # ex. '#6c63ff'
    surface = palette['surface']       # ex. '#f5f5f5'
"""
import colorsys


def derive_palette(hex_color: str) -> dict[str, str]:
    """Dérive une palette CSS complète depuis une couleur d'accent hexadécimale.

    Toutes les couleurs partagent le même teinte (H) que la couleur d'entrée.
    La saturation et la luminosité varient selon le rôle sémantique de chaque couleur.
    Les valeurs de gris (surface, row_alt) sont intentionnellement désaturées
    pour garantir un bon contraste en impression niveau de gris.

    :param hex_color: Couleur d'accent au format '#rrggbb'.
    :returns: Dict de 11 clés → valeurs hex '#rrggbb'.
    """
    h_str = hex_color.lstrip('#')
    r, g, b = (int(h_str[i:i+2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    def to_hex(h: float, l: float, s: float) -> str:
        rr, gg, bb = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)),
                                          max(0.0, min(1.0, s)))
        return '#{:02x}{:02x}{:02x}'.format(int(rr * 255), int(gg * 255), int(bb * 255))

    return {
        # Accent principal et variantes
        'primary':    f'#{h_str.lower()}',
        'primary_dk': to_hex(h, l - 0.12, s),
        'primary_mid': to_hex(h, l + 0.04, s * 0.85),
        'primary_lt': to_hex(h, 0.97,     s * 0.15),

        # Fonds (légèrement teintés mais quasi-neutres pour le N&B)
        'surface':    to_hex(h, 0.961, s * 0.08),   # gris très clair teinté
        'surface_2':  to_hex(h, 0.929, s * 0.18),   # sous-en-tête
        'row_alt':    to_hex(h, 0.941, 0.0),         # alternance tableau — neutre gris

        # Bordures
        'border':     to_hex(h, 0.78,  s * 0.45),
        'border_dk':  to_hex(h, 0.64,  s * 0.50),

        # Textes
        'text':       to_hex(h, 0.20,  s * 0.50),
        'text_md':    to_hex(h, 0.38,  s * 0.30),
        'text_sm':    to_hex(h, 0.55,  s * 0.22),
    }
