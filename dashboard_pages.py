#!/usr/bin/env python3
"""Paginacao do dashboard Turzx: pagina principal (layout 02 'grid neon') +
pagina Meshtastic. Ao chegar mensagem nova no inbox do MeshBot, troca para a
pagina Meshtastic por 60s (timer reinicia a cada mensagem nova) e volta sozinha.

Layout Meshtastic (aprovado): titulo 'Meshtastic' no cabecalho; caixa unica com
mensagens empilhadas (mais recente embaixo); cada linha traz
'REMETENTE · HORA  [canal]' (canal em ciano) ou '[PRIVADO]' (ambar) + cadeado
desenhado SO quando ha criptografia real (PKC ou PSK custom); a mais recente
ganha fundo verde + rotulo 'NOVA!' em verde.
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime

import psutil
import serial

from PIL import Image, ImageDraw

from tscreen import TScreen, ORIENT_LANDSCAPE
from dashboard_grid import (
    W, H, BG, BORDER, FG, MUTED, CYAN, GREEN,
    f_hdr, font, box, make_frame,
)

AMBER = (255, 180, 84)
NOVA_BG = (15, 42, 28)
SEP = (26, 42, 60)

f_m_title = font(14)
f_m_hdr = font(12)
f_m_text = font(15)
HDR_H = 16   # altura da linha de cabecalho (remetente · hora [canal])
TXT_H = 18   # altura de cada linha do corpo da mensagem

MESH_TIMEOUT = 60.0
INBOX = os.path.expanduser(os.environ.get(
    "MESHBOT_INBOX", "~/turzx-dashboard/mesh_inbox.jsonl"
))


def monitor_em_descanso() -> bool:
    """True quando o KDE ativou o descanso de tela / DPMS (monitor apagado).

    Usa org.freedesktop.ScreenSaver.GetActive (fornecido pelo kwin_wayland no
    Wayland). Falha de consulta => False (fail-safe: mantem a telinha ligada).
    """
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.freedesktop.ScreenSaver",
             "/ScreenSaver", "org.freedesktop.ScreenSaver", "GetActive"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return False
    return "true" in out.lower()


class Inbox:
    """Leitor incremental do JSONL do MeshBot (resistente a rotacao/truncacao)."""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.msgs = []
        self.last_ts = 0.0

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return False
        if size < self.pos:  # arquivo rotacionado -> re-le do inicio
            self.pos, self.msgs = 0, []
        if size == self.pos:
            return False
        try:
            with open(self.path, encoding="utf-8") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except OSError:
            return False
        novos = 0
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("ts", 0.0)
            if ts > self.last_ts:
                novos += 1
            self.last_ts = max(self.last_ts, ts)
            self.msgs.append(rec)
        self.msgs = self.msgs[-50:]
        return novos > 0


def _wrap(d, texto, fnt, maxw, maxlinhas=4):
    linhas = []
    for par in str(texto).split("\n"):
        atual = ""
        for w in par.split():
            t = f"{atual} {w}".strip()
            if d.textlength(t, fnt) <= maxw or not atual:
                atual = t
            else:
                linhas.append(atual)
                atual = w
        linhas.append(atual)
    linhas = linhas or [""]
    if len(linhas) > maxlinhas:  # nunca deixa 1 msg enorme engolir as demais
        linhas = linhas[:maxlinhas]
        linhas[-1] = linhas[-1].rstrip(" ,") + "…"
    return linhas


def _lock(d, x, y, col):
    """Cadeado desenhado (DejaVu nao tem emoji): corpo + arco."""
    d.rounded_rectangle([x, y + 3, x + 9, y + 11], radius=1.5, fill=col)
    d.arc([x + 1.5, y - 2, x + 7.5, y + 6], 180, 360, fill=col, width=2)


def _medir(d, rec):
    cab = f"{rec.get('de','?')} · {rec.get('hora','')}"
    tag = f"[{rec.get('canal','?')}]"
    corpo = _wrap(d, rec.get("texto", ""), f_m_text, W - 32)
    return cab, tag, corpo


def make_mesh_frame(now, msgs, restante):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # cabecalho: hora | Meshtastic | contagem de retorno
    d.text((8, 3), now.strftime("%H:%M:%S"), font=f_hdr, fill=FG)
    tw = d.textlength("Meshtastic", font=f_m_title)
    d.text(((W - tw) / 2, 2), "Meshtastic", font=f_m_title, fill=CYAN)
    rt = f"↩ 0:{int(max(0, restante)):02d}"
    d.text((W - 8, 3), rt, font=f_hdr, fill=MUTED, anchor="ra")

    box(d, (6, 24, W - 6, H - 6))

    if not msgs:
        d.text((W / 2, 160), "sem mensagens", font=f_m_text, fill=MUTED, anchor="ma")
        return img

    # monta blocos da mais recente para tras ate caber; desenha topo->baixo
    y_bot = H - 16
    blocos = []  # (rec, cab, tag, corpo, h)
    y = y_bot
    for i in range(len(msgs) - 1, -1, -1):
        rec = msgs[i]
        cab, tag, corpo = _medir(d, rec)
        h = HDR_H + len(corpo) * TXT_H + 6
        if y - h < 30 and blocos:
            break
        y -= h
        blocos.append((rec, cab, tag, corpo, h))
    blocos.reverse()

    # empilha do TOPO para baixo (mais recentes embaixo); quando enche, as
    # antigas rolam para fora e a nova fica encostada na base da caixa.
    y = 30
    n = len(blocos)
    for idx, (rec, cab, tag, corpo, h) in enumerate(blocos):
        nova = idx == n - 1
        privado = rec.get("privado")
        cripto = rec.get("cripto")
        if nova:
            d.rounded_rectangle((10, y - 2, W - 10, y + h - 8), radius=4, fill=NOVA_BG)
        # linha de cabecalho: REMETENTE · HORA  [canal] <cadeado> NOVA!
        cx = 16
        cor_cab = GREEN if nova else MUTED
        d.text((cx, y), cab, font=f_m_hdr, fill=cor_cab)
        cx += d.textlength(cab, f_m_hdr) + 6
        d.text((cx, y), tag, font=f_m_hdr, fill=AMBER if privado else CYAN)
        cx += d.textlength(tag, f_m_hdr)
        if cripto:
            cx += 3
            _lock(d, cx, y - 1, AMBER)
            cx += 13
        if nova:
            cx += 4
            d.text((cx, y), "NOVA!", font=f_m_hdr, fill=GREEN)
        y += HDR_H
        for ln in corpo:
            d.text((16, y), ln, font=f_m_text, fill=FG)
            y += TXT_H
        if not nova:
            d.line((12, y + 2, W - 12, y + 2), fill=SEP)
        y += 6
    return img


def connect(port, brightness):
    scr = TScreen(port)
    print("Modelo:", scr.hello())
    scr.screen_on()
    scr.set_brightness(brightness)
    scr.set_orientation(ORIENT_LANDSCAPE)
    print(f"Tela {scr.width}x{scr.height} - paginacao principal+Meshtastic (Ctrl+C sai)")
    return scr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--brightness", type=int, default=80)
    ap.add_argument("--inbox", default=INBOX)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-dpms", action="store_true",
                    help="nao espelhar o descanso de tela do monitor")
    args = ap.parse_args()

    psutil.cpu_percent(interval=None, percpu=True)
    inbox = Inbox(args.inbox)

    scr = None
    while scr is None:
        try:
            scr = connect(args.port, args.brightness)
        except (serial.SerialException, OSError) as e:
            print(f"Aguardando tela em {args.port or '(auto-detect)'}... ({e})")
            time.sleep(2)

    mesh_ate = 0.0
    tela_apagada = False
    ultimo_dpms = 0.0
    try:
        while True:
            try:
                # espelha o descanso de tela do monitor (consulta a cada ~3s)
                if not args.no_dpms and time.time() - ultimo_dpms >= 3:
                    ultimo_dpms = time.time()
                    dormindo = monitor_em_descanso()
                    if dormindo and not tela_apagada:
                        scr.screen_off()
                        tela_apagada = True
                        print("Monitor em descanso - telinha desligada")
                    elif not dormindo and tela_apagada:
                        scr.screen_on()
                        tela_apagada = False
                        print("Monitor acordou - telinha religada")
                if tela_apagada:
                    time.sleep(2)  # monitor apagado: nao gasta renderizando
                    continue

                agora = datetime.now()
                principal = make_frame(agora)  # amostra metricas sempre (historico contínuo)
                if inbox.poll():
                    mesh_ate = time.time() + MESH_TIMEOUT
                restante = mesh_ate - time.time()
                frame = (
                    make_mesh_frame(agora, inbox.msgs, restante)
                    if restante > 0
                    else principal
                )
                scr.display_image(frame, 0, 0, rotate=180)
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
