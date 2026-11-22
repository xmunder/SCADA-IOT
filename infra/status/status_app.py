"""Lambda that turns the gateway heartbeat into CloudWatch metrics.

The gateway publishes its state on plant/<site>/status. This function only emits metrics in Embedded Metric
Format (through its logs): it has no permission to write to DynamoDB, which keeps it to the least privilege.

The metric timestamp is the Lambda clock, not the gateway clock, so a skewed gateway clock cannot hide a
heartbeat. The absence of GatewayHeartbeat is what the "gateway down" alarm watches.
"""

from __future__ import annotations

import json
import time
from typing import Any

METRICS_NAMESPACE = "ScadaIot"
METRICS = (
    "GatewayHeartbeat",
    "GatewayPendingBatches",
    "GatewayOpcConnected",
    "GatewayMqttConnected",
    "GatewayReadingsDropped",
)


def build_status_emf(status: dict[str, Any], timestamp_ms: int) -> dict[str, Any]:
    return {
        "Site": str(status["site"]),
        "GatewayHeartbeat": 1,
        "GatewayPendingBatches": int(status.get("pending", 0)),
        "GatewayOpcConnected": 1 if status.get("opc_connected") else 0,
        "GatewayMqttConnected": 1 if status.get("mqtt_connected") else 0,
        "GatewayReadingsDropped": int(status.get("readings_dropped", 0)),
        "_aws": {
            "Timestamp": timestamp_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": METRICS_NAMESPACE,
                    "Dimensions": [["Site"]],
                    "Metrics": [{"Name": name, "Unit": "Count"} for name in METRICS],
                }
            ],
        },
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, bool]:
    if event.get("type") != "status" or not event.get("site"):
        return {"accepted": False}
    print(json.dumps(build_status_emf(event, int(time.time() * 1000))))
    return {"accepted": True}
