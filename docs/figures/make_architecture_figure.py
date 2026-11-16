"""Genera la figura de arquitectura (docs/figures/architecture.png) sin servicios externos.

Uso (Pillow no es dependencia del proyecto; se pide solo para este script):
    uv run --with pillow python docs/figures/make_architecture_figure.py

El tamaño (1050 x 1360 px) corresponde a 3.5 pulgadas de ancho a 300 ppp.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1050, 1360
INK = "#1a1a1a"
OT_FILL = "#eeeeee"
CLOUD_FILL = "#e4ecf5"
BOX_FILL = "#ffffff"
OUT = Path(__file__).with_name("architecture.png")

FONT_DIRS = ["C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]
REGULAR = ["arial.ttf", "DejaVuSans.ttf", "Arial.ttf"]
BOLD = ["arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]


def load_font(names: list[str], size: int) -> ImageFont.ImageFont:
    for directory in FONT_DIRS:
        for name in names:
            path = Path(directory) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


F_ZONE = load_font(BOLD, 27)
F_TITLE = load_font(BOLD, 30)
F_TEXT = load_font(REGULAR, 25)
F_LABEL = load_font(REGULAR, 24)

image = Image.new("RGB", (WIDTH, HEIGHT), "white")
draw = ImageDraw.Draw(image)


def zone(box: tuple[int, int, int, int], title: str, fill: str) -> None:
    draw.rounded_rectangle(box, radius=20, fill=fill, outline=INK, width=3)
    draw.text((box[0] + 22, box[1] + 14), title, font=F_ZONE, fill=INK, anchor="la")


def box(rect: tuple[int, int, int, int], title: str, detail: str = "", fill: str = BOX_FILL) -> None:
    draw.rounded_rectangle(rect, radius=14, fill=fill, outline=INK, width=3)
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    if detail:
        draw.text((cx, cy - 16), title, font=F_TITLE, fill=INK, anchor="mm")
        draw.text((cx, cy + 20), detail, font=F_TEXT, fill=INK, anchor="mm")
    else:
        draw.text((cx, cy), title, font=F_TEXT if len(title) > 26 else F_TITLE, fill=INK, anchor="mm")


def cylinder(rect: tuple[int, int, int, int], title: str, detail: str) -> None:
    x0, y0, x1, y1 = rect
    cap = 36
    draw.ellipse((x0, y1 - cap, x1, y1), fill=BOX_FILL, outline=INK, width=3)
    draw.rectangle((x0 + 2, y0 + cap // 2, x1 - 2, y1 - cap // 2), fill=BOX_FILL)
    draw.line((x0, y0 + cap // 2, x0, y1 - cap // 2), fill=INK, width=3)
    draw.line((x1, y0 + cap // 2, x1, y1 - cap // 2), fill=INK, width=3)
    draw.ellipse((x0, y0, x1, y0 + cap), fill=BOX_FILL, outline=INK, width=3)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 + 6
    draw.text((cx, cy - 10), title, font=F_TITLE, fill=INK, anchor="mm")
    draw.text((cx, cy + 22), detail, font=F_TEXT, fill=INK, anchor="mm")


def arrow(start: tuple[int, int], end: tuple[int, int], dashed: bool = False) -> None:
    x0, y0 = start
    x1, y1 = end
    if dashed:
        steps = 14
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        for i in range(0, int(length), steps * 2):
            a = i / length
            b = min(i + steps, length) / length
            draw.line(
                (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a, x0 + (x1 - x0) * b, y0 + (y1 - y0) * b), fill=INK, width=4
            )
    else:
        draw.line((x0, y0, x1, y1), fill=INK, width=4)
    head = 18
    if abs(y1 - y0) >= abs(x1 - x0):
        sign = 1 if y1 > y0 else -1
        draw.polygon([(x1, y1), (x1 - head // 2, y1 - sign * head), (x1 + head // 2, y1 - sign * head)], fill=INK)
    else:
        sign = 1 if x1 > x0 else -1
        draw.polygon([(x1, y1), (x1 - sign * head, y1 - head // 2), (x1 - sign * head, y1 + head // 2)], fill=INK)


def label(position: tuple[int, int], text: str) -> None:
    draw.text(position, text, font=F_LABEL, fill=INK, anchor="lm")


# ---------------------------------------------------------------- zona OT
zone((30, 30, 1020, 720), "Zona OT · planta (ISA-95, niveles 0 a 3)", OT_FILL)
box((230, 100, 820, 190), "Servidor OPC UA", "simulador (asyncua)")
arrow((525, 190), (525, 240))
label((545, 215), "OPC UA (IEC 62541)")

draw.rounded_rectangle((110, 240, 940, 690), radius=16, fill="#f9f9f9", outline=INK, width=3)
draw.text((525, 268), "Gateway de borde", font=F_TITLE, fill=INK, anchor="mm")
steps = [
    (300, "Lector OPC UA", "suscripción de 500 ms"),
    (396, "Agrupador de lotes", "100 lecturas o 1 s"),
    (492, "Buffer SQLite", "persiste antes de enviar"),
    (588, "Publicador MQTT", "QoS 1 · TLS mutuo"),
]
BOX_H = 76
for index, (top, title, detail) in enumerate(steps):
    box((250, top, 800, top + BOX_H), title, detail)
    if index < len(steps) - 1:
        arrow((525, top + BOX_H), (525, steps[index + 1][0]))

# ---------------------------------------------------------------- conducto
arrow((525, 588 + BOX_H), (525, 855))
label((545, 745), "MQTT/TLS · puerto 8883 · solo salida")

# ---------------------------------------------------------------- zona nube
zone((30, 770, 1020, 1340), "Zona TI · nube AWS (ISA-95, niveles 3 y 4)", CLOUD_FILL)
box((230, 855, 820, 935), "AWS IoT Core", "broker MQTT · política por Thing")
arrow((525, 935), (525, 975))
box((230, 975, 820, 1045), "Regla de IoT", "SELECT * FROM 'plant/+/telemetry'")
arrow((340, 1045), (340, 1085))
box((90, 1085, 590, 1175), "Lambda de ingesta", "valida · mide latencia · escribe")
arrow((590, 1130), (680, 1130), dashed=True)
box((680, 1085, 1000, 1175), "CloudWatch", "métricas EMF y logs")
arrow((340, 1175), (340, 1220))
label((360, 1200), "BatchWriteItem")
cylinder((90, 1220, 590, 1330), "DynamoDB", "clave (series, ts_ms)")
arrow((680, 1275), (590, 1275), dashed=True)
box((680, 1220, 1000, 1330), "loadtest/", "Query por serie")

image.save(OUT, dpi=(300, 300))
print(f"Figura guardada en {OUT} ({WIDTH}x{HEIGHT})")
