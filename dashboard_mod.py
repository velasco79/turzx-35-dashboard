#!/usr/bin/env python3
"""Layout 'layout das caixas': paineis estilo AIDA64 com medidores circulares (CPU/GPU/RAM).

Replica do tema da foto de referencia (Turzx 480x320 paisagem):
- Paineis com borda cinza clara, fundo escuro
- Medidores circulares com arcos segmentados (cor varia com uso; 3 seg. finais em vermelho)
- CPU | GPU (nucleo + temperatura + clock/memoria)
- RAM | HDD (disco / uso + temp) | NETWORK (download/upload)
- DATE / TIME no canto inferior direito
"""

import time
import math
import argparse
from datetime import datetime

import psutil
import serial
from PIL import Image, ImageDraw, ImageFont

from tscreen import TScreen, ORIENT_LANDSCAPE

W, H = 480, 320
BG = (26, 30, 36)
PANEL_LINE = (150, 158, 168)
FG = (235, 238, 242)
MUTED = (140, 148, 158)
PURPLE = (170, 90, 255)
GREEN = (120, 220, 90)
CYAN = (80, 210, 230)
SEG_BG = (58, 64, 74)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

def font(size):
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()

f_title = font(14)
f_big = font(34)
f_mid = font(15)
f_small = font(12)
f_time = font(26)


def panel(d, box, title):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=6, outline=PANEL_LINE, width=2)
    d.text((x0 + 8, y0 + 5), title, font=f_title, fill=FG)


def cpu_title():
    """Ex: 'CPU: i7-4765T @ 2.00GHz' a partir de /proc/cpuinfo."""
    import re
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    name = re.sub(r"Intel\(R\)\s*", "", name)
                    name = re.sub(r"Core\(TM\)\s*", "", name)
                    name = re.sub(r"\s*CPU\s*", " ", name)
                    name = re.sub(r"\s+", " ", name).strip()
                    return f"CPU: {name}"
    except OSError:
        pass
    return "CPU"

CPU_TITLE = cpu_title()


def center_text(d, cx, y, text, f, fill):
    tw = d.textlength(text, font=f)
    d.text((cx - tw / 2, y), text, font=f, fill=fill)


def gauge(d, cx, cy, r, pct, label, sub):
    """Medidor circular; cor varia com o uso, 3 segmentos finais em vermelho."""
    start, sweep = 135, 270  # arco aberto embaixo
    n = 26
    seg_deg = sweep / n
    lit = int(round(n * min(pct, 100) / 100))
    zone = GREEN if pct < 70 else ((255, 190, 40) if pct < 90 else (255, 90, 60))
    for i in range(n):
        a0 = start + i * seg_deg + 1
        a1 = start + (i + 1) * seg_deg - 1
        if i >= n - 3:
            color = (200, 60, 50) if i < lit else SEG_BG
        elif i < lit:
            color = zone
        else:
            color = SEG_BG
        d.arc([cx - r, cy - r, cx + r, cy + r], a0, a1, fill=color, width=7)
    center_text(d, cx, cy - 20, f"{pct:.0f}", f_big, FG)
    center_text(d, cx, cy + 16, label, f_small, MUTED)
    if sub:
        center_text(d, cx, cy + 30, sub, f_small, MUTED)


def mini_bar(d, x, y, w, h, pct):
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, outline=PANEL_LINE, width=1)
    fill_w = int((w - 4) * min(pct, 100) / 100)
    if fill_w > 0:
        d.rounded_rectangle([x + 2, y + 2, x + 2 + fill_w, y + h - 2],
                            radius=(h - 4) // 2, fill=FG)


_prev_net = None
_prev_net_t = None


def net_rates():
    """Retorna (down_Mbps, up_Mbps) medios desde a ultima chamada."""
    global _prev_net, _prev_net_t
    now = time.time()
    cur = psutil.net_io_counters()
    if _prev_net is None:
        _prev_net, _prev_net_t = cur, now
        return 0.0, 0.0
    dt = max(now - _prev_net_t, 1e-6)
    down = (cur.bytes_recv - _prev_net.bytes_recv) / dt * 8 / 1_000_000
    up = (cur.bytes_sent - _prev_net.bytes_sent) / dt * 8 / 1_000_000
    _prev_net, _prev_net_t = cur, now
    return down, up


def cpu_freq_ghz():
    f = psutil.cpu_freq()
    return f.current / 1000 if f else 0.0


def cpu_temp():
    try:
        temps = psutil.sensors_temperatures()
        for key in ("coretemp", "k10temp", "cpu_thermal", "zenpower"):
            if key in temps and temps[key]:
                return temps[key][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass
    return None


def make_frame(now):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ---- CPU
    panel(d, (6, 6, 236, 154), CPU_TITLE)
    cpu = psutil.cpu_percent(interval=None)
    t = cpu_temp()
    gauge(d, 84, 92, 52, cpu, "CPU %", None)
    d.text((146, 56), "TEMP", font=f_small, fill=MUTED)
    d.text((146, 70), f"{t:.0f}°" if t is not None else "--", font=f_mid, fill=FG)
    if t is not None:
        # alinha a barra entre o valor de temperatura e a borda direita do painel (x=236)
        bar_x = 186
        bar_w = 236 - 8 - bar_x  # 228 - 186 = 42, com margem de 8px da borda
        mini_bar(d, bar_x, 72, bar_w, 10, min(t, 100))
    d.text((146, 92), "CLK", font=f_small, fill=MUTED)
    d.text((146, 106), f"{cpu_freq_ghz():.2f}GHz", font=f_mid, fill=FG)


    # ---- GPU (sem GPU real: mostra "--")
    panel(d, (244, 6, 474, 154), "GPU")
    gauge(d, 326, 92, 52, 0, "GPU %", None)
    d.text((398, 60), "TEMP", font=f_small, fill=MUTED)
    d.text((443, 60), "--", font=f_mid, fill=FG)
    mini_bar(d, 398, 78, 66, 8, 0)
    d.text((398, 96), "MEM", font=f_small, fill=MUTED)
    d.text((430, 96), "--", font=f_mid, fill=FG)

    # ---- RAM
    panel(d, (6, 162, 168, 314), "RAM")
    mem = psutil.virtual_memory()
    gauge(d, 87, 236, 46, mem.percent, "RAM %", None)
    center_text(d, 87, 290, f"{mem.used / 2**30:.1f}/{mem.total / 2**30:.1f} GB",
                f_small, MUTED)

    # ---- HDD + NETWORK
    panel(d, (176, 162, 304, 314), "HDD")
    disk = psutil.disk_usage("/")
    d.text((190, 196), "/", font=f_mid, fill=FG)
    mini_bar(d, 210, 196, 78, 10, disk.percent)
    d.text((196, 214), f"{disk.percent:.0f} %", font=f_mid, fill=FG)
    d.text((196, 230), f"{disk.used / 2**30:.0f}/{disk.total / 2**30:.0f}GB",
           font=f_small, fill=MUTED)
    d.text((196, 246), "NETWORK", font=f_title, fill=FG)
    down, up = net_rates()
    d.text((186, 268), f"\u2193{down:6.2f}", font=f_small, fill=CYAN)
    d.text((242, 268), f"\u2191{up:6.2f}", font=f_small, fill=CYAN)
    d.text((186, 286), "NET Mbps", font=f_small, fill=MUTED)

    # ---- DATE / TIME
    panel(d, (312, 162, 474, 314), "DATE")
    d.text((326, 196), f"{now.day:02d}/{now.month:02d}/{now.year}", font=f_mid, fill=FG)
    d.text((326, 224), "TIME", font=f_title, fill=FG)
    d.text((326, 250), now.strftime("%H:%M"), font=f_time, fill=FG)

    return img


def connect(port, brightness):
    scr = TScreen(port)
    print("Modelo:", scr.hello())
    scr.screen_on()
    scr.set_brightness(brightness)
    scr.set_orientation(ORIENT_LANDSCAPE)
    print(f"Tela {scr.width}x{scr.height} - layout das caixas (Ctrl+C para sair)")
    return scr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
                    help="porta serial da tela (default: auto-detecta por VID 1a86)")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--brightness", type=int, default=80)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    psutil.cpu_percent(interval=None)

    scr = None
    while scr is None:
        try:
            scr = connect(args.port, args.brightness)
        except (serial.SerialException, OSError) as e:
            print(f"Aguardando tela em {args.port or '(auto-detect)'}... ({e})")
            time.sleep(2)

    try:
        while True:
            try:
                scr.display_image(make_frame(datetime.now()), 0, 0, rotate=180)
            except (serial.SerialException, OSError, serial.SerialTimeoutException) as e:
                print(f"\nConexao perdida ({e}). Aguardando reconexao...")
                if not scr.reconnect():
                    print("Falha ao reconectar. Tentando novamente...")
                else:
                    print("Tela reconectada - dashboard retomado.")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        scr.close()


if __name__ == "__main__":
    main()
