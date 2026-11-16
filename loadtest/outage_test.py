"""Prueba de corte de enlace: mide acumulación en el buffer y tiempo de recuperación.

Con el simulador y el gateway en ejecución, el script bloquea el tráfico saliente MQTT sobre TLS
(TCP 8883) con una regla del firewall de Windows. Es un corte de red real: el gateway detecta la
pérdida por su ``keepalive``, acumula mensajes en su buffer SQLite y, al quitar la regla, los
reenvía. El script muestrea el buffer cada ``--poll`` segundos y reporta:

  - ``backlog_max`` y ``backlog_at_restore``: mensajes acumulados durante el corte;
  - ``backlog_start_s``: tiempo desde el corte hasta que el buffer empieza a crecer (los lotes
    dejan de confirmarse); no es el tiempo que tarda el cliente MQTT en declarar la desconexión;
  - ``recovery_s``: tiempo desde el restablecimiento hasta que el buffer vuelve a su nivel base
    más ``--tolerance`` (por defecto 2 lotes, el ruido normal de mensajes en vuelo). Incluye la
    espera de reconexión del cliente (retroceso exponencial de hasta 30 s) y el vaciado.

Requiere una consola de PowerShell **como administrador** (el firewall lo exige). La regla siempre
se elimina al terminar, incluso ante Ctrl+C o un error. Afecta a todo el tráfico saliente por el
puerto 8883 del equipo durante la prueba.

Alternativas descartadas desde la nube: quitar la política y desactivar el certificado en IoT
Core no cierran una conexión MQTT ya establecida (se comprobó que el gateway seguía publicando).

Uso:
    python -m loadtest.outage_test --duration 300 --buffer data/buffer.db
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULE_NAME = "scada-outage-test"
MQTT_TLS_PORT = 8883


@dataclass(frozen=True)
class Sample:
    t: float  # segundos desde el inicio de la prueba
    pending: int


def analyze_timeline(samples: list[Sample], cut_t: float, restore_t: float, tolerance: int = 0) -> dict[str, Any]:
    """Calcula las métricas de acumulación y recuperación a partir de la serie de muestras."""
    if not samples:
        raise ValueError("No hay muestras del buffer")
    baseline = max((s.pending for s in samples if s.t < cut_t), default=0)
    during = [s for s in samples if cut_t <= s.t < restore_t]
    after = [s for s in samples if s.t >= restore_t]

    grown = next((s for s in during if s.pending > baseline + tolerance), None)
    drained = next((s for s in after if s.pending <= baseline + tolerance), None)
    return {
        "baseline_pending_max": baseline,
        "backlog_max": max((s.pending for s in samples if s.t >= cut_t), default=0),
        "backlog_at_restore": during[-1].pending if during else 0,
        "backlog_start_s": round(grown.t - cut_t, 2) if grown else None,
        "recovery_s": round(drained.t - restore_t, 2) if drained else None,
        "recovered": drained is not None,
    }


def read_pending(db_path: Path) -> int:
    """Cuenta los mensajes pendientes con una conexión de solo lectura (no interfiere con el gateway)."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
    finally:
        conn.close()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:  # no es Windows
        return False


def firewall(args: list[str]) -> None:
    subprocess.run(["netsh", "advfirewall", "firewall", *args], check=True, capture_output=True, text=True)


def block_mqtt() -> None:
    firewall(
        ["add", "rule", f"name={RULE_NAME}", "dir=out", "action=block", "protocol=TCP", f"remoteport={MQTT_TLS_PORT}"]
    )


def unblock_mqtt() -> None:
    firewall(["delete", "rule", f"name={RULE_NAME}"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba de corte y recuperación del gateway")
    parser.add_argument("--duration", type=float, default=300.0, help="segundos de corte")
    parser.add_argument("--baseline", type=float, default=30.0, help="segundos de observación previos al corte")
    parser.add_argument("--recovery-timeout", type=float, default=600.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--tolerance", type=int, default=2, help="lotes sobre la línea base que cuentan como recuperado"
    )
    parser.add_argument("--buffer", type=Path, default=Path("data/buffer.db"))
    parser.add_argument("--run-name", default="corte-5min")
    parser.add_argument("--out", type=Path, default=Path("loadtest/results"))
    args = parser.parse_args()
    if sys.platform != "win32" or not is_admin():
        parser.error("requiere Windows y una consola de PowerShell ejecutada como administrador")
    if not args.buffer.exists():
        parser.error(f"No existe el buffer {args.buffer}; inicie primero el gateway")

    samples: list[Sample] = []
    start = time.monotonic()

    def sample_until(deadline_s: float, stop_at_or_below: int | None = None) -> None:
        while True:
            now = time.monotonic() - start
            pending = read_pending(args.buffer)
            samples.append(Sample(round(now, 2), pending))
            if stop_at_or_below is not None and pending <= stop_at_or_below:
                return
            if now >= deadline_s:
                return
            time.sleep(args.poll)

    cut_t = restore_t = 0.0
    blocked = False
    try:
        sample_until(args.baseline)
        baseline_pending = max(s.pending for s in samples)
        block_mqtt()
        blocked = True
        cut_t = round(time.monotonic() - start, 2)
        print(f"Corte iniciado (t={cut_t:.1f}s): puerto {MQTT_TLS_PORT} bloqueado")
        sample_until(cut_t + args.duration)
        unblock_mqtt()
        blocked = False
        restore_t = round(time.monotonic() - start, 2)
        print(f"Enlace restablecido (t={restore_t:.1f}s): regla de firewall eliminada")
        sample_until(restore_t + args.recovery_timeout, stop_at_or_below=baseline_pending + args.tolerance)
    finally:
        if blocked:
            unblock_mqtt()
            print("Regla de firewall eliminada tras interrupción")

    result = {
        "run_name": args.run_name,
        "outage_s": round(restore_t - cut_t, 2),
        **analyze_timeline(samples, cut_t, restore_t, args.tolerance),
        "tolerance": args.tolerance,
    }
    out_dir = args.out / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outage.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    with (out_dir / "outage_timeline.csv").open("w", newline="", encoding="utf8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_s", "pending"])
        writer.writerows((s.t, s.pending) for s in samples)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
