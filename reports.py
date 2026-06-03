"""
Fonctions de génération PDF appelées depuis algo.py (hors contexte Flask).
Délègue aux fonctions de webserver/reports.py.
"""
from webserver.reports import (  # noqa: F401
    liste_papillons_connexion,
    liste_paillons_candidats,
    liste_paillons_loges,
)
