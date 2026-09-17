# -*- coding: utf-8 -*-
"""Уведомления Claude Code, когда ход перешёл к человеку (TASK-280).

Человеку говорят разное, когда у него спрашивают разрешение и когда ждут
ответа, — и развести эти поводы можно только двумя событиями среды.

Событие у них общее: и диалог доступа, и вопрос с вариантами среда шлёт как
`PermissionRequest` — «нужно решение». Отличается только `tool_name`, поэтому
вопрос узнают по инструменту (`AskUserQuestion`), а не по событию.

`Notification/permission_prompt` для этого не годится вовсе: там одинаковый
`message` («Claude needs your permission») и никакого имени инструмента
(проверено снятием настоящих событий). Он приезжает вторым концом того же
разрешения, поэтому из фильтра исключён — иначе на один повод две карточки.
`Notification` остаётся ожиданию ввода.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (CLAUDE_SETTINGS, hook_registered,  # noqa: E402
                              register_hook, scaffold_project)
from backend.validator import validate_project  # noqa: E402

CLAUDE_ONLY = {"claude": True, "opencode": False, "codex": False}
CODEX_ONLY = {"claude": False, "opencode": False, "codex": True}

HOOK_TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
                 / ".claude" / "hooks" / "attention-notify.py")


class _Project(unittest.TestCase):
    """Общая заготовка: пустой проект во временной папке."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"

    def deploy(self, harnesses: dict) -> dict:
        return scaffold_project(self.tasks, self.cfg(harnesses),
                                {"harnesses": harnesses})

    def cfg(self, harnesses: dict) -> dict:
        return {**DEFAULTS, "harnesses": harnesses}


class AttentionHookDeliveryTest(_Project):
    """Обработчик и запись о нём — одна рабочая часть поставки."""

    def settings(self) -> dict:
        return json.loads((self.root / CLAUDE_SETTINGS).read_text(encoding="utf-8"))

    def entry(self, event: str = "Notification") -> dict:
        return self.settings()["hooks"][event][0]

    def test_handler_and_registration_deployed(self) -> None:
        self.deploy(CLAUDE_ONLY)

        self.assertTrue((self.root / ".claude" / "hooks"
                         / "attention-notify.py").is_file())
        self.assertTrue(hook_registered(self.root, "claude"))

    def test_two_moments_come_through_two_doors(self) -> None:
        """Разрешение и вопрос среда шлёт неразличимо — развести их можно
        только двумя событиями: синхронным `PermissionRequest` (там есть
        инструмент) и `Notification` про ожидание ввода."""
        self.deploy(CLAUDE_ONLY)

        notification = self.entry("Notification")["matcher"]
        self.assertNotIn("permission_prompt", notification,
                         "иначе на разрешение придут две карточки")
        self.assertIn("idle_prompt", notification)

        permission = self.entry("PermissionRequest")
        self.assertEqual("*", permission["matcher"])
        for entry in (self.entry("Notification"), permission):
            self.assertIn(".claude/hooks/attention-notify.py",
                          entry["hooks"][0]["command"])

    def test_permission_hook_does_not_hold_the_dialog(self) -> None:
        """Диалог доступа ждать уведомления не должен: оно вспомогательно."""
        self.deploy(CLAUDE_ONLY)
        handler = self.entry("PermissionRequest")["hooks"][0]

        self.assertTrue(handler["async"])
        # Среда, не знающая про async, не должна висеть на нём дефолтные 600 с
        self.assertLessEqual(handler["timeout"], 10)

    def test_prompt_and_turn_end_mark_a_new_wait(self) -> None:
        """Отложенному уведомлению нужно знать, что ожидание кончилось: его
        кончают реплика человека и конец хода агента. Ни то ни другое ждать
        обработчик не должно."""
        self.deploy(CLAUDE_ONLY)

        for event in ("UserPromptSubmit", "Stop"):
            with self.subTest(event=event):
                handler = self.entry(event)["hooks"][0]
                self.assertIn(".claude/hooks/attention-notify.py",
                              handler["command"])
                self.assertTrue(handler["async"])
                self.assertLessEqual(handler["timeout"], 10)

    def test_codex_keeps_its_own_handler(self) -> None:
        """У Codex события `Notification` нет — там всё ловит `PermissionRequest`."""
        self.deploy(CODEX_ONLY)
        hooks = json.loads((self.root / ".codex" / "hooks.json")
                           .read_text(encoding="utf-8"))["hooks"]

        self.assertNotIn("Notification", hooks)
        self.assertFalse((self.root / ".codex" / "hooks"
                          / "attention-notify.py").exists())

    def test_missing_handler_gets_banner_and_button_restores_all(self) -> None:
        self.deploy(CLAUDE_ONLY)
        handler = self.root / ".claude" / "hooks" / "attention-notify.py"
        handler.unlink()

        issues = validate_project(self.tasks, self.cfg(CLAUDE_ONLY))["degraded"]
        missing = [i for i in issues if i["code"] == "no_hooks"]
        self.assertTrue(missing)
        self.assertIn("claude/attention-notify.py", missing[0]["names"])

        data = self.settings()
        data["hooks"].pop("Notification")
        data["hooks"].pop("PermissionRequest")
        (self.root / CLAUDE_SETTINGS).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        scaffold_project(self.tasks, self.cfg(CLAUDE_ONLY), {"parts": ["hooks"]})

        self.assertTrue(handler.is_file())
        self.assertTrue(hook_registered(self.root, "claude"))

    def test_missing_registration_is_restored(self) -> None:
        self.deploy(CLAUDE_ONLY)
        data = self.settings()
        data["hooks"].pop("PermissionRequest")
        (self.root / CLAUDE_SETTINGS).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

        self.assertFalse(hook_registered(self.root, "claude"))
        register_hook(self.root, self.cfg(CLAUDE_ONLY))

        self.assertTrue(hook_registered(self.root, "claude"))
        self.assertIn("PermissionRequest", self.settings()["hooks"])


class _HookCall(_Project):
    """Запуск обработчика с подменённым домом и временной папкой.

    Задержка простоя — настройка доски в глобальном конфиге, а состояние
    ожидания хук держит во временной папке системы: обе подменяются, чтобы
    тесты не читали и не писали настоящие.
    """

    idle_minutes: float = 0

    def setUp(self) -> None:
        super().setUp()
        self.home = Path(self._tmp.name) / "дом"
        self.temp = Path(self._tmp.name) / "tmp"
        self.temp.mkdir(parents=True, exist_ok=True)
        self.set_idle_minutes(self.idle_minutes)

    def set_idle_minutes(self, minutes: float) -> None:
        config = self.home / ".taskboard" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"notice_idle_minutes": minutes}),
                          encoding="utf-8")

    def env(self) -> dict:
        return dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                    TEMP=str(self.temp), TMP=str(self.temp),
                    TMPDIR=str(self.temp))

    def call(self, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOK_TEMPLATE)], input=json.dumps(event),
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env=self.env())

    def fake_notify(self) -> Path:
        """Подставной `tasks/notify.py`: записывает, с чем его позвали.

        Последний вызов — в `called.json`, все вызовы по строке — в
        `calls.log`: по нему считают, сколько раз позвали человека.
        """
        self.tasks.mkdir(parents=True, exist_ok=True)
        (self.tasks / "notify.py").write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "args = json.dumps(sys.argv[1:], ensure_ascii=False)\n"
            "Path(__file__).with_name('called.json').write_text("
            "args, encoding='utf-8')\n"
            "with open(Path(__file__).with_name('calls.log'), 'a',"
            " encoding='utf-8') as log:\n"
            "    log.write(args + '\\n')\n",
            encoding="utf-8")
        return self.tasks / "called.json"

    def calls(self) -> list:
        log = self.tasks / "calls.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line]


class AttentionHookBehaviourTest(_HookCall):
    """Хук зовёт скрипт проекта и ничего не решает за человека."""

    def args_for(self, notification_type: str, **extra) -> list:
        return self.args_for_event({"hook_event_name": "Notification",
                                    "notification_type": notification_type,
                                    **extra})

    def args_for_event(self, event: dict) -> list:
        called = self.fake_notify()
        done = self.call({"cwd": str(self.root), **event})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)
        return json.loads(called.read_text(encoding="utf-8"))

    def test_permission_request_calls_for_a_decision(self) -> None:
        args = self.args_for_event({
            "hook_event_name": "PermissionRequest", "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/build"}})

        self.assertIn("разрешени", args[0].lower())
        self.assertIn("--level", args)
        self.assertEqual("warning", args[args.index("--level") + 1])
        self.assertIn("--agent", args)
        self.assertIn("Claude Code", args)

    def test_question_is_an_answer_not_a_permission(self) -> None:
        """Вопрос с вариантами едет тем же событием — отличает его инструмент.

        Среда спрашивает решение и на диалог доступа, и на вопрос: без разбора
        `tool_name` человек видел бы «ждёт разрешения» там, где его просто
        спросили.
        """
        args = self.args_for_event({
            "hook_event_name": "PermissionRequest",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "какой вариант берём?"}]}})

        self.assertIn("ответ", args[0].lower())
        self.assertNotIn("разрешени", args[0].lower())
        self.assertEqual("info", args[args.index("--level") + 1])

    def test_question_text_never_travels(self) -> None:
        """В вопросе может стоять содержимое работы — доске оно не нужно."""
        secret = "внутренняя-формулировка"
        args = self.args_for_event({
            "hook_event_name": "PermissionRequest",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": secret}]}})

        self.assertNotIn(secret, json.dumps(args, ensure_ascii=False))

    def test_permission_request_decides_nothing(self) -> None:
        """Хук уведомляет, а не отвечает за человека: пустой stdout — согласие
        среды идти обычным путём, любой `decision` был бы решением за него."""
        called = self.fake_notify()
        done = self.call({"hook_event_name": "PermissionRequest",
                          "cwd": str(self.root), "tool_name": "Bash",
                          "tool_input": {"command": "git push"}})

        self.assertEqual(0, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertTrue(called.exists(), "уведомление всё же уходит")

    def test_permission_prompt_notification_is_ignored(self) -> None:
        """Второй конец того же разрешения: карточка о нём уже ушла."""
        called = self.fake_notify()
        done = self.call({"hook_event_name": "Notification",
                          "cwd": str(self.root),
                          "notification_type": "permission_prompt",
                          "message": "Claude needs your permission"})

        self.assertEqual(0, done.returncode)
        self.assertFalse(called.exists(), "иначе на разрешение придут две карточки")

    def test_idle_prompt_calls_for_an_answer(self) -> None:
        args = self.args_for("idle_prompt")

        self.assertIn("ответ", args[0].lower())
        self.assertEqual("info", args[args.index("--level") + 1])

    def test_two_moments_speak_differently(self) -> None:
        """Ради этого различения хук и заведён: поводы разные."""
        permission = self.args_for_event({"hook_event_name": "PermissionRequest",
                                          "tool_name": "Bash"})[0]
        self._tmp.cleanup()
        self.setUp()
        answer = self.args_for("idle_prompt")[0]

        self.assertNotEqual(permission, answer)

    def test_subagent_waiting_is_an_answer_too(self) -> None:
        args = self.args_for("agent_needs_input")

        self.assertIn("ответ", args[0].lower())

    def test_command_never_travels(self) -> None:
        """В команде инструмента может оказаться секрет — доске он не нужен."""
        secret = "команда-с-секретом"
        args = self.args_for_event({"hook_event_name": "PermissionRequest",
                                    "tool_name": "Bash",
                                    "tool_input": {"command": secret}})

        self.assertNotIn(secret, json.dumps(args, ensure_ascii=False))

    def test_other_notifications_are_silent(self) -> None:
        """Успешный вход и конец работы субагента человека не зовут."""
        for kind in ("auth_success", "agent_completed", "quota_auto_resume_fired"):
            with self.subTest(kind=kind):
                called = self.fake_notify()
                done = self.call({"hook_event_name": "Notification",
                                  "cwd": str(self.root),
                                  "notification_type": kind})
                self.assertEqual(0, done.returncode)
                self.assertFalse(called.exists(), "звать было не за чем")
                self._tmp.cleanup()
                self.setUp()

    def test_foreign_event_is_silent(self) -> None:
        called = self.fake_notify()
        done = self.call({"hook_event_name": "PostToolUse", "cwd": str(self.root)})

        self.assertEqual(0, done.returncode)
        self.assertFalse(called.exists())

    def test_missing_notify_script_is_silent(self) -> None:
        """Скрипта рядом нет — поставка старше уведомлений, и это не сбой."""
        self.root.mkdir(parents=True)

        done = self.call({"hook_event_name": "Notification", "cwd": str(self.root),
                          "notification_type": "permission_prompt"})

        self.assertEqual(0, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual("", done.stderr)



class IdleDelayTest(_HookCall):
    """Простой терминала зовёт человека не сразу и не больше раза за ожидание."""

    # Доли минуты: тесту нужна задержка в секунды, а не в минуты
    idle_minutes = 0.03
    SESSION = "сессия-1"

    def setUp(self) -> None:
        super().setUp()
        self.fake_notify()

    def event(self, name: str, **extra) -> None:
        payload = {"hook_event_name": name, "cwd": str(self.root),
                   "session_id": self.SESSION, **extra}
        done = self.call(payload)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)

    def idle(self, **extra) -> None:
        self.event("Notification", notification_type="idle_prompt", **extra)

    def wait_past_delay(self) -> None:
        time.sleep(self.idle_minutes * 60 + 3)

    def test_idle_waits_then_calls_once(self) -> None:
        started = time.monotonic()
        self.idle()

        self.assertLess(time.monotonic() - started, self.idle_minutes * 60,
                        "хук не должен держать среду на время задержки")
        self.assertEqual([], self.calls(), "звать сразу рано")
        self.wait_past_delay()
        calls = self.calls()
        self.assertEqual(1, len(calls))
        self.assertIn("ответ", calls[0][0].lower())

    def test_prompt_cancels_pending_call(self) -> None:
        self.idle()
        self.event("UserPromptSubmit", prompt="поехали дальше")

        self.wait_past_delay()
        self.assertEqual([], self.calls())

    def test_repeated_idle_gives_one_call(self) -> None:
        for _ in range(3):
            self.idle()

        self.wait_past_delay()
        self.assertEqual(1, len(self.calls()))

    def test_other_session_has_its_own_wait(self) -> None:
        self.idle()
        self.idle(session_id="сессия-2")

        self.wait_past_delay()
        self.assertEqual(2, len(self.calls()))


class WaiterProcessTest(unittest.TestCase):
    """Отложенная проверка не должна мелькать консольным окном."""

    def hook_module(self):
        spec = importlib.util.spec_from_file_location("attention_notify",
                                                      HOOK_TEMPLATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_waiter_runs_without_a_console(self) -> None:
        """У отсоединённого процесса консоли нет, и Windows выдаёт ему своё
        окно: консольным интерпретатором его запускать нельзя."""
        module = self.hook_module()
        chosen = Path(module.console_less_python())

        if os.name == "nt" and Path(sys.executable).with_name("pythonw.exe").is_file():
            self.assertEqual("pythonw.exe", chosen.name.lower())
        else:
            self.assertEqual(sys.executable, str(chosen))


class IdleWithoutDelayTest(_HookCall):
    """Задержка 0 — звать сразу, но всё так же раз на ожидание."""

    SESSION = "сессия-1"

    def setUp(self) -> None:
        super().setUp()
        self.fake_notify()

    def event(self, name: str, **extra) -> None:
        done = self.call({"hook_event_name": name, "cwd": str(self.root),
                          "session_id": self.SESSION, **extra})
        self.assertEqual(0, done.returncode, done.stderr)

    def idle(self) -> None:
        self.event("Notification", notification_type="idle_prompt")

    def test_zero_calls_at_once_and_once_per_wait(self) -> None:
        self.idle()
        self.assertEqual(1, len(self.calls()))

        self.idle()
        self.idle()
        self.assertEqual(1, len(self.calls()))

    def test_prompt_opens_a_new_wait(self) -> None:
        self.idle()
        self.event("UserPromptSubmit", prompt="ещё")
        self.idle()

        self.assertEqual(2, len(self.calls()))

    def test_agent_turn_without_prompt_opens_a_new_wait(self) -> None:
        """Агент продолжил сам (фоновая команда кончилась) и снова ждёт."""
        self.idle()
        self.event("Stop")
        self.idle()

        self.assertEqual(2, len(self.calls()))

    def test_question_and_permission_ignore_the_delay(self) -> None:
        """Они блокируют работу — задержка простоя к ним не относится."""
        self.set_idle_minutes(3)
        self.event("PermissionRequest", tool_name="AskUserQuestion")
        self.event("PermissionRequest", tool_name="Bash")

        self.assertEqual(2, len(self.calls()))

    def test_missing_setting_means_default_delay(self) -> None:
        """Настройку не сохраняли — звать сразу нельзя: действует умолчание."""
        (self.home / ".taskboard" / "config.json").unlink()
        self.idle()

        self.assertEqual([], self.calls())


if __name__ == "__main__":
    unittest.main()
