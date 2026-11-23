"""Configuración del gateway, leída desde variables de entorno."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(ValueError):
    """Se lanza cuando falta una variable requerida o su valor no es válido."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Variable de entorno requerida ausente: {name}")
    return value


@dataclass(frozen=True)
class GatewayConfig:
    site_id: str
    opc_endpoint: str
    opc_namespace_uri: str
    opc_root_name: str
    iot_endpoint: str
    iot_port: int
    cert_file: str
    key_file: str
    ca_file: str
    buffer_path: str
    batch_size: int
    flush_interval_s: float
    inflight_window: int
    opc_reconnect_max_s: float = 30.0
    status_interval_s: float = 15.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GatewayConfig:
        env = os.environ if env is None else env

        batch_size = int(env.get("GATEWAY_BATCH_SIZE", "100"))
        flush_interval_s = float(env.get("GATEWAY_FLUSH_INTERVAL_S", "1.0"))
        inflight_window = int(env.get("GATEWAY_INFLIGHT_WINDOW", "16"))

        opc_reconnect_max_s = float(env.get("OPC_RECONNECT_MAX_S", "30"))
        if opc_reconnect_max_s <= 0:
            raise ConfigError("OPC_RECONNECT_MAX_S debe ser mayor que cero")
        status_interval_s = float(env.get("GATEWAY_STATUS_INTERVAL_S", "15"))
        if status_interval_s <= 0:
            raise ConfigError("GATEWAY_STATUS_INTERVAL_S debe ser mayor que cero")
        if batch_size <= 0:
            raise ConfigError("GATEWAY_BATCH_SIZE debe ser mayor que cero")
        if flush_interval_s <= 0:
            raise ConfigError("GATEWAY_FLUSH_INTERVAL_S debe ser mayor que cero")
        if inflight_window <= 0:
            raise ConfigError("GATEWAY_INFLIGHT_WINDOW debe ser mayor que cero")

        return cls(
            site_id=_required(env, "SITE_ID"),
            opc_endpoint=_required(env, "OPC_ENDPOINT"),
            opc_namespace_uri=_required(env, "OPC_NAMESPACE_URI"),
            opc_root_name=env.get("OPC_ROOT_NAME", "Planta"),
            iot_endpoint=_required(env, "IOT_ENDPOINT"),
            iot_port=int(env.get("IOT_PORT", "8883")),
            cert_file=_required(env, "CERT_FILE"),
            key_file=_required(env, "KEY_FILE"),
            ca_file=_required(env, "CA_FILE"),
            buffer_path=env.get("BUFFER_PATH", "data/buffer.db"),
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
            inflight_window=inflight_window,
            opc_reconnect_max_s=opc_reconnect_max_s,
            status_interval_s=status_interval_s,
        )
