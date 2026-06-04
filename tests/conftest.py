"""Configuration pytest partagée entre tous les tests."""
import sys
from pathlib import Path

# Racine du projet et webserver accessibles depuis tous les tests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webserver"))
