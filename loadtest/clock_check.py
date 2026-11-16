"""Estima el desfase del reloj local frente a la hora de referencia con SNTP (milisegundos).

La latencia de ingesta compara el reloj del equipo de origen con el de la Lambda. Un desfase de
cientos de milisegundos la falsea por completo y, como la Lambda recorta a 0 las latencias
negativas, un reloj adelantado destruye la información de las latencias bajas. Por eso
``run_matrix`` se detiene si el desfase supera ``--max-clock-skew`` (50 ms por defecto).

El método principal es SNTP (UDP 123) contra ``time.aws.com`` y ``time.windows.com``: su incertidumbre
es del orden de la mitad del tiempo de ida y vuelta (decenas de ms). Si el puerto UDP 123 está
bloqueado se recurre al encabezado HTTP ``Date``, de resolución de 1 s, que NO sirve para verificar
desfases menores a ~1 s (en la práctica llegó a dar el signo contrario).

Convención: positivo = el equipo va atrasado respecto a la referencia. Para sincronizar en Windows
use ``w32tm /resync`` en una consola de administrador.

Uso:
    python -m loadtest.clock_check
"""

from __future__ import annotations

import email.utils
import socket
import statistics
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DATE_URL = "https://sts.us-east-1.amazonaws.com/"
NTP_HOSTS = ("time.aws.com", "time.windows.com")
NTP_EPOCH_OFFSET = 2_208_988_800  # segundos entre 1900 y 1970


@dataclass(frozen=True)
class ClockReading:
    offset_s: float  # referencia - equipo
    method: str  # "sntp" o "http-date"
    uncertainty_s: float


def sntp_offset_s(t1: float, t2: float, t3: float, t4: float) -> float:
    """Desfase (servidor - cliente) con las cuatro marcas de tiempo de SNTP, en segundos epoch.

    t1: envío del cliente; t2: recepción del servidor; t3: envío del servidor; t4: recepción del cliente.
    """
    return ((t2 - t1) + (t3 - t4)) / 2.0


def estimate_offset_s(observations: list[tuple[float, float, float]]) -> float:
    """Mediana de (reloj de AWS - reloj local) con el encabezado HTTP Date, en segundos.

    Cada observación es (t_envío, t_respuesta, fecha_servidor_en_epoch_entero). El encabezado
    trunca los milisegundos, por lo que se suma 0,5 s a la fecha como valor esperado.
    """
    if not observations:
        raise ValueError("Se requiere al menos una observación")
    return statistics.median(server + 0.5 - (sent + received) / 2 for sent, received, server in observations)


def query_sntp(host: str, timeout: float = 3.0) -> tuple[float, float]:
    """Una consulta SNTP. Devuelve (desfase_s, rtt_s)."""
    packet = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t1 = time.time()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(48)
        t4 = time.time()
    fields = struct.unpack("!12I", data)
    t2 = fields[8] - NTP_EPOCH_OFFSET + fields[9] / 2**32
    t3 = fields[10] - NTP_EPOCH_OFFSET + fields[11] / 2**32
    return sntp_offset_s(t1, t2, t3, t4), (t4 - t1) - (t3 - t2)


def pick_best_reading(readings: list[ClockReading]) -> ClockReading | None:
    """De varias fuentes elige la de menor incertidumbre (la de menor tiempo de ida y vuelta)."""
    return min(readings, key=lambda r: r.uncertainty_s) if readings else None


def _measure_sntp(samples: int) -> ClockReading | None:
    readings: list[ClockReading] = []
    for host in NTP_HOSTS:
        try:
            results = [query_sntp(host) for _ in range(samples)]
        except (OSError, struct.error):
            continue
        # las muestras con menor RTT son las más fiables
        best = sorted(results, key=lambda r: r[1])[: max(1, samples // 2)]
        readings.append(ClockReading(statistics.median(r[0] for r in best), "sntp", min(r[1] for r in results) / 2))
    return pick_best_reading(readings)


def _measure_http_date(samples: int, url: str) -> ClockReading:
    observations: list[tuple[float, float, float]] = []
    for _ in range(samples):
        sent = time.time()
        try:
            headers = urllib.request.urlopen(url, timeout=10).headers
        except urllib.error.HTTPError as err:  # STS responde 4xx a un GET anónimo; el encabezado Date sigue presente
            headers = err.headers
        received = time.time()
        server = email.utils.parsedate_to_datetime(headers["Date"]).timestamp()
        observations.append((sent, received, server))
    return ClockReading(estimate_offset_s(observations), "http-date", 1.0)


def measure_clock(samples: int = 5, url: str = DATE_URL) -> ClockReading:
    return _measure_sntp(samples) or _measure_http_date(samples, url)


def measure_clock_offset(samples: int = 5, url: str = DATE_URL) -> float:
    return measure_clock(samples, url).offset_s


def main() -> None:
    reading = measure_clock()
    print(
        f"Desfase estimado (referencia - equipo): {reading.offset_s * 1000:+.0f} ms "
        f"(método {reading.method}, incertidumbre ~{reading.uncertainty_s * 1000:.0f} ms)"
    )
    if reading.method == "http-date":
        print("SNTP (UDP 123) no respondió: con el encabezado HTTP solo se detectan desfases mayores a ~1 s.")
    if abs(reading.offset_s) > 0.05:
        print("El reloj local está desfasado: sincronice con NTP antes de medir latencias (w32tm /resync).")


if __name__ == "__main__":
    main()
