"""Cliente MQTT hacia AWS IoT Core con autenticación mutua TLS (certificado de dispositivo)."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from gateway.config import GatewayConfig

logger = logging.getLogger(__name__)

OnPublish = Callable[[Any, Any, int, Any, Any], None]


def build_client(cfg: GatewayConfig, on_publish: OnPublish | None = None) -> mqtt.Client:
    """Crea y conecta un cliente MQTT. La reconexión automática la gestiona paho en su hilo."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=cfg.site_id,
        protocol=mqtt.MQTTv311,
    )
    client.tls_set(
        ca_certs=cfg.ca_file,
        certfile=cfg.cert_file,
        keyfile=cfg.key_file,
        tls_version=ssl.PROTOCOL_TLSv1_2,
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    if on_publish is not None:
        client.on_publish = on_publish
    client.on_connect = lambda c, _u, _f, rc, _p: logger.info("Conectado a IoT Core (rc=%s)", rc)
    client.on_disconnect = lambda _c, _u, _f, rc, _p: logger.warning(
        "Desconectado de IoT Core (rc=%s); reintentando", rc
    )
    client.connect(cfg.iot_endpoint, cfg.iot_port, keepalive=30)
    client.loop_start()
    return client


class IotPublisher:
    """Publica lotes con QoS 1 y devuelve True solo si la nube confirmó (PUBACK)."""

    def __init__(self, cfg: GatewayConfig, timeout_s: float = 10.0) -> None:
        self._cfg = cfg
        self._timeout_s = timeout_s
        self._client: mqtt.Client | None = None

    def start(self) -> None:
        self._client = build_client(self._cfg)

    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    def publish(self, topic: str, payload: bytes) -> bool:
        """Bloqueante. Llamar desde un hilo de trabajo, no desde el bucle asyncio."""
        if self._client is None or not self._client.is_connected():
            return False
        info = self._client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=self._timeout_s)
        except (ValueError, RuntimeError):
            return False
        return bool(info.is_published())

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
