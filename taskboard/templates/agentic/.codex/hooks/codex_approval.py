"""Отзыв уведомления по решению Codex, пока разрешённая команда ещё работает.

Подключается к уже запущенному app-server через его локальный сокет. Не
запускает Codex, не создаёт ходы и никогда не отвечает на запросы разрешения.
На старом Codex или без сокета остаются обычные PostToolUse/Interrupt.
Протокол: https://learn.chatgpt.com/docs/app-server (serverRequest/resolved).
Скрипт автономен: для поставки достаточно стандартной библиотеки Python 3.10.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time


MAX_MESSAGE = 8 * 1024 * 1024
MAX_WAIT = 30 * 60


class Channel:
    """Минимальный WebSocket-клиент для локального JSON-RPC Codex."""

    def __init__(self, stream: socket.socket):
        self.stream = stream

    def _read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = self.stream.recv(size - len(data))
            except socket.timeout:
                raise EOFError("Codex не закончил кадр") from None
            if not chunk:
                raise EOFError("Codex закрыл соединение")
            data.extend(chunk)
        return bytes(data)

    def _send_frame(self, data: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        size = len(data)
        length = (bytes([128 | size]) if size < 126 else
                  b"\xfe" + struct.pack("!H", size) if size < 65536 else
                  b"\xff" + struct.pack("!Q", size))
        self.stream.sendall(bytes([128 | opcode]) + length + mask +
                            bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def send(self, message: dict) -> None:
        self._send_frame(json.dumps(message, ensure_ascii=False).encode("utf-8"))

    def receive(self) -> dict:
        # Таймаут ожидания события допустим только между сообщениями.
        # Оборванный кадр нельзя продолжать разбирать как новый заголовок.
        if not select.select([self.stream], [], [], 1)[0]:
            raise socket.timeout()
        data = bytearray()
        while True:
            first, second = self._read(2)
            opcode, size = first & 15, second & 127
            if size == 126:
                size = struct.unpack("!H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read(8))[0]
            if size + len(data) > MAX_MESSAGE or second & 128:
                raise ValueError("Некорректный кадр Codex")
            body = self._read(size)
            if opcode == 8:
                raise EOFError("Codex закрыл WebSocket")
            if opcode == 9:
                self._send_frame(body, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise ValueError("Ожидался текстовый кадр Codex")
            data.extend(body)
            if first & 128:
                message = json.loads(data.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("Ожидался объект JSON-RPC")
                return message


def connect(path: Path) -> Channel:
    """Открыть штатный Unix socket; Windows поддерживает его через Winsock."""
    stream = socket.socket(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            # CPython на Windows не экспортирует AF_UNIX и не кодирует его
            # адреса, хотя Winsock Windows 10 этот транспорт поддерживает.
            import ctypes

            class Address(ctypes.Structure):
                _fields_ = [("family", ctypes.c_ushort),
                            ("path", ctypes.c_char * 108)]

            encoded = os.fsencode(path)
            if len(encoded) >= 108:
                raise OSError("Слишком длинный путь сокета Codex")
            address = Address(1, encoded)
            winsock = ctypes.WinDLL("ws2_32")
            winsock.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
            if winsock.connect(stream.fileno(), ctypes.byref(address), ctypes.sizeof(address)):
                raise OSError(winsock.WSAGetLastError(), "Сокет Codex недоступен")
        else:
            stream.settimeout(1)
            stream.connect(str(path))
        stream.settimeout(1)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        stream.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Key: {key}\r\n\r\n").encode("ascii"))
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            chunk = stream.recv(1)
            if not chunk or len(header) > 16384:
                raise ValueError("Некорректное приветствие Codex")
            header.extend(chunk)
        lines = header.decode("ascii").split("\r\n")
        fields = {name.lower(): value.strip() for name, value in
                  (line.split(":", 1) for line in lines[1:] if ":" in line)}
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        if lines[0].split()[1] != "101" or fields.get("sec-websocket-accept") != expected:
            raise ValueError("Codex не подтвердил WebSocket")
        return Channel(stream)
    except BaseException:
        stream.close()
        raise


def _current(marker: Path, generation: str) -> bool:
    try:
        return marker.read_text(encoding="utf-8") == generation
    except OSError:
        return False


def observe(channel: Channel, root: Path, session: str, marker: Path,
            generation: str, ready: Path) -> None:
    """Подписаться только на загруженную сессию и дождаться её решения."""
    if not _current(marker, generation):
        return
    channel.send({"id": 1, "method": "initialize", "params": {
        "clientInfo": {"name": "taskmark_notices", "version": "1"}}})
    deadline = time.monotonic() + MAX_WAIT
    subscribed = False
    while time.monotonic() < deadline and _current(marker, generation):
        try:
            event = channel.receive()
        except socket.timeout:
            continue
        if "error" in event:
            return
        if event.get("id") == 1 and "result" in event:
            channel.send({"method": "initialized"})
            channel.send({"id": 2, "method": "thread/loaded/list", "params": {}})
        elif event.get("id") == 2 and "result" in event:
            if session not in event["result"].get("data", []):
                return  # Не загружать чужую/завершённую сессию ради уведомления.
            channel.send({"id": 3, "method": "thread/resume", "params": {
                "threadId": session, "excludeTurns": True}})
        elif event.get("id") == 3 and "result" in event:
            subscribed = True
            ready.touch()
        elif subscribed and event.get("method") == "serverRequest/resolved":
            if event.get("params", {}).get("threadId") != session:
                continue
            if not _current(marker, generation):
                return
            env = os.environ.copy()
            env["CODEX_SESSION_ID"] = session
            subprocess.run([sys.executable, str(root / "tasks/notify.py"),
                            "--dismiss", "env"], cwd=root, env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)
            if _current(marker, generation):
                marker.unlink(missing_ok=True)
            return


def start_watch(root: Path, session: str, marker: Path, generation: str) -> None:
    """Дождаться готовности наблюдателя до показа диалога разрешения."""
    if not session:
        return
    ready = marker.with_name(f"{marker.name}.{generation}.ready")
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
             if os.name == "nt" else 0)
    executable = Path(sys.executable)
    if os.name == "nt" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    child = subprocess.Popen(
        [str(executable), str(Path(__file__).resolve()), str(root), session,
         str(marker), generation, str(ready)], cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, start_new_session=os.name != "nt", close_fds=True)
    deadline = time.monotonic() + 1.2
    try:
        while time.monotonic() < deadline:
            if ready.exists() or child.poll() is not None:
                return
            time.sleep(0.02)
        child.terminate()  # Нет подписки — остаётся обычный отзыв хуками.
        child.wait(timeout=1)
    finally:
        ready.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 6:
        return 0
    root, session, marker, generation, ready = sys.argv[1:]
    channel = None
    try:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        channel = connect(codex_home / "app-server-control/app-server-control.sock")
        observe(channel, Path(root), session, Path(marker), generation, Path(ready))
    except (OSError, ValueError, EOFError, subprocess.SubprocessError):
        pass  # Отзыв не влияет на работу Codex.
    finally:
        if channel is not None:
            channel.stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
