#!/usr/bin/env python3

import argparse
import datetime
import sys
from dataclasses import dataclass

import serial
from serial.tools import list_ports

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow

VERSION = "2.0"
DEFAULT_PORT = "/dev/ttyUSB0"
BAUDRATE = 38400
TIMEOUT = 0.01

WIDTH = 128
HEIGHT = 64
FRAME_SIZE = 1024

HEADER = b"\xAA\x55"
TYPE_SCREENSHOT = 0x01
TYPE_DIFF = 0x02
TYPE_KEY = 0x03
TYPE_KEY_LONG = 0x04
KEEPALIVE = b"\x55\xAA\x00\x00"


@dataclass(frozen=True)
class ColorScheme:
    fg: tuple[int, int, int]
    bg: tuple[int, int, int]


COLOR_SETS = {
    "g": ColorScheme((0, 0, 0), (202, 202, 202)),
    "o": ColorScheme((0, 0, 0), (255, 193, 37)),
    "b": ColorScheme((0, 0, 0), (28, 134, 228)),
    "w": ColorScheme((0, 0, 0), (255, 255, 255)),
}

KEYCODES = {
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "MENU": 10,
    "UP": 11,
    "DOWN": 12,
    "EXIT": 13,
    "STAR": 14,
    "F": 15,
    "SIDE2": 17,
    "SIDE1": 18,
}

KEY_BINDINGS = {
    Qt.Key.Key_0: KEYCODES["0"],
    Qt.Key.Key_1: KEYCODES["1"],
    Qt.Key.Key_2: KEYCODES["2"],
    Qt.Key.Key_3: KEYCODES["3"],
    Qt.Key.Key_4: KEYCODES["4"],
    Qt.Key.Key_5: KEYCODES["5"],
    Qt.Key.Key_6: KEYCODES["6"],
    Qt.Key.Key_7: KEYCODES["7"],
    Qt.Key.Key_8: KEYCODES["8"],
    Qt.Key.Key_9: KEYCODES["9"],
    Qt.Key.Key_A: KEYCODES["MENU"],
    Qt.Key.Key_Up: KEYCODES["UP"],
    Qt.Key.Key_Down: KEYCODES["DOWN"],
    Qt.Key.Key_Z: KEYCODES["EXIT"],
    Qt.Key.Key_X: KEYCODES["STAR"],
    Qt.Key.Key_Asterisk: KEYCODES["STAR"],
    Qt.Key.Key_C: KEYCODES["F"],
    Qt.Key.Key_F1: KEYCODES["SIDE1"],
    Qt.Key.Key_F2: KEYCODES["SIDE2"],
}


class FrameParser:
    def __init__(self):
        self.rx = bytearray()
        self.framebuffer = bytearray([0] * FRAME_SIZE)

    def feed(self, data: bytes) -> bool:
        if not data:
            return False
        self.rx.extend(data)
        updated = False

        while True:
            header_index = self.rx.find(HEADER)
            if header_index < 0:
                if len(self.rx) > 3:
                    del self.rx[:-3]
                break

            if header_index > 0:
                del self.rx[:header_index]

            if len(self.rx) < 5:
                break

            pkt_type = self.rx[2]
            size = int.from_bytes(self.rx[3:5], "big")
            total = 5 + size
            if len(self.rx) < total:
                break

            payload = self.rx[5:total]
            del self.rx[:total]

            if pkt_type == TYPE_SCREENSHOT and size == FRAME_SIZE:
                self.framebuffer[:] = payload
                updated = True
            elif pkt_type == TYPE_DIFF and size % 9 == 0:
                self.apply_diff(payload)
                updated = True

        return updated

    def apply_diff(self, payload: bytes):
        i = 0
        while i + 9 <= len(payload):
            block = payload[i]
            i += 1
            if block >= 128:
                break
            self.framebuffer[block * 8:block * 8 + 8] = payload[i:i + 8]
            i += 8


class CombinedWindow(QMainWindow):
    def __init__(self, ser: serial.Serial):
        super().__init__()
        self.ser = ser
        self.parser = FrameParser()

        self.scale = 5
        self.pixel_lcd = False
        self.inverted = False
        self.color_key = "g"

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(self.label)

        self.setWindowTitle(f"Quansheng K5 Combined Viewer/Remote v{VERSION} – No data")
        self._resize_window()

        self.frames = 0
        self.last_fps_ms = 0
        self.no_data_count = 0

        self.serial_timer = QTimer(self)
        self.serial_timer.timeout.connect(self.poll_serial)
        self.serial_timer.start(20)

        self.keepalive_timer = QTimer(self)
        self.keepalive_timer.timeout.connect(self.send_keepalive)
        self.keepalive_timer.start(200)

        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self.update_fps_title)
        self.fps_timer.start(1000)

    def _resize_window(self):
        w = WIDTH * (self.scale - 1)
        h = HEIGHT * self.scale
        self.setMinimumSize(w, h)
        self.resize(w, h)

    def send_keepalive(self):
        try:
            self.ser.write(KEEPALIVE)
        except serial.SerialException:
            pass

    def send_radio_key(self, keycode: int, long_press: bool):
        pkt_type = TYPE_KEY_LONG if long_press else TYPE_KEY
        packet = HEADER + bytes([pkt_type, keycode])
        try:
            self.ser.write(packet)
        except serial.SerialException:
            pass

    def render_frame(self):
        scheme = COLOR_SETS[self.color_key]
        fg = scheme.fg
        bg = scheme.bg
        if self.inverted:
            fg, bg = bg, fg

        image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB888)
        image.fill(Qt.GlobalColor.black)

        for y in range(HEIGHT):
            for x in range(WIDTH):
                bit_index = y * WIDTH + x
                byte_idx = bit_index // 8
                bit_pos = bit_index % 8
                pixel_on = (self.parser.framebuffer[byte_idx] >> bit_pos) & 0x01
                r, g, b = fg if pixel_on else bg
                image.setPixel(x, y, (r << 16) | (g << 8) | b)

        scaled = image.scaled(
            WIDTH * (self.scale - 1),
            HEIGHT * self.scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

        self.label.setPixmap(QPixmap.fromImage(scaled))

    def save_screenshot(self):
        pixmap = self.label.pixmap()
        if not pixmap:
            return
        filename = datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
        pixmap.save(filename)
        print(f"[✔] Screenshot saved: {filename}")

    def poll_serial(self):
        try:
            data = self.ser.read(4096)
        except serial.SerialException:
            print("[!] Serial read failed (port likely used by another app).")
            QApplication.quit()
            return

        updated = self.parser.feed(data)
        if updated:
            self.render_frame()
            self.frames += 1
            self.no_data_count = 0
        else:
            self.no_data_count = min(self.no_data_count + 1, 5)
            if self.no_data_count == 5:
                self.setWindowTitle(f"Quansheng K5 Combined Viewer/Remote v{VERSION} – No data")

    def update_fps_title(self):
        fps = self.frames
        self.frames = 0
        self.setWindowTitle(f"Quansheng K5 Combined Viewer/Remote v{VERSION} – FPS: {fps:>04.1f}")

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key == Qt.Key.Key_Q:
            self.close()
            return
        if key == Qt.Key.Key_Space:
            self.save_screenshot()
            return
        if key == Qt.Key.Key_P:
            self.pixel_lcd = not self.pixel_lcd
            self.render_frame()
            return
        if key == Qt.Key.Key_I:
            self.inverted = not self.inverted
            self.render_frame()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            if self.scale < 12:
                self.scale += 1
                self._resize_window()
                self.render_frame()
            return
        if key == Qt.Key.Key_Minus:
            if self.scale > 3:
                self.scale -= 1
                self._resize_window()
                self.render_frame()
            return

        text = event.text().lower()
        if text in COLOR_SETS:
            self.color_key = text
            self.render_frame()
            return

        if key in KEY_BINDINGS:
            long_press = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.send_radio_key(KEY_BINDINGS[key], long_press)
            return

    def closeEvent(self, event):
        try:
            self.ser.close()
        except Exception:
            pass
        super().closeEvent(event)


def cmd_list_ports():
    print("Available ports:")
    for port in list_ports.comports():
        if port.vid is None:
            continue
        description = " - ".join(filter(None, (port.product, port.manufacturer)))
        print(f"- {description} : {port.device}" if description else f"- {port.device}")


def main():
    parser = argparse.ArgumentParser(
        prog="combined.py",
        description="Live display viewer + remote keyboard for UV-K5 with F4HWN firmware (Qt6)",
    )
    parser.add_argument("--list-ports", action="store_true", help="list available ports and exit")
    parser.add_argument("--port", type=str, help="serial port to use (in place of DEFAULT_PORT)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    if args.list_ports:
        cmd_list_ports()
        return

    serial_port = args.port or DEFAULT_PORT
    if not serial_port:
        print("Please specify --port or set DEFAULT_PORT.")
        sys.exit(1)

    try:
        ser = serial.Serial(serial_port, BAUDRATE, timeout=TIMEOUT)
    except serial.SerialException as err:
        print(f"[!] Serial error: {err}")
        sys.exit(1)

    app = QApplication(sys.argv)
    w = CombinedWindow(ser)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
