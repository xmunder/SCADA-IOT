"""Servidor OPC UA simulado con N tags de proceso.

Uso:
    python -m simulator.plant_simulator --tags 200 --period 1.0

Solo para laboratorio y pruebas de carga. No debe conectarse a equipos de planta.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math

from asyncua import Server

NAMESPACE_URI = "urn:scada-iot:planta"
ROOT_NAME = "Planta"
logger = logging.getLogger("simulador")


def tag_value(index: int, t: float) -> float | bool:
    """Valor simulado del tag `index` en el instante `t` (segundos).

    Se generan tres comportamientos típicos: senoidal (temperatura), rampa
    (contador de producción) y estado binario (bomba encendida/apagada).
    """
    kind = index % 3
    if kind == 0:
        return 50.0 + 20.0 * math.sin(t / 10.0 + index)
    if kind == 1:
        return float(int(t) % 100)
    return int(t / 15.0 + index) % 2 == 0


async def run(endpoint: str, tag_count: int, period_s: float) -> None:
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("Simulador de planta SCADA-IOT")

    idx = await server.register_namespace(NAMESPACE_URI)
    root = await server.nodes.objects.add_object(idx, ROOT_NAME)

    variables = []
    for i in range(tag_count):
        var = await root.add_variable(idx, f"Tag_{i:04d}", tag_value(i, 0.0))
        await var.set_writable()
        variables.append((i, var))

    async with server:
        logger.info("Servidor en %s con %d tags", endpoint, tag_count)
        t = 0.0
        while True:
            await asyncio.gather(*(var.write_value(tag_value(i, t)) for i, var in variables))
            t += period_s
            await asyncio.sleep(period_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador OPC UA de planta")
    parser.add_argument("--endpoint", default="opc.tcp://0.0.0.0:4840/scada-iot/simulador")
    parser.add_argument("--tags", type=int, default=200)
    parser.add_argument("--period", type=float, default=1.0, help="segundos entre actualizaciones")
    args = parser.parse_args()
    if args.tags <= 0 or args.period <= 0:
        parser.error("--tags y --period deben ser mayores que cero")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(args.endpoint, args.tags, args.period))
    except KeyboardInterrupt:
        logger.info("Simulador detenido")


if __name__ == "__main__":
    main()
