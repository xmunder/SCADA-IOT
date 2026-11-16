import pytest

from gateway.config import ConfigError, GatewayConfig

BASE_ENV = {
    "SITE_ID": "PLANTA-01",
    "OPC_ENDPOINT": "opc.tcp://localhost:4840/scada-iot/simulador",
    "OPC_NAMESPACE_URI": "urn:scada-iot:planta",
    "IOT_ENDPOINT": "example-ats.iot.us-east-1.amazonaws.com",
    "CERT_FILE": "certs/c.pem",
    "KEY_FILE": "certs/k.key",
    "CA_FILE": "certs/ca.pem",
}


def test_defaults_are_applied():
    cfg = GatewayConfig.from_env(BASE_ENV)
    assert cfg.iot_port == 8883
    assert cfg.batch_size == 100
    assert cfg.inflight_window == 16
    assert cfg.opc_root_name == "Planta"


def test_missing_required_variable_raises():
    env = dict(BASE_ENV)
    del env["SITE_ID"]
    with pytest.raises(ConfigError, match="SITE_ID"):
        GatewayConfig.from_env(env)


def test_blank_variable_counts_as_missing():
    env = dict(BASE_ENV, SITE_ID="   ")
    with pytest.raises(ConfigError):
        GatewayConfig.from_env(env)


@pytest.mark.parametrize(
    "name, value", [("GATEWAY_BATCH_SIZE", "0"), ("GATEWAY_FLUSH_INTERVAL_S", "-1"), ("GATEWAY_INFLIGHT_WINDOW", "0")]
)
def test_non_positive_values_are_rejected(name, value):
    with pytest.raises(ConfigError):
        GatewayConfig.from_env(dict(BASE_ENV, **{name: value}))
