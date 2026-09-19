# -*- coding: utf-8 -*-
"""Отзыв уведомлений доски целиком: хук среды → настоящий `notify.py` → доска.

Заглушка `notify.py` проверяла только аргументы — и пропустила все поломки
отзыва: они жили в окружении, которым хук запускает скрипт, и в метке, которую
скрипт из него строит. Здесь скрипт настоящий, а доска — маленький сервер,
который держит живые карточки по меткам: «погасла» значит «метка снята».

Порядок событий Codex снят живьём: `PreToolUse` → `PermissionRequest` (без
`tool_use_id`) → решение человека → `PostToolUse` того же вызова по концу
команды; отказ прерывает ход событием `Interrupt`, и `Stop` в нём не приходит.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
NOTIFY = TEMPLATES / "tasks" / "notify.py"
CODEX_HOOK = TEMPLATES / "agentic" / ".codex" / "hooks" / "permission-notify.py"
CLAUDE_HOOK = TEMPLATES / "agentic" / ".claude" / "hooks" / "attention-notify.py"

# Переменные сессий всех сред: тест сам решает, какая из них видна процессу,
# иначе метку построила бы сессия среды, в которой тесты запущены
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "OPENCODE_SESSION_ID",
                "CODEX_SESSION_ID")


class _Board:
    """Доска: живые карточки по меткам. Отзыв снимает все карточки метки."""

    def __init__(self) -> None:
        self.live: dict[str, list[str]] = {}
        self.lock = threading.Lock()
        board = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(
                    int(self.headers["Content-Length"])).decode("utf-8"))
                with board.lock:
                    if self.path == "/api/notify":
                        board.live.setdefault(body["key"], []).append(body["text"])
                    elif self.path == "/api/notify/dismiss":
                        board.live.pop(body["key"], None)
                answer = json.dumps({"ok": True, "sent": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, format, *args) -> None:  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def texts(self) -> list[str]:
        with self.lock:
            return [text for texts in self.live.values() for text in texts]


class _Live(unittest.TestCase):
    """Проект с настоящим `notify.py`, подменённым домом и временной папкой."""

    HOOK: Path

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "проект"
        self.tasks = self.root / "tasks"
        self.tasks.mkdir(parents=True)
        shutil.copy(NOTIFY, self.tasks / "notify.py")
        self.board = _Board()
        self.addCleanup(self.board.close)
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"port": self.board.port}), encoding="utf-8")
        self.home = base / "дом"
        self.home.mkdir()
        self.temp = base / "tmp"
        self.temp.mkdir()

    def env(self, **sessions: str) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                   TEMP=str(self.temp), TMP=str(self.temp), TMPDIR=str(self.temp),
                   PYTHONIOENCODING="utf-8", **sessions)
        return env

    def agent_says(self, text: str, **sessions: str) -> None:
        """Агент зовёт человека из шелла своей сессии."""
        done = subprocess.run(
            [sys.executable, str(self.tasks / "notify.py"), text,
             "--agent", "Модель"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env=self.env(**sessions))
        self.assertEqual(0, done.returncode, done.stderr)

    def hook(self, event: str, sessions: dict | None = None, **payload) -> None:
        """Среда зовёт свой хук с событием."""
        body = {"hook_event_name": event, "cwd": str(self.root), **payload}
        done = subprocess.run(
            [sys.executable, str(self.HOOK)], input=json.dumps(body),
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=self.env(**(sessions or {})))
        self.assertEqual(0, done.returncode, done.stderr)

    def assertShown(self, fragment: str) -> None:  # noqa: N802
        self.assertTrue(any(fragment in text for text in self.board.texts()),
                        f"нет карточки «{fragment}»: {self.board.texts()}")

    def assertGone(self, fragment: str) -> None:  # noqa: N802
        self.assertFalse(any(fragment in text for text in self.board.texts()),
                         f"карточка «{fragment}» не погасла: {self.board.texts()}")


class CodexDismissTest(_Live):
    """Codex кладёт сессию в окружение шелла, а хуку — только в payload."""

    HOOK = CODEX_HOOK
    SESSION = "01a0b6f0-сессия-1"

    def shell(self, session: str = SESSION) -> dict:
        return {"CODEX_SESSION_ID": session}

    def codex(self, event: str, session: str = SESSION, **payload) -> None:
        self.hook(event, session_id=session, **payload)

    def test_reply_dismisses_agent_message(self) -> None:
        self.agent_says("работа готова", **self.shell())

        self.codex("UserPromptSubmit")

        self.assertGone("работа готова")

    def test_reply_in_other_session_keeps_the_card(self) -> None:
        self.agent_says("работа готова", **self.shell("сессия-2"))
        self.codex("PermissionRequest", "сессия-2", tool_name="Bash")

        self.codex("UserPromptSubmit")

        self.assertShown("работа готова")
        self.assertShown("разрешения")

    def test_answered_question_goes_agent_message_stays(self) -> None:
        self.agent_says("работа готова", **self.shell())
        self.codex("PreToolUse", tool_name="request_user_input")
        self.assertShown("ответа")

        self.codex("PostToolUse", tool_name="request_user_input")

        self.assertGone("ответа")
        self.assertShown("работа готова")

    def test_allowed_action_dismisses_on_its_completion(self) -> None:
        self.codex("PreToolUse", tool_name="Bash", tool_use_id="exec-1")
        self.codex("PermissionRequest", tool_name="Bash")
        self.assertShown("разрешения")

        self.codex("PostToolUse", tool_name="Bash", tool_use_id="exec-1")

        self.assertGone("разрешения")

    def test_denied_permission_dismisses_on_interrupt(self) -> None:
        self.codex("PermissionRequest", tool_name="Bash")

        self.codex("Interrupt")

        self.assertGone("разрешения")

    def test_without_session_dismissal_is_per_project(self) -> None:
        self.agent_says("работа готова")
        self.hook("PermissionRequest", tool_name="Bash")

        self.hook("UserPromptSubmit")

        self.assertEqual([], self.board.texts())


class _ToolEventCost(_Live):
    """Каждый вызов инструмента будит хук: без карточки разрешения он не
    должен звать доску — иначе на каждую команду лишний процесс и запрос."""

    def check_silent(self) -> None:
        calls = self.temp / "calls.log"
        (self.tasks / "notify.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(calls)!r}).write_text('called', encoding='utf-8')\n",
            encoding="utf-8")

        for event in ("PostToolUse", "PostToolUseFailure"):
            self.hook(event, session_id="с-1", tool_name="Bash")

        self.assertFalse(calls.exists())


class CodexToolEventCostTest(_ToolEventCost):
    HOOK = CODEX_HOOK

    def test_tool_completion_without_permission_is_silent(self) -> None:
        self.check_silent()


class ClaudeToolEventCostTest(_ToolEventCost):
    HOOK = CLAUDE_HOOK

    def test_tool_completion_without_permission_is_silent(self) -> None:
        self.check_silent()


class ClaudeDismissTest(_Live):
    """Claude Code кладёт сессию в окружение и шелла, и хука."""

    HOOK = CLAUDE_HOOK
    SESSION = "8f048864-сессия-1"

    def claude(self, event: str, **payload) -> None:
        sessions = {"CLAUDE_CODE_SESSION_ID": self.SESSION}
        self.hook(event, sessions, session_id=self.SESSION, **payload)

    def test_allowed_action_dismisses_on_its_completion(self) -> None:
        self.claude("PermissionRequest", tool_name="Bash")
        self.assertShown("разрешения")

        self.claude("PostToolUse", tool_name="Bash")

        self.assertGone("разрешения")

    def test_denied_permission_dismisses_on_next_action(self) -> None:
        self.claude("PermissionRequest", tool_name="Bash")

        self.claude("PostToolUseFailure", tool_name="Bash")

        self.assertGone("разрешения")

    def test_tool_completion_keeps_agent_message(self) -> None:
        self.agent_says("работа готова",
                        CLAUDE_CODE_SESSION_ID=self.SESSION)
        self.claude("PermissionRequest", tool_name="Bash")

        self.claude("PostToolUse", tool_name="Bash")

        self.assertShown("работа готова")


if __name__ == "__main__":
    unittest.main()
