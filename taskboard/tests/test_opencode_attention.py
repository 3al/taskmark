# -*- coding: utf-8 -*-
"""Уведомления opencode, когда ход перешёл к человеку (TASK-283).

Встроенный `question` блокирует работу до ответа, а агент изнутри вызова себя
не видит — позвать человека в этот момент может только плагин. Шина событий
opencode даёт ровно два нужных повода, и различать их нечего придумывать:

- `question.asked` — задан вопрос (голубая карточка);
- `permission.asked` — ждёт разрешения (жёлтая).

Оба приходят в хук `event` в момент показа, пока решение ещё не принято
(проверено пробным плагином на opencode 1.15). Устаревший хук
`permission.ask` среда уже не вызывает, `session.idle` не отличает ожидание
ответа от конца реплики.

Поведение проверяется настоящим node, если он есть: плагин импортируется как
модуль и получает события, `tasks/notify.py` подменён записывающей заглушкой.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

PLUGIN = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
          / ".opencode" / "plugin" / "attention-notify.js")

OPENCODE_ONLY = {"claude": False, "opencode": True, "codex": False}
CLAUDE_ONLY = {"claude": True, "opencode": False, "codex": False}

NODE = shutil.which("node")

# Как долго ждём отсоединённый процесс уведомления: плагин его не ждёт, и
# заглушка пишет файл уже после выхода node
WAIT = 15


class _Project(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"

    def cfg(self, harnesses: dict) -> dict:
        return {**DEFAULTS, "harnesses": harnesses}


class OpencodeAttentionDeliveryTest(_Project):
    """Плагин — часть поставки opencode и только её."""

    def deploy(self, harnesses: dict) -> None:
        scaffold_project(self.tasks, self.cfg(harnesses), {"harnesses": harnesses})

    def test_plugin_deployed_with_opencode(self) -> None:
        self.deploy(OPENCODE_ONLY)

        self.assertTrue((self.root / ".opencode" / "plugin"
                         / "attention-notify.js").is_file())

    def test_other_harnesses_do_not_get_it(self) -> None:
        self.deploy(CLAUDE_ONLY)

        self.assertFalse((self.root / ".opencode" / "plugin"
                          / "attention-notify.js").exists())

    def test_missing_plugin_gets_banner_and_button_restores(self) -> None:
        self.deploy(OPENCODE_ONLY)
        plugin = self.root / ".opencode" / "plugin" / "attention-notify.js"
        plugin.unlink()

        issues = validate_project(self.tasks, self.cfg(OPENCODE_ONLY))["degraded"]
        missing = [i for i in issues if i["code"] == "no_hooks"]
        self.assertTrue(missing)
        self.assertIn("opencode/attention-notify.js", missing[0]["names"])

        scaffold_project(self.tasks, self.cfg(OPENCODE_ONLY), {"parts": ["hooks"]})
        self.assertTrue(plugin.is_file())


class OpencodeAttentionSourceTest(unittest.TestCase):
    """То, что видно из текста и не требует запуска."""

    def source(self) -> str:
        self.assertTrue(PLUGIN.is_file(), "плагин отсутствует в шаблонах")
        return PLUGIN.read_text(encoding="utf-8")

    def test_listens_to_the_two_moments(self) -> None:
        source = self.source()
        self.assertIn("question.asked", source)
        self.assertIn("permission.asked", source)

    def test_does_not_wait_for_notification(self) -> None:
        """Ждать нельзя: хуки плагинов opencode выполняются по очереди."""
        source = self.source()
        self.assertNotIn("spawnSync", source)
        self.assertIn("unref", source)


@unittest.skipUnless(NODE, "node не найден — поведение плагина не проверить")
class OpencodeAttentionBehaviourTest(_Project):
    """Плагин зовёт скрипт проекта и молчит на всё остальное."""

    def fake_notify(self) -> Path:
        self.tasks.mkdir(parents=True, exist_ok=True)
        (self.tasks / "notify.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(__file__).with_name('called.json').write_text(json.dumps({\n"
            "    'args': sys.argv[1:],\n"
            "    'session': os.environ.get('OPENCODE_SESSION_ID', ''),\n"
            "}, ensure_ascii=False), encoding='utf-8')\n",
            encoding="utf-8")
        return self.tasks / "called.json"

    def fire(self, event: dict) -> subprocess.CompletedProcess:
        """Загрузить плагин как модуль и отдать ему одно событие."""
        self.root.mkdir(parents=True, exist_ok=True)
        runner = Path(self._tmp.name) / "run.mjs"
        runner.write_text(
            "const [plugin, dir, event] = process.argv.slice(2)\n"
            "const mod = await import(new URL('file:///' + plugin.replace(/\\\\/g, '/')))\n"
            "const factory = Object.values(mod)[0]\n"
            "const hooks = await factory({ directory: dir, worktree: dir })\n"
            "await hooks.event({ event: JSON.parse(event) })\n"
            "console.log('ok')\n",
            encoding="utf-8")
        return subprocess.run(
            [str(NODE), str(runner), str(PLUGIN), str(self.root), json.dumps(event)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            # Не из репозитория: у него свой `tasks/notify.py`, и промах плагина
            # мимо проекта отправил бы настоящую карточку на доску
            cwd=self._tmp.name)

    def wait_call(self, called: Path) -> dict:
        """Чем позвали скрипт: аргументы и метка сессии в его окружении."""
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if called.is_file():
                try:
                    return json.loads(called.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            time.sleep(0.1)
        self.fail("уведомление не отправлено")

    def wait_args(self, called: Path) -> list:
        return self.wait_call(called)["args"]

    def args_for(self, event: dict) -> list:
        called = self.fake_notify()
        done = self.fire(event)
        self.assertEqual(0, done.returncode, done.stderr)
        return self.wait_args(called)

    def question(self, text: str = "какой вариант берём?") -> dict:
        return {"type": "question.asked", "properties": {
            "id": "que_1", "sessionID": "ses_1",
            "questions": [{"question": text, "header": "Вариант", "options": []}]}}

    def permission(self, pattern: str = "git push") -> dict:
        return {"type": "permission.asked", "properties": {
            "id": "per_1", "sessionID": "ses_1", "permission": "bash",
            "patterns": [pattern], "metadata": {}, "always": []}}

    def assert_silent(self, event: dict) -> None:
        called = self.fake_notify()
        done = self.fire(event)
        self.assertEqual(0, done.returncode, done.stderr)
        time.sleep(2)
        self.assertFalse(called.exists(), "звать было не за чем")

    def assert_dismisses(self, event: dict, scope: str = "env") -> None:
        """Повод отпал: плагин просит доску снять сказанное этой сессией."""
        called = self.fake_notify()
        done = self.fire(event)
        self.assertEqual(0, done.returncode, done.stderr)
        time.sleep(2)
        self.assertTrue(called.exists(), "отзыв не ушёл")
        call = json.loads(called.read_text(encoding="utf-8"))
        self.assertEqual(["--dismiss", scope], call["args"])
        session = event["properties"].get("sessionID") \
            or event["properties"].get("info", {}).get("sessionID")
        self.assertEqual(session, call["session"],
                         "без метки сессии гасить нечего")

    def test_shell_calls_get_the_session_too(self) -> None:
        """Сообщения агент шлёт сам, из шелла среды: без подстановки они
        приходят без метки, и ответ человека их не снимает."""
        runner = Path(self._tmp.name) / "shell.mjs"
        runner.write_text("\n".join([
            "const [plugin, dir, event] = process.argv.slice(2)",
            "const url = new URL('file:///' + plugin.split('\\\\').join('/'))",
            "const mod = await import(url)",
            "const factory = Object.values(mod)[0]",
            "const hooks = await factory({ directory: dir, worktree: dir })",
            "await hooks.event({ event: JSON.parse(event) })",
            "const out = { env: {} }",
            "await hooks['shell.env']({ cwd: dir }, out)",
            "console.log(JSON.stringify(out.env))",
        ]), encoding="utf-8")

        done = subprocess.run(
            [str(NODE), str(runner), str(PLUGIN), str(self.root),
             json.dumps(self.question())],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=self._tmp.name)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("ses_1", json.loads(done.stdout)["OPENCODE_SESSION_ID"])

    def test_call_carries_the_session(self) -> None:
        """Среда не кладёт сессию в окружение — её кладёт плагин: иначе
        карточка уходит безымянной и отзывать потом нечего."""
        called = self.fake_notify()
        self.fire(self.question())

        self.assertEqual("ses_1", self.wait_call(called)["session"])

    def test_question_calls_for_an_answer(self) -> None:
        args = self.args_for(self.question())

        self.assertIn("ответ", args[0].lower())
        self.assertNotIn("разрешени", args[0].lower())
        self.assertEqual("info", args[args.index("--level") + 1])
        self.assertEqual("opencode", args[args.index("--agent") + 1])

    def test_permission_calls_for_a_decision(self) -> None:
        args = self.args_for(self.permission())

        self.assertIn("разрешени", args[0].lower())
        self.assertEqual("warning", args[args.index("--level") + 1])

    def test_nothing_private_travels(self) -> None:
        """В вопросе и команде может оказаться содержимое работы или секрет."""
        secret = "секрет-в-команде"
        for event in (self.question(secret), self.permission(secret)):
            with self.subTest(event=event["type"]):
                args = self.args_for(event)
                self.assertNotIn(secret, json.dumps(args, ensure_ascii=False))
                self._tmp.cleanup()
                self.setUp()

    def test_other_events_do_not_call(self) -> None:
        """Конец хода не зовёт — но сказанное раньше уже никого не ждёт."""
        self.assert_dismisses({"type": "session.idle",
                               "properties": {"sessionID": "ses_1"}})

    def test_answer_dismisses_what_was_said(self) -> None:
        self.assert_dismisses({"type": "question.replied", "properties": {
            "sessionID": "ses_1", "requestID": "que_1", "answers": [["A"]]}})

    def test_permission_decision_dismisses_at_once(self) -> None:
        """Решение по разрешению шина называет прямо — ждать конца хода незачем."""
        self.assert_dismisses({"type": "permission.replied", "properties": {
            "sessionID": "ses_1", "permissionID": "per_1", "response": "once"}})

    def test_reply_dismisses_everything_said(self) -> None:
        """Ответил в терминале — прочёл и то, что агент сказал раньше: гаснут
        и его сообщения, а не только зовы среды."""
        self.assert_dismisses({"type": "message.updated", "properties": {
            "info": {"id": "msg_1", "role": "user", "sessionID": "ses_1"}}},
            scope="all")

    def test_same_reply_updates_dismiss_once(self) -> None:
        """Ту же реплику шина обновляет и внутри хода агента: приняв это за
        новый ответ, плагин погасил бы карточку, показанную секунду назад."""
        called = self.fake_notify()
        runner = Path(self._tmp.name) / "twice.mjs"
        runner.write_text("\n".join([
            "const [plugin, dir, events] = process.argv.slice(2)",
            "const url = new URL('file:///' + plugin.split('\\\\').join('/'))",
            "const hooks = await (Object.values(await import(url))[0])(",
            "  { directory: dir, worktree: dir })",
            "for (const event of JSON.parse(events)) {",
            "  await hooks.event({ event })",
            "  await new Promise((r) => setTimeout(r, 900))",
            "}",
            "console.log('ok')",
        ]), encoding="utf-8")
        reply = {"type": "message.updated", "properties": {
            "info": {"id": "msg_1", "role": "user", "sessionID": "ses_1"}}}
        question = self.question()

        done = subprocess.run(
            [str(NODE), str(runner), str(PLUGIN), str(self.root),
             json.dumps([reply, question, reply])],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=self._tmp.name)

        self.assertEqual(0, done.returncode, done.stderr)
        time.sleep(1)
        # Последним звали показ вопроса: повторное обновление реплики молчит
        self.assertNotIn("--dismiss",
                         json.loads(called.read_text(encoding="utf-8"))["args"])

    def test_reply_without_id_is_ignored(self) -> None:
        """Отличить новую реплику от обновления нечем — молчим: ложный отзыв
        гасит карточку, которую человек не видел."""
        self.assert_silent({"type": "message.updated", "properties": {
            "info": {"role": "user", "sessionID": "ses_1"}}})

    def test_agent_message_is_not_a_reply(self) -> None:
        """Собственные сообщения агента летят пачкой при каждом ответе."""
        self.assert_silent({"type": "message.updated", "properties": {
            "info": {"id": "msg_2", "role": "assistant", "sessionID": "ses_1"}}})

    def test_unrelated_event_stays_silent(self) -> None:
        self.assert_silent({"type": "file.edited",
                            "properties": {"file": "README.md"}})

    def test_missing_notify_script_is_silent(self) -> None:
        """Поставка старше уведомлений — не сбой."""
        done = self.fire(self.question())

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("ok", done.stdout.strip())

    def test_process_folder_is_not_the_project(self) -> None:
        """Скрипт в рабочей папке процесса — чужой: звать туда нельзя."""
        foreign = Path(self._tmp.name) / "tasks"
        foreign.mkdir()
        (foreign / "notify.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('called.json').write_text('[]')\n",
            encoding="utf-8")

        done = self.fire(self.question())
        self.assertEqual(0, done.returncode, done.stderr)
        time.sleep(2)
        self.assertFalse((foreign / "called.json").exists())


if __name__ == "__main__":
    unittest.main()
