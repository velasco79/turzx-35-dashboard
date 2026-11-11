#!/usr/bin/env python3
"""Layout "layout do relógio": relógio digital + CPU + RAM + disco + swap."""
import time
import argparse
from datetime import datetime

import psutil
import serial
from PIL import Image, ImageDraw, ImageFont

from tscreen import TScreen, ORIENT_LANDSCAPE

W, H = 480, 320
BG = (12, 14, 18)
FG = (230, 235, 240)
MUTED = (120, 130, 145)
ACCENT = (64, 190, 255)
BAR_BG = (40, 45, 55)

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def font(size):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()

f_clock = font(64)
f_label = font(20)
f_value = font(22)


def bar(draw, x, y, w, h, pct, color):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=BAR_BG)
    fill_w = int(w * min(pct, 100) / 100)
    if fill_w > 0:
        c = (255, 90, 60) if pct >= 90 else ((255, 190, 40) if pct >= 75 else color)
        draw.rounded_rectangle([x, y, x + max(fill_w, h), y + h], radius=h // 2, fill=c)


def stats_row(draw, y, label, pct, extra=""):
    draw.text((16, y), label, font=f_label, fill=MUTED)
    txt = f"{pct:5.1f}%"
    if extra:
        txt += " " + extra
    tw = draw.textlength(txt, font=f_value)
    draw.text((W - 16 - tw, y - 2), txt, font=f_value, fill=FG)
    bar(draw, 16, y + 26, W - 32, 10, pct, ACCENT)


def fmt_mb_ou_gb(bytes_):
    """Formata em GB com 1 casa decimal."""
    return f"{bytes_ / 2**30:.1f}GB"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

def make_frame(now):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    clock = now.strftime("%H:%M:%S")
    date = f"{DIAS_SEMANA[now.weekday()]} {now.day:02d} {MESES[now.month - 1]} {now.year}"
    tw = d.textlength(clock, font=f_clock)
    d.text(((W - tw) / 2, 18), clock, font=f_clock, fill=FG)
    dw = d.textlength(date, font=f_label)
    d.text(((W - dw) / 2, 92), date, font=f_label, fill=MUTED)

    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    stats_row(d, 130, "CPU", cpu, f"{psutil.cpu_count()} cores")
    stats_row(d, 176, "RAM", mem.percent, f"{fmt_mb_ou_gb(mem.used)}/{fmt_mb_ou_gb(mem.total)}")
    stats_row(d, 222, "SWAP", swap.percent, f"{fmt_mb_ou_gb(swap.used)}/{fmt_mb_ou_gb(swap.total)}")
    stats_row(d, 268, "DISK /", disk.percent, f"{fmt_mb_ou_gb(disk.used)}/{fmt_mb_ou_gb(disk.total)}")
    return img


def connect(port, brightness):
    """Abre a tela e faz o handshake inicial."""
    scr = TScreen(port)
    print("Modelo:", scr.hello())
    scr.screen_on()
    scr.set_brightness(brightness)
    scr.set_orientation(ORIENT_LANDSCAPE)
    print(f"Tela {scr.width}x{scr.height} — enviando dashboard (Ctrl+C para sair)")
    return scr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--brightness", type=int, default=80)
    ap.add_argument("--once", action="store_true", help="Envia um frame e sai")
    args = ap.parse_args()

    psutil.cpu_percent(interval=None)  # primeira amostra (baseline)

    scr = None
    while scr is None:
        try:
            scr = connect(args.port, args.brightness)
        except (serial.SerialException, OSError) as e:
            print(f"Aguardando tela em {args.port}... ({e})")
            import time as _t
            _t.sleep(2)

    try:
        while True:
            try:
                scr.display_image(make_frame(datetime.now()), 0, 0)
            except (serial.SerialException, OSError, serial.SerialTimeoutException) as e:
                print(f"\nConexão perdida ({e}). Aguardando reconexão...")
                if not scr.reconnect():
                    print("Falha ao reconectar. Tentando novamente...")
                else:
                    print("Tela reconectada — dashboard retomado.")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        scr.close()


if __name__ == "__main__":
    main()
