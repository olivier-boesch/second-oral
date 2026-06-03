"""
Fonctions de génération PDF appelées depuis algo.py (hors contexte Flask).
Délègue aux fonctions de webserver/reports.py.
"""
from webserver.reports import (  # noqa: F401
    liste_papillons_connexion,
    liste_papillons_candidats,
    liste_papillons_loges,
)
