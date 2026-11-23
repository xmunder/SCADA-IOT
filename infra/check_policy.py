"""Verifica experimentalmente la política de mínimo privilegio de un gateway en AWS IoT Core.

Con el certificado del dispositivo comprueba que:
  1. puede conectarse con su propio nombre de Thing y publicar en sus temas de telemetría y de estado (permitido);
  2. no puede publicar en los temas de otro sitio (esperado: rechazado por IoT Core);
  3. no puede conectarse con un identificador de cliente ajeno (esperado: rechazado).

Uso (con las variables de entorno del gateway: IOT_ENDPOINT, CERT_FILE, KEY_FILE, CA_FILE):
    python -m infra.check_policy --site PLANTA-01

Detenga el gateway antes: IoT Core desconecta a un cliente cuando otro usa el mismo client_id.
"""

from __future__ import annotations

import argparse
import json
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from gateway.config import GatewayConfig


def evaluate(
    own_published: bool,
    other_published: bool,
    foreign_client_connected: bool,
    own_status_published: bool = True,
    other_status_published: bool = False,
) -> dict[str, bool]:
    """Un resultado es correcto si lo permitido funciona y lo prohibido falla."""
    return {
        "publish_own_topic_allowed": own_published,
        "publish_other_topic_denied": not other_published,
        "publish_own_status_allowed": own_status_published,
        "publish_other_status_denied": not other_status_published,
        "connect_foreign_client_id_denied": not foreign_client_connected,
        "policy_enforced": own_published
        and not other_published
        and own_status_published
        and not other_status_published
        and not foreign_client_connected,
    }


def _client(cfg: GatewayConfig, client_id: str) -> tuple[mqtt.Client, threading.Event]:
    connected = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311)
    client.tls_set(ca_certs=cfg.ca_file, certfile=cfg.cert_file, keyfile=cfg.key_file, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.on_connect = lambda c, u, f, rc, p: connected.set() if not rc.is_failure else None
    client.reconnect_delay_set(min_delay=1, max_delay=2)
    return client, connected


def _try_publish(client: mqtt.Client, topic: str, timeout_s: float = 6.0) -> bool:
    info = client.publish(topic, json.dumps({"check": "policy"}), qos=1)
    try:
        info.wait_for_publish(timeout=timeout_s)
    except (ValueError, RuntimeError):
        return False
    return bool(info.is_published())


def _publish_on_fresh_connection(cfg: GatewayConfig, client_id: str, topic: str) -> bool:
    """Publica en `topic` con una conexión nueva y propia.

    IoT Core cierra la conexión cuando rechaza una publicación; por eso cada prueba de rechazo usa la suya y
    un resultado "no publicado" siempre viene de una conexión que sí se pudo establecer.
    """
    client, connected = _client(cfg, client_id)
    client.connect(cfg.iot_endpoint, cfg.iot_port, keepalive=30)
    client.loop_start()
    try:
        if not connected.wait(10):
            raise SystemExit("No se pudo conectar con el identificador propio; revise el endpoint y los certificados")
        return _try_publish(client, topic)
    finally:
        client.loop_stop()
        client.disconnect()
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprueba la política de IoT Core de un gateway")
    parser.add_argument("--site", required=True)
    parser.add_argument("--other-site", default="PLANTA-99")
    args = parser.parse_args()
    cfg = GatewayConfig.from_env()

    own_published = _publish_on_fresh_connection(cfg, args.site, f"plant/{args.site}/telemetry")
    own_status_published = _publish_on_fresh_connection(cfg, args.site, f"plant/{args.site}/status")
    other_published = _publish_on_fresh_connection(cfg, args.site, f"plant/{args.other_site}/telemetry")
    other_status_published = _publish_on_fresh_connection(cfg, args.site, f"plant/{args.other_site}/status")

    foreign, foreign_connected = _client(cfg, f"{args.site}-intruso")
    try:
        foreign.connect(cfg.iot_endpoint, cfg.iot_port, keepalive=30)
        foreign.loop_start()
        foreign_ok = foreign_connected.wait(8)
    except (OSError, ssl.SSLError):
        foreign_ok = False
    finally:
        foreign.loop_stop()
        foreign.disconnect()

    print(
        json.dumps(
            evaluate(own_published, other_published, foreign_ok, own_status_published, other_status_published),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
