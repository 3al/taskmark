"""Решение Codex снимает карточку до завершения разрешённой команды."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import unittest

from tests.test_attention_dismiss_live import _Live, CODEX_HOOK

MODULE = CODEX_HOOK.with_name("codex_approval.py")


def load_module():
    spec = importlib.util.spec_from_file_location("codex_approval", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Channel:
    """Внешний канал Codex: ход продолжается после решения."""

    def __init__(self, events):
        self.events = iter(events)
        self.sent = []

    def send(self, data):
        self.sent.append(data)

    def receive(self):
        return next(self.events)


class ApprovalResolutionTest(_Live):
    HOOK = CODEX_HOOK

    def check_decision(self, decision):
        module = load_module()
        session = "сессия-1"
        self.hook("PermissionRequest", session_id=session, tool_name="Bash")
        self.hook("PermissionRequest", session_id="сессия-2", tool_name="Bash")
        self.assertEqual(2, len(self.board.texts()))
        marker = self.temp / "pending"
        marker.write_text("generation", encoding="utf-8")
        ready = self.temp / "ready"
        channel = _Channel([
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [session]}},
            {"id": 3, "result": {"thread": {"id": session}}},
            {"method": "serverRequest/resolved", "params": {
                "threadId": "сессия-2", "requestId": 10}},
            {"method": "serverRequest/resolved", "params": {
                "threadId": session, "requestId": 11}},
        ])
        # Подменено только окружение внешнего процесса и внешний канал.
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, self.env(CODEX_SESSION_ID=session), clear=True):
            module.observe(channel, self.root, session, marker, "generation", ready)
        self.assertEqual(1, len(self.board.texts()), decision)
        self.assertEqual({"env:CODEX_SESSION_ID:сессия-2"}, set(self.board.live))
        self.assertTrue(ready.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(["initialize", "initialized", "thread/loaded/list", "thread/resume"],
                         [msg["method"] for msg in channel.sent])
        self.assertTrue(all("result" not in msg for msg in channel.sent),
                        "наблюдатель не отвечает на запросы Codex")

    def test_accept_dismisses_before_command_completion(self):
        self.check_decision("accept")

    def test_decline_dismisses_without_waiting_for_interrupt(self):
        self.check_decision("decline")

    def test_old_observer_keeps_new_permission(self):
        module = load_module()
        marker = self.temp / "pending"
        marker.write_text("new", encoding="utf-8")
        channel = _Channel([])
        module.observe(channel, self.root, "session", marker, "old", self.temp / "ready")
        self.assertEqual("new", marker.read_text(encoding="utf-8"))
        self.assertEqual([], channel.sent)

    def test_unloaded_session_is_not_resumed(self):
        module = load_module()
        marker = self.temp / "pending"
        marker.write_text("generation", encoding="utf-8")
        channel = _Channel([
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": ["other"]}},
        ])
        module.observe(channel, self.root, "session", marker, "generation", self.temp / "ready")
        self.assertEqual("generation", marker.read_text(encoding="utf-8"))
        self.assertNotIn("thread/resume", [msg["method"] for msg in channel.sent])


class WebSocketTest(unittest.TestCase):
    def test_frames_fragmentation_ping_and_utf8(self):
        module = load_module()
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        channel = module.Channel(client)
        data = json.dumps({"текст": "я"}, ensure_ascii=False).encode("utf-8")
        server.sendall(bytes([1, 3]) + data[:3] + b"\x89\x01x" +
                       bytes([128, len(data)-3]) + data[3:])
        self.assertEqual({"текст": "я"}, channel.receive())
        header = server.recv(2)
        self.assertEqual(b"\x8a\x81", header)
        mask = server.recv(4)
        self.assertEqual(ord('x'), server.recv(1)[0] ^ mask[0])


if __name__ == "__main__":
    unittest.main()
