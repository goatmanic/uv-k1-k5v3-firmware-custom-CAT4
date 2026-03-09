#!/usr/bin/env python3

import os
import sys
import time
import datetime
import argparse

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"

import pygame
import serial
from serial.tools import list_ports

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
    pygame.K_0: KEYCODES["0"],
    pygame.K_1: KEYCODES["1"],
    pygame.K_2: KEYCODES["2"],
    pygame.K_3: KEYCODES["3"],
    pygame.K_4: KEYCODES["4"],
    pygame.K_5: KEYCODES["5"],
    pygame.K_6: KEYCODES["6"],
    pygame.K_7: KEYCODES["7"],
    pygame.K_8: KEYCODES["8"],
    pygame.K_9: KEYCODES["9"],
    pygame.K_KP0: KEYCODES["0"],
    pygame.K_KP1: KEYCODES["1"],
    pygame.K_KP2: KEYCODES["2"],
    pygame.K_KP3: KEYCODES["3"],
    pygame.K_KP4: KEYCODES["4"],
    pygame.K_KP5: KEYCODES["5"],
    pygame.K_KP6: KEYCODES["6"],
    pygame.K_KP7: KEYCODES["7"],
    pygame.K_KP8: KEYCODES["8"],
    pygame.K_KP9: KEYCODES["9"],
    pygame.K_a: KEYCODES["MENU"],
    pygame.K_UP: KEYCODES["UP"],
    pygame.K_DOWN: KEYCODES["DOWN"],
    pygame.K_z: KEYCODES["EXIT"],
    pygame.K_KP_MULTIPLY: KEYCODES["STAR"],
    pygame.K_x: KEYCODES["STAR"],
    pygame.K_c: KEYCODES["F"],
    pygame.K_F1: KEYCODES["SIDE1"],
    pygame.K_F2: KEYCODES["SIDE2"],
}

COLOR_SETS = {
    "g": ("Grey", pygame.Color(0, 0, 0), pygame.Color(202, 202, 202)),
    "o": ("Orange", pygame.Color(0, 0, 0), pygame.Color(255, 193, 37)),
    "b": ("Blue", pygame.Color(0, 0, 0), pygame.Color(28, 134, 228)),
    "w": ("White", pygame.Color(0, 0, 0), pygame.Color(255, 255, 255)),
}
DEFAULT_COLOR = "g"

framebuffer = bytearray([0] * FRAME_SIZE)


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


def read_frame(ser: serial.Serial) -> bytearray:
    global framebuffer
    while True:
        try:
            b = ser.read(1)
        except serial.SerialException:
            print("[!] Your USB serial cable is probably being used by another application such as Chirp or Chrome.")
            sys.exit(1)
        if not b:
            return None
        if b == HEADER[0:1]:
            b2 = ser.read(1)
            if b2 != HEADER[1:2]:
                continue
            frame_type = ser.read(1)
            size = int.from_bytes(ser.read(2), "big")
            if frame_type == TYPE_SCREENSHOT and size == FRAME_SIZE:
                framebuffer = bytearray(ser.read(FRAME_SIZE))
                return framebuffer
            if frame_type == TYPE_DIFF and size % 9 == 0:
                framebuffer = apply_diff(framebuffer, ser.read(size))
                return framebuffer


def draw_frame(screen: pygame.Surface, fb: bytearray, bg: pygame.Color, fg: pygame.Color, pixel_size: int, pixel_lcd: int) -> pygame.Surface:
    def get_bit(bit_idx: int) -> int:
        byte_idx = bit_idx // 8
        bit_pos = bit_idx % 8
        if byte_idx < len(fb):
            return (fb[byte_idx] >> bit_pos) & 0x01
        return 0

    screen.fill(bg)
    bit_index = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if get_bit(bit_index):
                px = x * (pixel_size - 1)
                py = y * pixel_size
                pygame.draw.rect(screen, fg, (px, py, pixel_size - 1 - pixel_lcd, pixel_size - pixel_lcd))
            bit_index += 1

    pygame.display.flip()
    return pygame.display.get_surface().copy()


def run_combined(ser: serial.Serial):
    pixel_size = 5
    pixel_lcd = 0
    pygame.init()
    screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))

    fg_color, bg_color = COLOR_SETS[DEFAULT_COLOR][1:]
    last_surface = None
    frame_count = 0
    frame_lost = 0
    last_time = time.monotonic()
    base_title = f"Quansheng K5 Combined Viewer/Remote v{VERSION}"
    pygame.display.set_caption(f"{base_title} – No data")

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise KeyboardInterrupt
            if event.type != pygame.KEYDOWN:
                continue

            if event.key == pygame.K_q:
                raise KeyboardInterrupt
            if event.key == pygame.K_SPACE and last_surface:
                filename = datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
                pygame.image.save(last_surface, filename)
                print(f"[✔] Screenshot saved: {filename}")
                continue
            if event.key == pygame.K_p:
                pixel_lcd = 1 - pixel_lcd
                draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size, pixel_lcd)
                continue
            if event.key == pygame.K_i:
                if bg_color == pygame.Color(0, 0, 0):
                    bg_color, fg_color = fg_color, pygame.Color(0, 0, 0)
                else:
                    bg_color, fg_color = pygame.Color(0, 0, 0), bg_color
                draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size, pixel_lcd)
                continue
            if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                if pixel_size < 12:
                    pixel_size += 1
                    screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))
                    draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size, pixel_lcd)
                continue
            if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                if pixel_size > 3:
                    pixel_size -= 1
                    screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))
                    draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size, pixel_lcd)
                continue

            pressed = event.unicode.lower() if event.unicode else ""
            if pressed in COLOR_SETS:
                fg_color, bg_color = COLOR_SETS[pressed][1:]
                continue

            if event.key in KEY_BINDINGS:
                mods = pygame.key.get_mods()
                is_long = bool(mods & pygame.KMOD_SHIFT)
                send_radio_key(ser, KEY_BINDINGS[event.key], is_long)

        frame = read_frame(ser)
        if frame:
            last_surface = draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size, pixel_lcd)
            frame_count += 1
            now = time.monotonic()
            if now - last_time >= 1.0:
                fps = frame_count / (now - last_time)
                pygame.display.set_caption(f"{base_title} – FPS: {fps:>04.1f}")
                frame_count = 0
                last_time = now
                frame_lost = 0
        else:
            frame_lost = min(frame_lost + 1, 5)
            if frame_lost == 5:
                pygame.display.set_caption(f"{base_title} – No data")

        send_keepalive(ser)


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
        description="Live screen viewer + remote keyboard for UV-K5 with F4HWN firmware",
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

    try:
        run_combined(ser)
    except KeyboardInterrupt:
        print("[✔] Exiting")
    finally:
        ser.close()
        pygame.quit()


if __name__ == "__main__":
    main()
