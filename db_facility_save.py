"""
Interface de sauvegarde en base de données pour l'algorithme de placement.

Recrée complètement le schéma et insère toutes les données à chaque exécution.
Les requêtes utilisent des paramètres liés pour prévenir les injections SQL.
Note : les directives DELIMITER ne sont pas utilisées car mysql.connector
       les gère nativement sans elles.
"""
import os

import mysql.connector

from webserver.app_secrets import DB_PARAMS, DB_SALT

SQL_BASE = [
    {"sql": "SET @salt = %(salt)s", "params": {"salt": DB_SALT}},
    {"sql": "DROP TABLE IF EXISTS Oral"},
    {"sql": "DROP TABLE IF EXISTS Examinateur"},
    {"sql": "DROP TABLE IF EXISTS Candidat"},
    {"sql": "DROP TABLE IF EXISTS Matiere"},
    {"sql": "DROP TABLE IF EXISTS Loge"},
    {"sql": "DROP TABLE IF EXISTS TokenSignature"},
    {"sql": "DROP TABLE IF EXISTS Logs"},
    {"sql": "DROP PROCEDURE IF EXISTS log_action"},
    {"sql": "DROP TRIGGER IF EXISTS after_insert_Matiere"},
    {"sql": "DROP TRIGGER IF EXISTS after_insert_Candidat"},
    {"sql": "DROP TRIGGER IF EXISTS after_insert_Examinateur"},
    {"sql": "DROP TRIGGER IF EXISTS after_insert_Oral"},
    {"sql": "DROP TRIGGER IF EXISTS after_update_Matiere"},
    {"sql": "DROP TRIGGER IF EXISTS after_update_Candidat"},
    {"sql": "DROP TRIGGER IF EXISTS after_update_Examinateur"},
    {"sql": "DROP TRIGGER IF EXISTS after_update_Oral"},
    {"sql": "DROP TRIGGER IF EXISTS after_delete_Matiere"},
    {"sql": "DROP TRIGGER IF EXISTS after_delete_Candidat"},
    {"sql": "DROP TRIGGER IF EXISTS after_delete_Examinateur"},
    {"sql": "DROP TRIGGER IF EXISTS after_delete_Oral"},
    {"sql": "DROP TRIGGER IF EXISTS before_update_Oral"},
    {"sql": """
    CREATE TABLE IF NOT EXISTS TokenSignature (
        id INT PRIMARY KEY AUTO_INCREMENT,
        token TEXT,
        time_limit TEXT NOT NULL,
        oral INTEGER NOT NULL
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Matiere (
        id INT PRIMARY KEY,
        nom TEXT NOT NULL,
        nom_court TEXT NOT NULL
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Candidat (
        id INT PRIMARY KEY,
        nom TEXT NOT NULL,
        numero TEXT NOT NULL,
        etablissement TEXT NOT NULL,
        choix1 INT NOT NULL,
        choix2 INT NOT NULL,
        tiers_temps BOOLEAN NOT NULL,
        login_key TEXT NOT NULL DEFAULT '',
        password_hash TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (choix1) REFERENCES Matiere (id),
        FOREIGN KEY (choix2) REFERENCES Matiere (id)
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Examinateur (
        id INT PRIMARY KEY AUTO_INCREMENT,
        nom TEXT NOT NULL,
        etablissements TEXT NOT NULL,
        matiere INT NOT NULL,
        salle TEXT NOT NULL,
        loge TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        FOREIGN KEY (matiere) REFERENCES Matiere (id)
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Oral (
        id INT PRIMARY KEY,
        examinateur INT NOT NULL,
        candidat INT NOT NULL,
        heure_sujet TEXT NOT NULL,
        heure_oral TEXT NOT NULL,
        heure_fin TEXT NOT NULL,
        mis_a_jour BOOLEAN NOT NULL DEFAULT FALSE,
        emargement TEXT NOT NULL DEFAULT '',
        heure_emargement TEXT NOT NULL DEFAULT '',
        hash_emargement TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (examinateur) REFERENCES Examinateur (id),
        FOREIGN KEY (candidat) REFERENCES Candidat (id)
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Loge (
        id INT PRIMARY KEY AUTO_INCREMENT,
        nom TEXT NOT NULL,
        password_hash TEXT NOT NULL
    )
    """},
    {"sql": """
    CREATE TABLE IF NOT EXISTS Logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        action_data JSON,
        table_name VARCHAR(255),
        hash CHAR(64),
        timestamp TEXT NOT NULL DEFAULT (NOW())
    )
    """},
    # Procédure stockée de logging avec chaîne de hash
    {"sql": """
    CREATE PROCEDURE log_action(IN action_data JSON, IN table_name VARCHAR(255))
    BEGIN
        DECLARE last_hash CHAR(64);
        DECLARE new_hash CHAR(64);
        SELECT hash INTO last_hash FROM Logs ORDER BY id DESC LIMIT 1 FOR UPDATE;
        IF last_hash IS NULL THEN
            SET last_hash = '';
        END IF;
        SET new_hash = SHA2(CONCAT(action_data, table_name, last_hash, @salt), 256);
        INSERT INTO Logs (action_data, table_name, hash)
        VALUES (action_data, table_name, new_hash);
    END
    """},
    # Triggers INSERT
    {"sql": """
    CREATE TRIGGER after_insert_Matiere
    AFTER INSERT ON Matiere FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'INSERT', 'data',
            JSON_OBJECT('id', NEW.id, 'nom', NEW.nom, 'nom_court', NEW.nom_court)
        ), 'Matiere');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_insert_Candidat
    AFTER INSERT ON Candidat FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'INSERT', 'data',
            JSON_OBJECT('id', NEW.id, 'nom', NEW.nom, 'numero', NEW.numero,
                        'etablissement', NEW.etablissement,
                        'choix1', NEW.choix1, 'choix2', NEW.choix2,
                        'tiers_temps', NEW.tiers_temps)
        ), 'Candidat');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_insert_Examinateur
    AFTER INSERT ON Examinateur FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'INSERT', 'data',
            JSON_OBJECT('id', NEW.id, 'nom', NEW.nom,
                        'etablissements', NEW.etablissements,
                        'matiere', NEW.matiere, 'salle', NEW.salle,
                        'loge', NEW.loge)
        ), 'Examinateur');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_insert_Oral
    AFTER INSERT ON Oral FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'INSERT', 'data',
            JSON_OBJECT('id', NEW.id, 'examinateur', NEW.examinateur,
                        'candidat', NEW.candidat, 'heure_sujet', NEW.heure_sujet,
                        'heure_oral', NEW.heure_oral, 'heure_fin', NEW.heure_fin,
                        'mis_a_jour', NEW.mis_a_jour, 'emargement', NEW.emargement,
                        'heure_emargement', NEW.heure_emargement,
                        'hash_emargement', NEW.hash_emargement)
        ), 'Oral');
    END
    """},
    # Triggers UPDATE
    {"sql": """
    CREATE TRIGGER after_update_Matiere
    AFTER UPDATE ON Matiere FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'UPDATE',
            'data_new', JSON_OBJECT('id', NEW.id, 'nom', NEW.nom, 'nom_court', NEW.nom_court),
            'data_old', JSON_OBJECT('id', OLD.id, 'nom', OLD.nom, 'nom_court', OLD.nom_court)
        ), 'Matiere');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_update_Candidat
    AFTER UPDATE ON Candidat FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'UPDATE',
            'data_new', JSON_OBJECT('id', NEW.id, 'nom', NEW.nom, 'numero', NEW.numero,
                        'etablissement', NEW.etablissement,
                        'choix1', NEW.choix1, 'choix2', NEW.choix2,
                        'tiers_temps', NEW.tiers_temps),
            'data_old', JSON_OBJECT('id', OLD.id, 'nom', OLD.nom, 'numero', OLD.numero,
                        'etablissement', OLD.etablissement,
                        'choix1', OLD.choix1, 'choix2', OLD.choix2,
                        'tiers_temps', OLD.tiers_temps)
        ), 'Candidat');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_update_Examinateur
    AFTER UPDATE ON Examinateur FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'UPDATE',
            'data_new', JSON_OBJECT('id', NEW.id, 'nom', NEW.nom,
                        'etablissements', NEW.etablissements,
                        'matiere', NEW.matiere, 'salle', NEW.salle,
                        'loge', NEW.loge),
            'data_old', JSON_OBJECT('id', OLD.id, 'nom', OLD.nom,
                        'etablissements', OLD.etablissements,
                        'matiere', OLD.matiere, 'salle', OLD.salle,
                        'loge', OLD.loge)
        ), 'Examinateur');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_update_Oral
    AFTER UPDATE ON Oral FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'UPDATE',
            'data_new', JSON_OBJECT('id', NEW.id, 'examinateur', NEW.examinateur,
                        'candidat', NEW.candidat, 'heure_sujet', NEW.heure_sujet,
                        'heure_oral', NEW.heure_oral, 'heure_fin', NEW.heure_fin,
                        'mis_a_jour', NEW.mis_a_jour, 'emargement', NEW.emargement,
                        'heure_emargement', NEW.heure_emargement,
                        'hash_emargement', NEW.hash_emargement),
            'data_old', JSON_OBJECT('id', OLD.id, 'examinateur', OLD.examinateur,
                        'candidat', OLD.candidat, 'heure_sujet', OLD.heure_sujet,
                        'heure_oral', OLD.heure_oral, 'heure_fin', OLD.heure_fin,
                        'mis_a_jour', OLD.mis_a_jour, 'emargement', OLD.emargement,
                        'heure_emargement', OLD.heure_emargement,
                        'hash_emargement', OLD.hash_emargement)
        ), 'Oral');
    END
    """},
    # Triggers DELETE
    {"sql": """
    CREATE TRIGGER after_delete_Matiere
    AFTER DELETE ON Matiere FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'DELETE', 'data',
            JSON_OBJECT('id', OLD.id, 'nom', OLD.nom, 'nom_court', OLD.nom_court)
        ), 'Matiere');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_delete_Candidat
    AFTER DELETE ON Candidat FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'DELETE', 'data',
            JSON_OBJECT('id', OLD.id, 'nom', OLD.nom, 'numero', OLD.numero,
                        'etablissement', OLD.etablissement,
                        'choix1', OLD.choix1, 'choix2', OLD.choix2,
                        'tiers_temps', OLD.tiers_temps)
        ), 'Candidat');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_delete_Examinateur
    AFTER DELETE ON Examinateur FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'DELETE', 'data',
            JSON_OBJECT('id', OLD.id, 'nom', OLD.nom,
                        'etablissements', OLD.etablissements,
                        'matiere', OLD.matiere, 'salle', OLD.salle,
                        'loge', OLD.loge)
        ), 'Examinateur');
    END
    """},
    {"sql": """
    CREATE TRIGGER after_delete_Oral
    AFTER DELETE ON Oral FOR EACH ROW
    BEGIN
        CALL log_action(JSON_OBJECT(
            'action', 'DELETE', 'data',
            JSON_OBJECT('id', OLD.id, 'examinateur', OLD.examinateur,
                        'candidat', OLD.candidat, 'heure_sujet', OLD.heure_sujet,
                        'heure_oral', OLD.heure_oral, 'heure_fin', OLD.heure_fin,
                        'mis_a_jour', OLD.mis_a_jour, 'emargement', OLD.emargement,
                        'heure_emargement', OLD.heure_emargement,
                        'hash_emargement', OLD.hash_emargement)
        ), 'Oral');
    END
    """},
    # Trigger BEFORE UPDATE : calcule le hash de l'émargement
    {"sql": """
    CREATE TRIGGER before_update_Oral
    BEFORE UPDATE ON Oral FOR EACH ROW
    BEGIN
        IF NEW.emargement != '' AND NEW.heure_emargement != '' THEN
            SET NEW.hash_emargement = SHA2(
                CONCAT(NEW.emargement, NEW.heure_emargement, @salt), 256
            );
        ELSE
            SET NEW.hash_emargement = '';
        END IF;
    END
    """},
]

SQL_INSERT_MATIERES = """
INSERT INTO Matiere (id, nom, nom_court)
VALUES (%(id)s, %(nom)s, %(nom_court)s)
"""

SQL_INSERT_CANDIDATS = """
INSERT INTO Candidat (id, nom, numero, etablissement, choix1, choix2,
                      tiers_temps, login_key, password_hash)
VALUES (%(id)s, %(nom)s, %(numero)s, %(etablissement)s, %(choix1)s, %(choix2)s,
        %(tiers_temps)s, %(login_key)s, %(password_hash)s)
"""

SQL_INSERT_EXAMINATEURS = """
INSERT INTO Examinateur (id, nom, etablissements, matiere, salle, loge, password_hash)
VALUES (%(id)s, %(nom)s, %(etablissements)s, %(matiere)s, %(salle)s,
        %(loge)s, %(password_hash)s)
"""

SQL_INSERT_ORAUX = """
INSERT INTO Oral (id, examinateur, candidat, heure_sujet, heure_oral, heure_fin)
VALUES (%(id)s, %(examinateur)s, %(candidat)s, %(heure_sujet)s,
        %(heure_oral)s, %(heure_fin)s)
"""

SQL_INSERT_LOGES = """
INSERT INTO Loge (nom, password_hash)
VALUES (%(nom)s, %(password_hash)s)
"""


class DbFacility:
    """Recrée le schéma complet et insère toutes les données."""

    @classmethod
    def read_existing_credentials(cls) -> dict:
        """Lit les credentials existants en base SANS modifier le schéma.

        Doit être appelé AVANT __init__() qui recrée les tables.
        Retourne {"candidats": {numero: {"login_key": str, "password_hash": str}}}
        ou un dict avec "candidats" vide si la table n'existe pas encore (premier run).

        :returns: Dict des credentials candidats indexés par numero.
        """
        result: dict = {"candidats": {}}
        try:
            params = dict(DB_PARAMS)
            db_host = os.environ.get('DB_HOST')
            if db_host:
                params['host'] = db_host
            conn = mysql.connector.connect(**params)
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT numero, login_key, password_hash FROM Candidat"
                )
                for row in cursor.fetchall():
                    result["candidats"][row["numero"]] = {
                        "login_key":    row["login_key"],
                        "password_hash": row["password_hash"],
                    }
            conn.close()
        except mysql.connector.Error:
            pass  # Table inexistante (premier run) ou DB inaccessible
        return result

    def __init__(self):
        params = dict(DB_PARAMS)
        db_host = os.environ.get('DB_HOST')
        if db_host:
            params['host'] = db_host
        self.conn = mysql.connector.connect(**params)
        with self.conn.cursor() as c:
            for entry in SQL_BASE:
                params = entry.get("params")
                c.execute(entry["sql"], params)
        self.conn.commit()

    def save_all(self, algo):
        # Assignation des idx séquentiels
        for i, obj in enumerate(algo.liste_candidats):
            obj.idx = i + 1
        for i, obj in enumerate(algo.liste_matieres):
            obj.idx = i + 1
        for i, obj in enumerate(algo.liste_examinateurs):
            obj.idx = i + 1
        for i, obj in enumerate(algo.liste_oraux):
            obj.idx = i + 1

        dicts_candidats = [c.to_dict() for c in algo.liste_candidats]
        dicts_matieres = [m.to_dict() for m in algo.liste_matieres]
        dicts_examinateurs = [e.to_dict() for e in algo.liste_examinateurs]
        dicts_oraux = [o.to_dict() for o in algo.liste_oraux]

        self.save_matieres(dicts_matieres)
        self.save_candidats(dicts_candidats)
        self.save_examinateurs(dicts_examinateurs)
        self.save_oraux(dicts_oraux)

    def save_matieres(self, matieres):
        with self.conn.cursor() as c:
            for m in matieres:
                c.execute(SQL_INSERT_MATIERES, m)
        self.conn.commit()

    def save_candidats(self, candidats):
        with self.conn.cursor() as c:
            for candidat in candidats:
                c.execute(SQL_INSERT_CANDIDATS, candidat)
        self.conn.commit()

    def save_examinateurs(self, examinateurs):
        with self.conn.cursor() as c:
            for examinateur in examinateurs:
                c.execute(SQL_INSERT_EXAMINATEURS, examinateur)
        self.conn.commit()

    def save_oraux(self, oraux):
        with self.conn.cursor() as c:
            for oral in oraux:
                c.execute(SQL_INSERT_ORAUX, oral)
        self.conn.commit()

    def save_loges(self, loges: dict):
        """Enregistre les mots de passe des loges. loges = {nom: password_hash}."""
        with self.conn.cursor() as c:
            for nom, password_hash in loges.items():
                c.execute(SQL_INSERT_LOGES, {'nom': nom, 'password_hash': password_hash})
        self.conn.commit()
