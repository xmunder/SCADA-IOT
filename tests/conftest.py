import sys
from pathlib import Path

# Permite importar infra/ingest/app.py como módulo en las pruebas.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "ingest"))
