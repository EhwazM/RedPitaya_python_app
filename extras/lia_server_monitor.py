#!/usr/bin/env python3
from __future__ import annotations

import re
import socket
import struct
import subprocess
import sys
import threading

HOST = "0.0.0.0"
PORT = 5001
BASE_ADDR = 0x40700000

REG_PHASE_INC = 0x00
REG_PHASE_OFFSET = 0x04
REG_I = 0x08
REG_Q = 0x0C
REG_SOFT_RESET = 0x10
REG_K = 0x14
REG_N_STAGES = 0x18
REG_EXT_REF = 0x20
REG_ZC_THRESH = 0x24
REG_PERIOD = 0x28

READABLE = {
    REG_PHASE_INC, REG_PHASE_OFFSET, REG_I, REG_Q,
    REG_K, REG_N_STAGES, REG_EXT_REF, REG_ZC_THRESH,
    REG_PERIOD,
}

WRITABLE = {
    REG_PHASE_INC, REG_PHASE_OFFSET, REG_SOFT_RESET,
    REG_K, REG_N_STAGES, REG_EXT_REF, REG_ZC_THRESH,
}


class LiaMonitor:
    def __init__(self, base_addr: int = BASE_ADDR):
        self.base_addr = base_addr
        self._hw_lock = threading.Lock()

    def _absolute_address(self, offset: int) -> int:
        return self.base_addr + offset

    @staticmethod
    def _parse_monitor_value(output: str) -> int:
        match = re.search(r"0x([0-9a-fA-F]{1,8})", output)
        if not match:
            raise RuntimeError(f"Could not parse monitor output: {output!r}")
        return int(match.group(1), 16)

    def read32(self, offset: int) -> int:
        if offset not in READABLE:
            raise ValueError(f"Invalid LIA read register: 0x{offset:02X}")
        address = self._absolute_address(offset)
        with self._hw_lock:
            result = subprocess.run(
                ["monitor", f"0x{address:08X}"],
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"monitor read failed at 0x{address:08X}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return self._parse_monitor_value(result.stdout)

    def write32(self, offset: int, value: int) -> None:
        if offset not in WRITABLE:
            raise ValueError(f"Invalid LIA write register: 0x{offset:02X}")
        value &= 0xFFFFFFFF
        address = self._absolute_address(offset)
        with self._hw_lock:
            result = subprocess.run(
                ["monitor", f"0x{address:08X}", f"0x{value:08X}"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"monitor write failed at 0x{address:08X}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            if offset != REG_SOFT_RESET:
                verify = subprocess.run(
                    ["monitor", f"0x{address:08X}"],
                    capture_output=True,
                    text=True,
                )
                if verify.returncode != 0:
                    raise RuntimeError(
                        f"monitor readback failed at 0x{address:08X}: "
                        f"{verify.stderr.strip() or verify.stdout.strip()}"
                    )
                readback = self._parse_monitor_value(verify.stdout)
                if readback != value:
                    raise RuntimeError(
                        f"Write verification failed at 0x{address:08X}: "
                        f"wrote 0x{value:08X}, read 0x{readback:08X}"
                    )


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Client disconnected")
        data.extend(chunk)
    return bytes(data)


def send_status(sock: socket.socket, success: bool) -> None:
    sock.sendall(b"\x00" if success else b"\x01")


def handle_client(conn: socket.socket, address, monitor: LiaMonitor) -> None:
    print(f"LIA client connected: {address}", flush=True)
    try:
        while True:
            command = conn.recv(1)
            if not command:
                break
            if command == b"R":
                offset = struct.unpack("<I", recv_exact(conn, 4))[0]
                try:
                    value = monitor.read32(offset)
                    print(f"READ  0x{offset:02X} -> 0x{value:08X}", flush=True)
                    conn.sendall(struct.pack("<I", value))
                except Exception as exc:
                    print(f"READ ERROR 0x{offset:02X}: {exc}", flush=True)
                    conn.sendall(struct.pack("<I", 0xFFFFFFFF))
            elif command == b"W":
                offset, value = struct.unpack("<II", recv_exact(conn, 8))
                try:
                    monitor.write32(offset, value)
                    print(f"WRITE 0x{offset:02X} <- 0x{value:08X}", flush=True)
                    send_status(conn, True)
                except Exception as exc:
                    print(
                        f"WRITE ERROR 0x{offset:02X} <- 0x{value:08X}: {exc}",
                        flush=True,
                    )
                    send_status(conn, False)
            else:
                print(f"Unknown command: {command!r}", flush=True)
                break
    except (ConnectionError, BrokenPipeError, ConnectionResetError) as exc:
        print(f"Client connection ended: {exc}", flush=True)
    finally:
        conn.close()
        print(f"LIA client disconnected: {address}", flush=True)


def main() -> int:
    geteuid = getattr(sys, "geteuid", None)
    if geteuid is not None and geteuid() != 0:
        print("ERROR: run this server as root.", file=sys.stderr)
        return 1

    monitor = LiaMonitor()
    try:
        phase_inc = monitor.read32(REG_PHASE_INC)
    except Exception as exc:
        print(f"FATAL: cannot access LIA register block: {exc}", file=sys.stderr)
        return 1

    print("LIA monitor backend ready", flush=True)
    print(f"Base address: 0x{BASE_ADDR:08X}", flush=True)
    print(f"PHASE_INC: 0x{phase_inc:08X}", flush=True)
    print("Register map: 00,04,08,0C,10,14,18,1C,20,24,28", flush=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(4)
    print(
        f"LIA register server listening on TCP {PORT}, base 0x{BASE_ADDR:08X}",
        flush=True,
    )
    try:
        while True:
            conn, address = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, address, monitor),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\nStopping LIA server.", flush=True)
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
