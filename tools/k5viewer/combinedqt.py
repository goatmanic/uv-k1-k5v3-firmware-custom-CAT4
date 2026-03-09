#!/usr/bin/env python3

import sys
import time
import datetime
import argparse

import serial
from serial.tools import list_ports
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QPainter, QPixmap, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QPushButton,
    QCheckBox,
)

VERSION = "1.1"
DEFAULT_PORT = "/dev/ttyUSB0"
BAUDRATE = 38400
TIMEOUT = 0.5

WIDTH, HEIGHT = 128, 64
FRAME_SIZE = 1024

HEADER = b"\xAA\x55"
TYPE_SCREENSHOT = b"\x01"
TYPE_DIFF = b"\x02"
TYPE_KEY = b"\x03"
TYPE_KEY_LONG = b"\x04"

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
    Qt.Key.Key_Asterisk: KEYCODES["STAR"],
    Qt.Key.Key_X: KEYCODES["STAR"],
    Qt.Key.Key_C: KEYCODES["F"],
    Qt.Key.Key_F1: KEYCODES["SIDE1"],
    Qt.Key.Key_F2: KEYCODES["SIDE2"],
}

COLOR_SETS = {
    "g": ("Grey", QColor(0, 0, 0), QColor(202, 202, 202)),
    "o": ("Orange", QColor(0, 0, 0), QColor(255, 193, 37)),
    "b": ("Blue", QColor(0, 0, 0), QColor(28, 134, 228)),
    "w": ("White", QColor(0, 0, 0), QColor(255, 255, 255)),
}
DEFAULT_COLOR = "g"


def send_keepalive(ser: serial.Serial):
    try:
        ser.write(b"\x55\xAA\x00\x00")
    except serial.SerialException:
        pass


def send_radio_key(ser: serial.Serial, keycode: int, is_long: bool):
    pkt_type = TYPE_KEY_LONG if is_long else TYPE_KEY
    try:
        ser.write(HEADER + pkt_type + bytes([keycode]))
    except serial.SerialException:
        print("[!] Failed to send key packet to radio")


def apply_diff(fb: bytearray, diff_payload: bytes) -> bytearray:
    i = 0
    while i + 9 <= len(diff_payload):
        block_index = diff_payload[i]
        i += 1
        if block_index >= 128:
            break
        fb[block_index * 8: block_index * 8 + 8] = diff_payload[i:i + 8]
        i += 8
    return fb


def read_frame(ser: serial.Serial, framebuffer: bytearray) -> bytearray | None:
    while True:
        try:
            b = ser.read(1)
        except serial.SerialException:
            print("[!] Your USB serial cable is probably being used by another application such as Chirp or Chrome.")
            sys.exit(1)

        if not b:
            return None
        if b != HEADER[0:1]:
            continue

        b2 = ser.read(1)
        if b2 != HEADER[1:2]:
            continue

        frame_type = ser.read(1)
        size = int.from_bytes(ser.read(2), "big")
        if frame_type == TYPE_SCREENSHOT and size == FRAME_SIZE:
            return bytearray(ser.read(FRAME_SIZE))
        if frame_type == TYPE_DIFF and size % 9 == 0:
            return apply_diff(framebuffer, ser.read(size))


class DisplayCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = QPixmap(1, 1)

    def set_canvas(self, canvas: QPixmap):
        self.canvas = canvas
        self.setFixedSize(canvas.size())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.canvas)


class CombinedQtViewer(QWidget):
    def __init__(self, ser: serial.Serial):
        super().__init__()
        self.ser = ser
        self.framebuffer = bytearray([0] * FRAME_SIZE)
        self.pixel_size = 5
        self.pixel_lcd = 0
        self.fg_color, self.bg_color = COLOR_SETS[DEFAULT_COLOR][1:]
        self.base_title = f"Quansheng K5 Combined Viewer/Remote v{VERSION}"
        self.frame_count = 0
        self.frame_lost = 0
        self.last_time = time.monotonic()

        self.canvas = QPixmap(self.display_width, self.display_height)
        self.canvas.fill(self.bg_color)
        self.display = DisplayCanvas(self)
        self.display.set_canvas(self.canvas)
        self.long_press_checkbox = QCheckBox("Long press (SHIFT)")
        self.long_press_checkbox.setToolTip("Send TYPE_KEY_LONG packets")

        self.build_ui()

        self.setWindowTitle(f"{self.base_title} – No data")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_serial)
        self.timer.start(5)

    @property
    def display_width(self) -> int:
        return WIDTH * (self.pixel_size - 1)

    @property
    def display_height(self) -> int:
        return HEIGHT * self.pixel_size

    def sizeHint(self) -> QSize:
        return QSize(self.display_width + 260, self.display_height)

    def build_ui(self):
        layout = QHBoxLayout()
        layout.addWidget(self.display)

        side_panel = QVBoxLayout()
        side_panel.addWidget(self.long_press_checkbox)

        grid = QGridLayout()
        button_rows = [
            ["MENU", "UP", "EXIT"],
            ["1", "2", "3"],
            ["4", "5", "6"],
            ["7", "8", "9"],
            ["STAR", "0", "F"],
            ["SIDE1", "DOWN", "SIDE2"],
        ]

        for r, row in enumerate(button_rows):
            for c, key_name in enumerate(row):
                button = QPushButton(key_name)
                button.setMinimumSize(64, 36)
                button.clicked.connect(lambda checked=False, name=key_name: self.send_named_key(name))
                grid.addWidget(button, r, c)

        side_panel.addLayout(grid)
        side_panel.addStretch(1)

        layout.addLayout(side_panel)
        self.setLayout(layout)

    def send_named_key(self, key_name: str, is_long: bool | None = None):
        if key_name not in KEYCODES:
            return
        if is_long is None:
            is_long = self.long_press_checkbox.isChecked()
        send_radio_key(self.ser, KEYCODES[key_name], is_long)
        self.setFocus()

    def poll_serial(self):
        frame = read_frame(self.ser, self.framebuffer)
        if frame:
            self.framebuffer = frame
            self.draw_frame()
            self.frame_count += 1
            now = time.monotonic()
            if now - self.last_time >= 1.0:
                fps = self.frame_count / (now - self.last_time)
                self.setWindowTitle(f"{self.base_title} – FPS: {fps:>04.1f}")
                self.frame_count = 0
                self.last_time = now
                self.frame_lost = 0
        else:
            self.frame_lost = min(self.frame_lost + 1, 5)
            if self.frame_lost == 5:
                self.setWindowTitle(f"{self.base_title} – No data")

        send_keepalive(self.ser)

    def draw_frame(self):
        self.canvas = QPixmap(self.display_width, self.display_height)
        self.canvas.fill(self.bg_color)

        painter = QPainter(self.canvas)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.fg_color)

        bit_index = 0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                byte_idx = bit_index // 8
                bit_pos = bit_index % 8
                bit = (self.framebuffer[byte_idx] >> bit_pos) & 0x01 if byte_idx < len(self.framebuffer) else 0
                if bit:
                    px = x * (self.pixel_size - 1)
                    py = y * self.pixel_size
                    painter.drawRect(px, py, self.pixel_size - 1 - self.pixel_lcd, self.pixel_size - self.pixel_lcd)
                bit_index += 1

        painter.end()
        self.display.set_canvas(self.canvas)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key == Qt.Key.Key_Q:
            self.close()
            return

        if key == Qt.Key.Key_Space:
            filename = datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
            self.canvas.save(filename)
            print(f"[✔] Screenshot saved: {filename}")
            return

        if key == Qt.Key.Key_P:
            self.pixel_lcd = 1 - self.pixel_lcd
            self.draw_frame()
            return

        if key == Qt.Key.Key_I:
            if self.bg_color == QColor(0, 0, 0):
                self.bg_color, self.fg_color = self.fg_color, QColor(0, 0, 0)
            else:
                self.bg_color, self.fg_color = QColor(0, 0, 0), self.bg_color
            self.draw_frame()
            return

        if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            if self.pixel_size < 12:
                self.pixel_size += 1
                self.draw_frame()
            return

        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            if self.pixel_size > 3:
                self.pixel_size -= 1
                self.draw_frame()
            return

        text = event.text().lower() if event.text() else ""
        if text in COLOR_SETS:
            self.fg_color, self.bg_color = COLOR_SETS[text][1:]
            self.draw_frame()
            return

        if key in KEY_BINDINGS:
            is_long = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            send_radio_key(self.ser, KEY_BINDINGS[key], is_long)

    def closeEvent(self, event):
        self.timer.stop()
        self.ser.close()
        event.accept()


def cmd_list_ports():
    print("Available ports:")
    for port in list_ports.comports():
        if port.vid is None:
            continue
        description = " - ".join(filter(None, (port.product, port.manufacturer)))
        print(f"- {description} : {port.device}" if description else f"- {port.device}")


def main():
    parser = argparse.ArgumentParser(
        prog="combinedqt.py",
        description="Live screen viewer + remote keyboard for UV-K5 with F4HWN firmware (Qt6)",
    )
    parser.add_argument("--list-ports", action="store_true", help="list available ports and exit")
    parser.add_argument("--port", type=str, help="serial port to use (in place of DEFAULT_PORT)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    if args.list_ports:
        cmd_list_ports()
        return

    if not args.port and not DEFAULT_PORT:
        print("Please specify --port or edit DEFAULT_PORT in the script")
        sys.exit(1)

    serial_port = args.port or DEFAULT_PORT
    try:
        ser = serial.Serial(serial_port, BAUDRATE, timeout=TIMEOUT)
    except serial.SerialException as err:
        print(f"[!] Serial error: {err}")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = CombinedQtViewer(ser)
    window.show()

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("[✔] Exiting")
        ser.close()


if __name__ == "__main__":
    main()
