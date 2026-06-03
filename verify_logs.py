"""
Script de vérification standalone de l'intégrité des logs en base de données.
"""
from pprint import pprint

import mysql.connector

from webserver.app_secrets import DB_PARAMS, verify_log_item

conn = mysql.connector.connect(**DB_PARAMS)
cursor = conn.cursor(dictionary=True)

cursor.execute("SELECT * FROM Logs ORDER BY id")
logs = cursor.fetchall()

previous_hash = ''
n_ok = 0
faulty = False

for log in logs:
    if not verify_log_item(log, previous_hash):
        print(f"Erreur d'intégrité détectée à l'entrée ID {log['id']}")
        pprint(log)
        faulty = True
        break
    print('.', end='', flush=True)
    n_ok += 1
    previous_hash = log['hash']

if not faulty:
    print(f"\n{n_ok} logs vérifiés et concordants.")

cursor.close()
conn.close()
