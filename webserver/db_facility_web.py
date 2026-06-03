"""
Requêtes SQL et interface base de données pour le serveur web.

Toutes les requêtes utilisent des requêtes paramétrées pour prévenir les injections SQL.
"""
import os

import mysql.connector

# Initialise la variable de session @salt pour les triggers MariaDB
INIT_DB = "SET @salt = %(salt)s"

# ---------- Candidat

SELECT_INFOS_CANDIDAT = """
SELECT Candidat.id AS id, Candidat.nom AS nom, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Candidat.login_key AS login_key
FROM Candidat
WHERE Candidat.numero = %s
"""

SELECT_ORAUX_CANDIDAT = """
SELECT Matiere.nom AS matiere, Oral.heure_sujet AS heure,
       Examinateur.salle AS salle, Oral.mis_a_jour AS maj
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Candidat.numero = %s
ORDER BY Oral.heure_sujet
"""

SELECT_CANDIDAT_AUTH = """
SELECT Candidat.id AS id, Candidat.nom AS nom,
       Candidat.numero AS ine, Candidat.password_hash AS password_hash
FROM Candidat
WHERE Candidat.numero = %s
"""

SELECT_ALL_CANDIDATS_PAPILLONS = """
SELECT Candidat.nom AS nom, Candidat.numero AS ine, Candidat.login_key AS login_key
FROM Candidat
ORDER BY Candidat.nom
"""

# ---------- Salle / Examinateur

SELECT_LISTE_SALLES = """
SELECT Examinateur.salle AS salle, Examinateur.nom AS nom, Matiere.nom AS matiere
FROM Examinateur
    JOIN Matiere ON Examinateur.matiere = Matiere.id
ORDER BY Examinateur.salle
"""

SELECT_INFOS_SALLE = """
SELECT Examinateur.id AS id, Examinateur.nom AS nom, Examinateur.salle AS salle,
       Examinateur.loge AS loge, Matiere.nom AS matiere
FROM Examinateur
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Examinateur.salle = %s
"""

SELECT_ORAUX_SALLE = """
SELECT Oral.id AS id, Candidat.nom AS candidat, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Oral.heure_sujet AS sujet,
       Oral.heure_oral AS oral, Oral.mis_a_jour AS maj, Oral.emargement AS emargement,
       Oral.heure_emargement AS heure_emargement
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
WHERE Examinateur.id = %s
ORDER BY Oral.heure_sujet
"""

SELECT_PASSWORD_CHECK_SALLE = """
SELECT nom, password_hash FROM Examinateur WHERE salle = %s
"""

# ---------- Liste générale

SELECT_LISTE_ORAUX = """
SELECT Oral.id AS id_oral, Candidat.id AS id_candidat, Candidat.nom AS nom,
       Candidat.numero AS ine, Candidat.tiers_temps AS tiers_temps,
       Matiere.nom AS matiere, Oral.heure_sujet AS heure,
       Examinateur.salle AS salle, Oral.mis_a_jour AS maj
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
ORDER BY nom, heure
"""

# ---------- Loge

SELECT_LISTE_LOGES = """
SELECT DISTINCT Examinateur.loge AS salle
FROM Examinateur
ORDER BY Examinateur.loge
"""

SELECT_INFOS_LOGE = """
SELECT DISTINCT Examinateur.loge AS salle
FROM Examinateur
WHERE Examinateur.loge = %s
"""

SELECT_ORAUX_LOGE = """
SELECT Examinateur.loge AS loge, Candidat.nom AS candidat, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Examinateur.salle AS salle,
       Oral.heure_sujet AS sujet, Oral.mis_a_jour AS maj, Oral.heure_oral AS oral,
       Matiere.nom AS matiere, Matiere.nom_court AS matiere_court,
       Examinateur.nom AS examinateur
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Examinateur.loge = %s
ORDER BY sujet, candidat
"""

# ---------- Matières / Examinateurs

SELECT_LISTE_MATIERES = """
SELECT Matiere.id AS id, Matiere.nom AS nom FROM Matiere
"""

SELECT_LISTE_EXAMINATEURS_PAR_MATIERE = """
SELECT Examinateur.id AS id, Examinateur.nom AS nom,
       Examinateur.etablissements AS etablissements, Examinateur.salle AS salle
FROM Examinateur
WHERE Examinateur.matiere = %s
"""

SELECT_INFOS_ORAL = """
SELECT Oral.id AS id, Candidat.nom AS nom, Candidat.numero AS ine,
       Candidat.etablissement AS etablissement, Candidat.tiers_temps AS tiers_temps,
       Candidat.id AS id_candidat, Examinateur.id AS id_examinateur,
       Examinateur.matiere AS id_matiere, Oral.heure_sujet AS heure_sujet,
       Oral.heure_oral AS heure_oral, Matiere.nom AS matiere
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Oral.id = %s
"""

SELECT_LISTE_EDITION_ORAL = """
SELECT Oral.id AS id, Matiere.nom AS matiere, Examinateur.nom AS examinateur,
       Examinateur.salle AS salle, Oral.heure_sujet AS heure_sujet,
       Oral.heure_oral AS heure_oral, Oral.heure_fin AS heure_fin
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Candidat.id = %s
ORDER BY heure_sujet
"""

UPDATE_INFOS_ORAL = """
UPDATE Oral SET
    Oral.heure_sujet = %(heure_sujet)s,
    Oral.heure_oral = %(heure_oral)s,
    Oral.examinateur = %(examinateur)s,
    Oral.mis_a_jour = %(mis_a_jour)s
WHERE id = %(id)s
"""

# ---------- Tokens signature

INSERT_TOKEN_SIGNATURE = """
INSERT INTO TokenSignature (token, time_limit, oral)
VALUES (%(token)s, %(time_limit)s, %(oral)s)
"""

SELECT_TOKEN_SIGNATURE = """
SELECT * FROM TokenSignature WHERE token = %s
"""

SELECT_TOKEN_SIGNATURE_ALL = """
SELECT * FROM TokenSignature
"""

DELETE_TOKEN_SIGNATURE = """
DELETE FROM TokenSignature WHERE token = %(token)s
"""

DELETE_SALLE_TOKEN_SIGNATURE = """
DELETE FROM TokenSignature
WHERE oral IN (
    SELECT Oral.id FROM Oral
        JOIN Examinateur ON Examinateur.id = Oral.examinateur
    WHERE Examinateur.salle = %(id_salle)s
)
"""

UPDATE_SIGNATURE_ORAL = """
UPDATE Oral SET
    emargement = %(signature)s,
    heure_emargement = %(heure_signature)s
WHERE id = %(id)s
"""

SELECT_SIGNATURE_ORAL = """
SELECT Oral.id AS id_oral, Candidat.nom AS nom, Candidat.numero AS ine,
       Examinateur.salle AS salle, Examinateur.nom AS examinateur,
       Oral.emargement AS emargement, Oral.heure_emargement AS heure_emargement,
       Matiere.nom AS matiere
FROM Oral
    JOIN Examinateur ON Examinateur.id = Oral.examinateur
    JOIN Candidat ON Candidat.id = Oral.candidat
    JOIN Matiere ON Matiere.id = Examinateur.matiere
WHERE Oral.id = %s
"""

# ---------- Examinateurs CRUD

SELECT_LISTE_EXAMINATEURS = """
SELECT Examinateur.id AS id, Examinateur.nom AS nom,
       Examinateur.etablissements AS etablissements,
       Examinateur.salle AS salle, Examinateur.loge AS loge,
       Matiere.nom AS matiere, COUNT(Oral.id) AS nb_oraux
FROM Examinateur
    JOIN Matiere ON Matiere.id = Examinateur.matiere
    LEFT OUTER JOIN Oral ON Oral.examinateur = Examinateur.id
GROUP BY Examinateur.nom
ORDER BY Examinateur.nom
"""

SELECT_EXAMINATEUR_INFOS = """
SELECT Examinateur.id AS id, Examinateur.nom AS nom,
       Examinateur.etablissements AS etablissements,
       Examinateur.salle AS salle, Examinateur.loge AS loge,
       Matiere.nom AS matiere, COUNT(*) AS nb_oraux
FROM Examinateur
    LEFT OUTER JOIN Oral ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Examinateur.id = %s
GROUP BY Examinateur.nom
"""

SELECT_ORAUX_EXAMINATEUR = """
SELECT Candidat.nom AS candidat, Candidat.numero AS ine,
       Candidat.etablissement AS etablissement, Candidat.tiers_temps AS tiers_temps,
       Oral.heure_sujet AS heure, Oral.mis_a_jour AS maj, Oral.id AS id
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
WHERE Examinateur.id = %s
ORDER BY Oral.heure_sujet
"""

DELETE_EXAMINATEUR = """
DELETE FROM Examinateur WHERE Examinateur.id = %(id)s
"""

UPDATE_EXAMINATEUR_INFOS = """
UPDATE Examinateur SET
    Examinateur.nom = %(nom)s,
    Examinateur.etablissements = %(etablissements)s,
    Examinateur.salle = %(salle)s,
    Examinateur.loge = %(loge)s
WHERE Examinateur.id = %(id)s
"""

INSERT_EXAMINATEUR = """
INSERT INTO Examinateur (nom, etablissements, matiere, salle, loge)
VALUES (%(nom)s, %(etablissements)s, %(matiere)s, %(salle)s, %(loge)s)
"""

# ---------- Documents PDF

SELECT_DOC_LISTE_ORAUX = """
SELECT Candidat.nom AS candidat, Matiere.nom AS matiere,
       Oral.heure_sujet AS heure, Examinateur.salle AS salle
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
ORDER BY Candidat.nom, Oral.heure_sujet
"""

SELECT_DOC_LISTE_SALLES = """
SELECT Examinateur.id AS id, Examinateur.nom AS nom, Examinateur.salle AS salle,
       Examinateur.loge AS loge, Matiere.nom AS matiere
FROM Examinateur
    JOIN Matiere ON Matiere.id = Examinateur.matiere
ORDER BY Examinateur.salle
"""

SELECT_DOC_LISTE_SALLES_ORAUX = """
SELECT Candidat.nom AS candidat, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Oral.heure_sujet AS sujet,
       Oral.heure_oral AS oral, Oral.emargement AS emargement,
       Oral.heure_emargement AS heure_emargement
FROM Oral
    JOIN Candidat ON Oral.candidat = Candidat.id
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
WHERE Examinateur.id = %s
ORDER BY Oral.heure_sujet
"""

SELECT_DOC_LISTE_CANDIDATS = """
SELECT Candidat.id AS id, Candidat.nom AS nom, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Candidat.etablissement,
       Candidat.login_key AS login_key
FROM Candidat
ORDER BY Candidat.nom
"""

SELECT_DOC_LISTE_CANDIDATS_ORAUX = """
SELECT Matiere.nom AS matiere, Examinateur.salle AS salle, Oral.heure_sujet AS heure
FROM Oral
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Oral.candidat = %s
ORDER BY Oral.heure_sujet
"""

SELECT_DOC_INFOS_CANDIDAT = """
SELECT Candidat.id AS id, Candidat.nom AS nom, Candidat.numero AS ine,
       Candidat.tiers_temps AS tiers_temps, Candidat.etablissement,
       Candidat.login_key AS login_key
FROM Candidat
WHERE Candidat.id = %s
"""

SELECT_DOC_INFOS_CANDIDATS_ORAUX = """
SELECT Matiere.nom AS matiere, Examinateur.salle AS salle, Oral.heure_sujet AS heure
FROM Oral
    JOIN Examinateur ON Oral.examinateur = Examinateur.id
    JOIN Matiere ON Examinateur.matiere = Matiere.id
WHERE Oral.candidat = %s
ORDER BY Oral.heure_sujet
"""

# ---------- Logs

# ---------- SSE — résolution des canaux

SELECT_SALLE_LOGE_FROM_EXAMINATEUR = """
SELECT salle, loge FROM Examinateur WHERE id = %s
"""

# ---------- Logs

SELECT_ALL_LOGS = "SELECT * FROM Logs"


class DbInterface:
    """Interface de connexion à la base de données MariaDB."""

    def __init__(self):
        self.con = None
        self.connect()

    def connect(self):
        from app_secrets import DB_PARAMS, DB_SALT
        # DB_HOST peut être surchargé par variable d'environnement (Docker)
        params = dict(DB_PARAMS)
        db_host = os.environ.get('DB_HOST')
        if db_host:
            params['host'] = db_host
        self.con = mysql.connector.connect(**params)
        self.make_sql_update(INIT_DB, salt=DB_SALT)

    def make_sql_select(self, query, *args):
        """Exécute une requête SELECT paramétrée et renvoie toujours une liste."""
        if not self.con.is_connected():
            self.connect()
        with self.con.cursor(dictionary=True) as cur:
            cur.execute(query, args if args else None)
            data = cur.fetchall()
        self.con.commit()
        return data

    def make_sql_update(self, query, **kwargs):
        """Exécute une requête INSERT/UPDATE/DELETE paramétrée."""
        if not self.con.is_connected():
            self.connect()
        with self.con.cursor() as cur:
            cur.execute(query, kwargs if kwargs else None)
        self.con.commit()
