#!/usr/bin/env python3
"""Example: write received Meshtastic text messages to the dashboard inbox JSONL.

Run this on the machine that OWNS the Meshtastic radio (the serial port is
exclusive — only one process can hold it). The dashboard (dashboard_pages.py)
reads the SAME JSONL file. Point both at the same path with the MESHBOT_INBOX
environment variable.

    pip install meshtastic
    MESHBOT_INBOX=~/turzx-dashboard/mesh_inbox.jsonl python meshtastic_inbox_example.py

Each received text message becomes one JSON line:
    {"ts":..., "hora":"HH:MM", "de":"<node name>", "canal":"<channel|PRIVADO>",
     "privado":bool, "cripto":bool, "texto":"..."}

`cripto` is true only when the message is really encrypted: a public-key PM
(pki_encrypted) or a channel whose PSK is a custom key. The default LongFast
key and unencrypted channels are NOT flagged, so the dashboard shows no padlock.
"""

import json
import os
import time
from datetime import datetime

from meshtastic.serial_interface import SerialInterface
from meshtastic.util import pskToString
from pubsub import pub

INBOX = os.path.expanduser(
    os.environ.get("MESHBOT_INBOX", "~/turzx-dashboard/mesh_inbox.jsonl")
)

iface = None  # set in main()


def node_name(num):
    for attr in ("nodesByNum", "nodes"):
        d = getattr(iface, attr, None)
        if isinstance(d, dict):
            u = (d.get(num) or {}).get("user") or {}
            n = u.get("longName") or u.get("shortName")
            if n:
                return n
    return f"!{num:08x}"


def channel_info(packet):
    idx = packet.get("channel", 0)
    privado = packet.get("to") == iface.myInfo.my_node_num
    nome, cripto = None, False
    try:
        ch = iface.localNode.channels[idx]
        nome = (ch.settings.name or "").strip()
        cripto = pskToString(ch.settings.psk) not in ("unencrypted", "default")
    except Exception:
        pass
    if privado:
        nome = "PRIVADO"
        cripto = bool(packet.get("pki_encrypted")) or cripto
    if not nome:
        nome = "LongFast" if idx == 0 else f"canal{idx}"
    return nome, privado, cripto


def on_packet(packet, interface=None):
    try:
        decoded = packet.get("decoded") or {}
        if decoded.get("portnum") != "TEXT_MESSAGE_APP":
            return
        frm = packet.get("from")
        if not frm or frm == iface.myInfo.my_node_num:
            return
        texto = decoded.get("text")
        if not texto:
            return
        nome, privado, cripto = channel_info(packet)
        rec = {
            "ts": time.time(),
            "hora": datetime.now().strftime("%H:%M"),
            "de": node_name(frm),
            "canal": nome,
            "privado": privado,
            "cripto": cripto,
            "texto": texto,
        }
        os.makedirs(os.path.dirname(INBOX), exist_ok=True)
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # never crash on a dashboard write
        print("inbox write failed:", e)


def main():
    global iface
    iface = SerialInterface()  # or SerialInterface(devPath="/dev/ttyUSB0")
    pub.subscribe(on_packet, "meshtastic.receive")
    print("Gravando mensagens em", INBOX, "— Ctrl+C para sair")
    try:
        while True:
            time.sleep(1)
    finally:
        iface.close()


if __name__ == "__main__":
    main()
