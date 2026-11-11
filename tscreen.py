"""Driver mínimo para Turzx/Turing Smart Screen 3.5" (rev A, CH572, USB serial).

Protocolo: comandos de 6 bytes com coordenadas de 10 bits empacotadas:
  byte0 = x >> 2
  byte1 = ((x & 3) << 6) | (y >> 4)
  byte2 = ((y & 0xF) << 4) | (ex >> 6)
  byte3 = ((ex & 0x3F) << 2) | (ey >> 8)
  byte4 = ey & 0xFF
  byte5 = comando
Dados de imagem: RGB565 little-endian, linha a linha.
Referência: github.com/mathoudebine/turing-smart-screen-python (lcd_comm_rev_a.py)

Display nativo: 320x480 (retrato). Usamos paisagem 480x320 via SET_ORIENTATION.
"""
import os
import time
import serial
from PIL import Image

RESET = 101
CLEAR = 102
SCREEN_OFF = 108
SCREEN_ON = 109
SET_BRIGHTNESS = 110
SET_ORIENTATION = 121
DISPLAY_BITMAP = 197
HELLO = 69

# SET_ORIENTATION valores (rev A): 0=retrato, 1=paisagem, 2=retrato inv., 3=paisagem inv.
ORIENT_LANDSCAPE = 1  # 1 = paisagem nativa; rotação 180° feita em software

def find_turzx_port(preferred="/dev/ttyCH572"):
    """Localiza a porta da tela Turzx pelo VID do dispositivo (1a86 = QinHeng/CH572).
    Usa o symlink estável quando existe; senão varre os ttyACM* via sysfs.
    Imune a renumeração quando outros USB-CDC-ACM (ex.: rádio TYT 2e3c:9900)
    são conectados antes da tela."""
    if os.path.exists(preferred):
        return preferred
    import glob
    for entry in sorted(glob.glob("/sys/class/tty/ttyACM*")):
        try:
            dev = os.path.realpath(os.path.join(entry, "device"))
            parent = os.path.dirname(dev)
            vid = open(os.path.join(parent, "idVendor")).read().strip().lower()
            if vid == "1a86":
                return "/dev/" + os.path.basename(entry)
        except OSError:
            continue
    return preferred


class TScreen:
    def __init__(self, port=None, baud=115200, timeout=2):
        self.port = port or find_turzx_port()
        self.baud = baud
        self.timeout = timeout
        self.width = 320
        self.height = 480
        self.ser = None
        self._open()

    def _resolve_devnum(self):
        """Numero do dispositivo USB atras de self.port (muda a cada re-enumeracao)."""
        try:
            tty = os.path.basename(os.path.realpath(self.port))
            p = os.path.realpath(os.path.join("/sys/class/tty", tty, "device"))
            for _ in range(5):
                cand = os.path.join(p, "devnum")
                if os.path.isfile(cand):
                    with open(cand) as f:
                        return f.read().strip()
                p = os.path.dirname(p)
        except OSError:
            pass
        return None

    def _open(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        self.ser.dtr = True
        self.ser.rts = False
        self._devnum = self._resolve_devnum()

    def check_alive(self):
        """False se o dispositivo USB re-enumerou (fd obsoleto / tty zumbi)."""
        if self.ser is None or not self.ser.is_open:
            return False
        cur = self._resolve_devnum()
        return cur is not None and cur == self._devnum

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def reconnect(self, attempts=120, delay=0.5):
        """Fecha a serial e espera a porta reaparecer (tela religada).
        Tenta por até ~60s."""
        self.close()
        self.ser = None  # estado limpo do objeto serial
        for i in range(attempts):
            if os.path.exists(self.port):
                try:
                    time.sleep(0.5)  # deixa o kernel estabilizar o device
                    self._open()
                    # handshake completo após religar
                    self.screen_on()
                    self.set_brightness(getattr(self, "_brightness", 80))
                    self.set_orientation(getattr(self, "_orientation", 1))
                    print(f"[reconnect] OK na tentativa {i+1}")
                    return True
                except (serial.SerialException, OSError, PermissionError) as e:
                    if i % 20 == 0:
                        print(f"[reconnect] tentativa {i+1}: {type(e).__name__}: {e}")
            time.sleep(delay)
        print("[reconnect] FALHOU após tentativas")
        return False

    def _send(self, cmd, x=0, y=0, ex=0, ey=0):
        buf = bytearray(6)
        buf[0] = x >> 2
        buf[1] = ((x & 3) << 6) | (y >> 4)
        buf[2] = ((y & 0xF) << 4) | (ex >> 6)
        buf[3] = ((ex & 0x3F) << 2) | (ey >> 8)
        buf[4] = ey & 0xFF
        buf[5] = cmd
        self.ser.write(buf)

    def _write_data(self, data: bytes):
        # Múltiplos do "display width" (protocolo rev A)
        self.ser.write(data)

    def hello(self):
        """Identifica o modelo. UsbMonitor responde; Turing 3.5 não responde."""
        self.ser.write(bytearray([HELLO] * 6))
        resp = self.ser.read(6)
        self.ser.reset_input_buffer()
        modelos = {
            bytes([1] * 6): "USBMONITOR_3_5 (320x480)",
            bytes([2] * 6): "USBMONITOR_5 (480x800)",
            bytes([3] * 6): "USBMONITOR_7 (600x1024)",
        }
        return modelos.get(resp, "TURING/TURZX 3.5 (320x480, sem resposta ao HELLO)")

    def clear(self):
        self._send(CLEAR)

    def screen_on(self):
        self._send(SCREEN_ON)

    def screen_off(self):
        self._send(SCREEN_OFF)

    def set_brightness(self, level=100):
        """level 0-100."""
        self._brightness = level
        level = max(0, min(100, level))
        absolute = int(255 * level / 100)
        self._send(SET_BRIGHTNESS, absolute)

    def set_orientation(self, orientation=ORIENT_LANDSCAPE):
        self._orientation = orientation
        self._send(SET_ORIENTATION, orientation)
        if orientation % 2 == 1:
            self.width, self.height = 480, 320
        else:
            self.width, self.height = 320, 480

    def display_image(self, image: Image.Image, x=0, y=0, rotate=0):
        """Envia uma PIL Image (qualquer modo; convertida p/ RGB) p/ a tela.
        rotate: 0 ou 180 graus (feito em software antes de enviar)."""
        if not self.check_alive():
            # tty zumbi: device re-enumerou, o fd atual escreve para o vazio
            raise serial.SerialException(
                "dispositivo USB re-enumerado (descritor obsoleto)")
        if rotate == 180:
            image = image.rotate(180)
        image = image.convert("RGB")
        if x + image.width > self.width or y + image.height > self.height:
            image = image.crop((0, 0, min(image.width, self.width - x),
                                       min(image.height, self.height - y)))
        x1, y1 = x + image.width - 1, y + image.height - 1

        # RGB -> RGB565 little-endian
        data = bytearray()
        px = image.tobytes()
        for i in range(0, len(px), 3):
            r, g, b = px[i], px[i + 1], px[i + 2]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data.append(v & 0xFF)
            data.append(v >> 8)

        self._send(DISPLAY_BITMAP, x, y, x1, y1)
        chunk = self.width * 8
        for off in range(0, len(data), chunk):
            self._write_data(bytes(data[off:off + chunk]))
