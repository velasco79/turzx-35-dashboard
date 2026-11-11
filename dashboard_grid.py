#!/usr/bin/env python3
"""Layout 02 'grid neon': grade cyber/neon com gauge dentro da propria caixa.

Cabecalho (hora/data/uptime) > 4 colunas (cada uma: titulo + gauge circular +
linhas de info + barra) > 2 graficos de linha (cores CPU + rede).
Sem rotulo repetido embaixo do gauge: o titulo da caixa rotula o gauge.
"""

import time
import re
import argparse
from collections import deque
from datetime import datetime

import psutil
import serial
from PIL import Image, ImageDraw, ImageFont

from tscreen import TScreen, ORIENT_LANDSCAPE

W, H = 480, 320
BG = (9, 16, 30)
BORDER = (45, 75, 110)
FG = (228, 238, 250)
MUTED = (110, 132, 160)
CYAN = (60, 220, 255)
GREEN = (80, 230, 130)
YELLOW = (255, 205, 70)
RED = (255, 95, 95)
TEAL = (60, 220, 200)
PURPLE = (180, 115, 255)
MAGENTA = (255, 110, 200)
BLUE = (95, 140, 255)
TRACK = (30, 45, 68)

CORE_COLORS = [CYAN, GREEN, YELLOW, PURPLE, MAGENTA, BLUE, TEAL, RED]
HIST_N = 60  # amostras (1/s) nos graficos

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

f_hdr = font(12)
f_gnum = font(24)
f_pct = font(12)
f_title = font(12)
f_iline = font(11)
f_ctitle = font(11)
f_leg = font(10)
f_legnum = font(13)

# faixas de cor do arco (por posicao ao longo do gauge)
GREEN_MAX, YELLOW_MAX = 50.0, 80.0


def zone(pct):
    return GREEN if pct < 70 else (YELLOW if pct < 90 else RED)


def box(d, rect):
    d.rounded_rectangle(rect, radius=5, outline=BORDER, width=1)


def cpu_model_short():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    m = re.search(r"(i[3579]-\S+)", name)
                    if m:
                        return m.group(1)
                    return name.split(" CPU")[0].replace("Intel(R)", "").strip()[:14]
    except OSError:
        pass
    return "CPU"

CPU_MODEL = cpu_model_short()
NPROC = psutil.cpu_count() or 4


def gauge(d, cx, cy, r, pct):
    """Arco colorido por posicao: verde ate 50%, amarelo ate 80%, vermelho depois.
    Inicio embaixo (6h), sentido horario."""
    pct = max(0.0, min(100.0, pct))
    ring = [cx - r, cy - r, cx + r, cy + r]
    d.ellipse(ring, outline=TRACK, width=6)

    def ang(v):
        return 90 + 3.6 * v  # graus, a partir de baixo, horario

    if pct > 0:
        d.arc(ring, ang(0), ang(min(pct, GREEN_MAX)), fill=GREEN, width=6)
        if pct > GREEN_MAX:
            d.arc(ring, ang(GREEN_MAX), ang(min(pct, YELLOW_MAX)), fill=YELLOW, width=6)
        if pct > YELLOW_MAX:
            d.arc(ring, ang(YELLOW_MAX), ang(pct), fill=RED, width=6)
    num = f"{pct:.0f}"
    nw = d.textlength(num, font=f_gnum)
    pw = d.textlength("%", font=f_pct)
    x = cx - (nw + pw + 1) / 2
    d.text((x, cy - 15), num, font=f_gnum, fill=FG)
    d.text((x + nw + 1, cy - 10), "%", font=f_pct, fill=MUTED)


def column(d, rect, title, pct, lines):
    """Caixa com titulo + gauge (sem barra repetida) + linhas de info."""
    x0, y0, x1, y1 = rect
    box(d, rect)
    tw = d.textlength(title, font=f_title)
    d.text(((x0 + x1 - tw) / 2, y0 + 4), title, font=f_title, fill=CYAN)
    cx = (x0 + x1) / 2
    gauge(d, cx, y0 + 74, 40, pct)
    ly = y0 + 124
    for k, v in lines:
        d.text((x0 + 6, ly), k, font=f_iline, fill=MUTED)
        d.text((x1 - 6, ly), v, font=f_iline, fill=FG, anchor="ra")
        ly += 15


def line_chart(d, rect, series, colors, ymax=100.0):
    x0, y0, x1, y1 = rect
    n = max(len(s) for s in series)
    if n < 2:
        return
    for g in (25, 50, 75):
        gy = y1 - (y1 - y0) * g / 100
        d.line([x0, gy, x1, gy], fill=TRACK)
    for s, col in zip(series, colors):
        pts = [(x0 + (x1 - x0) * i / (n - 1),
                y1 - (y1 - y0) * min(v, ymax) / ymax)
               for i, v in enumerate(s)]
        d.line(pts, fill=col, width=1)


_prev_net = None
_prev_net_t = None


def net_rates():
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


def uptime_str():
    secs = int(time.time() - psutil.boot_time())
    d, rem = divmod(secs, 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h:02d}:{m:02d}"


CORES_H = deque(maxlen=HIST_N)
NET_H = deque(maxlen=HIST_N)

GB = 2**30


def make_frame(now):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    percpu = psutil.cpu_percent(interval=None, percpu=True)
    cpu = sum(percpu) / len(percpu)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    down, up = net_rates()
    t = cpu_temp()
    CORES_H.append(list(percpu))
    NET_H.append((down, up))

    # ---- cabecalho
    tstr = now.strftime("%H:%M:%S")
    dstr = now.strftime("%d/%m/%Y")
    ustr = "Up " + uptime_str()
    d.text((8, 3), tstr, font=f_hdr, fill=FG)
    tw = d.textlength(dstr, font=f_hdr)
    d.text(((W - tw) / 2, 3), dstr, font=f_hdr, fill=FG)
    tw = d.textlength(ustr, font=f_hdr)
    d.text((W - 8 - tw, 3), ustr, font=f_hdr, fill=MUTED)

    # ---- 4 colunas (gauge dentro da caixa)
    y0, y1 = 20, 200
    gap, m = 4, 4
    cw = (W - 2 * m - 3 * gap) / 4
    cols = [
        ("CPU", cpu, [("Modelo", CPU_MODEL), ("Freq", f"{cpu_freq_ghz():.2f}GHz"),
                      ("Temp", f"{t:.0f}°C" if t is not None else "--"),
                      ("Núc", str(NPROC))]),
        ("RAM", mem.percent, [("Total", f"{mem.total/GB:.0f}GB"),
                              ("Usado", f"{mem.used/GB:.1f}GB"),
                              ("Livre", f"{mem.available/GB:.1f}GB")]),
        ("SWAP", swap.percent, [("Total", f"{swap.total/GB:.0f}GB"),
                                ("Usado", f"{swap.used/GB:.1f}GB"),
                                ("Livre", f"{swap.free/GB:.1f}GB")]),
        ("DISCO", disk.percent, [("Total", f"{disk.total/GB:.0f}GB"),
                                 ("Usado", f"{disk.used/GB:.0f}GB"),
                                 ("Livre", f"{disk.free/GB:.0f}GB")]),
    ]
    for i, (title, pct, lines) in enumerate(cols):
        x0 = m + i * (cw + gap)
        column(d, (x0, y0, x0 + cw, y1), title, pct, lines)

    # ---- 2 graficos
    cy0, cy1 = 206, 314
    box(d, (4, cy0, 238, cy1))
    d.text((10, cy0 + 3), "NÚCLEOS", font=f_ctitle, fill=CYAN)
    series = [[s[i] for s in CORES_H] for i in range(NPROC)]
    line_chart(d, (10, cy0 + 20, 232, cy1 - 10), series, CORE_COLORS[:NPROC], 100)

    box(d, (242, cy0, 476, cy1))
    d.text((248, cy0 + 3), "REDE", font=f_ctitle, fill=CYAN)
    downs = [v[0] for v in NET_H]
    ups = [v[1] for v in NET_H]
    ymax = max(1.0, max(downs + ups, default=1.0))
    line_chart(d, (248, cy0 + 20, 392, cy1 - 10), [downs, ups], [CYAN, GREEN], ymax)
    d.text((470, cy0 + 22), f"↓{down:.2f}", font=f_legnum, fill=CYAN, anchor="ra")
    d.text((470, cy0 + 42), f"↑{up:.2f}", font=f_legnum, fill=GREEN, anchor="ra")
    d.text((470, cy0 + 60), "Mbps", font=f_leg, fill=MUTED, anchor="ra")

    return img


def connect(port, brightness):
    scr = TScreen(port)
    print("Modelo:", scr.hello())
    scr.screen_on()
    scr.set_brightness(brightness)
    scr.set_orientation(ORIENT_LANDSCAPE)
    print(f"Tela {scr.width}x{scr.height} - layout 02 grid (Ctrl+C para sair)")
    return scr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--brightness", type=int, default=80)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    psutil.cpu_percent(interval=None, percpu=True)

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
