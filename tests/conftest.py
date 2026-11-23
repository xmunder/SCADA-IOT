import sys
from pathlib import Path

# Permite importar las Lambdas de infra/ como módulos en las pruebas.
for lambda_dir in ("ingest", "status"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / lambda_dir))
