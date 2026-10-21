"""A small WebSocket server side (RFC 6455) on top of http.server. Standard library only."""
import base64
import hashlib
import json
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MSG = 1 << 20  # a paste can be big, but not this big


class Closed(Exception):
    pass


class WebSocket:
    def __init__(self, handler):
        self.sock = handler.connection
        self.rfile = handler.rfile
        self.wlock = threading.Lock()
        self.open = True

    @staticmethod
    def accept(handler):
        """Finish the upgrade handshake. Returns a WebSocket, or None after sending an error."""
        h = handler.headers
        key = h.get("Sec-WebSocket-Key")
        if "websocket" not in (h.get("Upgrade") or "").lower() or not key:
            handler.send_error(400, "WebSocket upgrade expected")
            return None
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", acc)
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        return WebSocket(handler)

    def _read(self, n):
        b = self.rfile.read(n)
        if len(b) < n:
            raise Closed()
        return b

    def recv(self):
        """Next whole message: str for text, bytes for binary. Raises Closed."""
        parts, kind = [], None
        while True:
            b0, b1 = self._read(2)
            fin, op, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if n > MAX_MSG:
                raise Closed()
            mask = self._read(4) if b1 & 0x80 else b"\0\0\0\0"
            data = self._read(n) if n else b""
            if n and b1 & 0x80:
                data = (int.from_bytes(data, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")
            if op == 8:
                self.close()
                raise Closed()
            if op == 9:
                self._frame(10, data)
                continue
            if op == 10:
                continue
            if op in (1, 2):
                kind, parts = op, [data]
            elif op == 0 and kind:
                parts.append(data)
                if sum(map(len, parts)) > MAX_MSG:
                    raise Closed()
            if fin and kind:
                msg = b"".join(parts)
                return msg.decode("utf-8", "replace") if kind == 1 else msg

    def _frame(self, op, data):
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else b"\x7e" + struct.pack("!H", n) if n < 65536 else b"\x7f" + struct.pack("!Q", n))
        with self.wlock:
            if not self.open:
                raise Closed()
            try:
                self.sock.sendall(head + data)
            except OSError:
                self.open = False
                raise Closed()

    def send_bytes(self, data):
        self._frame(2, data)

    def send_json(self, obj):
        self._frame(1, json.dumps(obj, separators=(",", ":")).encode())

    def close(self):
        if self.open:
            try:
                self._frame(8, b"")
            except Closed:
                pass
        self.open = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
