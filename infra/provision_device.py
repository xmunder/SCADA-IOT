"""Aprovisiona un gateway en AWS IoT Core: Thing, certificado, política y CA raíz.

Uso (requiere credenciales AWS configuradas):
    python infra/provision_device.py --site PLANTA-01 --out certs

Crea los archivos en la carpeta indicada. Esa carpeta está excluida de Git.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

POLICY_NAME = "scada-gateway-policy"
ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"


def policy_document(region: str, account_id: str) -> dict:
    """Política de mínimo privilegio: cada gateway solo se conecta y publica en su propio tema."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iot:Connect",
                "Resource": f"arn:aws:iot:{region}:{account_id}:client/${{iot:Connection.Thing.ThingName}}",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Publish",
                "Resource": f"arn:aws:iot:{region}:{account_id}:topic/plant/${{iot:Connection.Thing.ThingName}}/telemetry",
            },
        ],
    }


def ensure_policy(iot, region: str, account_id: str) -> None:
    try:
        iot.create_policy(
            policyName=POLICY_NAME,
            policyDocument=json.dumps(policy_document(region, account_id)),
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise


def download_root_ca(destination: Path) -> None:
    with urllib.request.urlopen(ROOT_CA_URL, timeout=30) as response:
        destination.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Aprovisiona un gateway en AWS IoT Core")
    parser.add_argument("--site", required=True, help="Identificador del sitio (nombre del Thing)")
    parser.add_argument("--out", default="certs", help="Carpeta de salida para certificados")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = boto3.session.Session()
    region = session.region_name
    if not region:
        raise SystemExit("Defina AWS_REGION o configure una región por defecto en AWS CLI")

    account_id = session.client("sts").get_caller_identity()["Account"]
    iot = session.client("iot")

    iot.create_thing(thingName=args.site)
    keys = iot.create_keys_and_certificate(setAsActive=True)
    (out_dir / f"{args.site}-certificate.pem.crt").write_text(keys["certificatePem"], encoding="utf8")
    (out_dir / f"{args.site}-private.pem.key").write_text(keys["keyPair"]["PrivateKey"], encoding="utf8")
    download_root_ca(out_dir / "AmazonRootCA1.pem")

    ensure_policy(iot, region, account_id)
    iot.attach_policy(policyName=POLICY_NAME, target=keys["certificateArn"])
    iot.attach_thing_principal(thingName=args.site, principal=keys["certificateArn"])

    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    print(f"Thing '{args.site}' aprovisionado.")
    print(f"IOT_ENDPOINT={endpoint}")
    print(f"Certificados en: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
