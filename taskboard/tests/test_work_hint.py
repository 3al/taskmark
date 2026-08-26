"""Подсказка о задаче в работе для хуков среды (TASK-197).

Скрипт видит только собственные вызовы, а коммит, push и запрос на слияние
проходят мимо него — именно так работа и уезжает наружу, пока задача числится
в разработке. Хук среды видит сам вызов инструмента, но решение принимает
скрипт: иначе одно правило пришлось бы писать дважды, на JSON и на JS.

Подсказка **не блокирует**: коммит в середине работы законен, а запрет учил бы
его обходить.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_finish_reminders import PLAIN_CFG, Project  # noqa: E402

AGENTIC = (Path(__file__).resolve().parent.parent / "templates" / "agentic")


class WorkHintTest(Project):
    """Срез отвечает на один вопрос: есть ли задача в рабочем статусе."""

    CFG = PLAIN_CFG

    def hint(self) -> dict:
        return self.mod.work_hint(self.tasks)

    def test_silent_without_work(self) -> None:
        self.make("TASK-001", status="todo", section="## To Do")

        answer = self.hint()

        self.assertEqual([], answer["in_work"])
        self.assertEqual("", answer["hint"])

    def test_names_the_task_in_work(self) -> None:
        self.make("TASK-002", status="development", section="## Development")

        answer = self.hint()

        self.assertEqual(["TASK-002"], answer["in_work"])
        self.assertIn("TASK-002", answer["hint"])
        self.assertIn("handoff-task", answer["hint"])

    def test_several_tasks_are_all_named(self) -> None:
        self.make("TASK-003", status="development", section="## Development")
        self.make("TASK-004", title="Вторая", status="development",
                  section="## Development")

        answer = self.hint()

        self.assertEqual({"TASK-003", "TASK-004"}, set(answer["in_work"]))

    def test_cli_returns_json_and_zero(self) -> None:
        """Хук зовёт срез командой: ответ разбирается, код возврата не пугает среду."""
        self.make("TASK-005", status="development", section="## Development")

        done = subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), "--work-hint"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(["TASK-005"], json.loads(done.stdout)["in_work"])


class ClaudeHookTest(Project):
    """Обработчик Claude Code отдаёт подсказку как additionalContext."""

    CFG = PLAIN_CFG

    def hook_path(self) -> Path:
        return self.tasks.parent / ".claude" / "hooks" / "work-hint.py"

    def call(self, command: str) -> dict:
        event = {"hook_event_name": "PostToolUse", "cwd": str(self.tasks.parent),
                 "tool_name": "Bash", "tool_input": {"command": command}}
        done = subprocess.run(
            [sys.executable, str(self.hook_path())],
            input=json.dumps(event), capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout) if done.stdout.strip() else {}

    def test_deployed_with_the_environment(self) -> None:
        self.assertTrue(self.hook_path().is_file(),
                        "обработчик — часть агентского окружения")

    def test_commit_gets_the_hint(self) -> None:
        self.make("TASK-001", status="development", section="## Development")

        answer = self.call("git commit -m 'правка'")

        context = answer.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("TASK-001", context)

    def test_other_commands_are_silent(self) -> None:
        """Хук висит на всех Bash-вызовах, но говорит только про уход работы наружу."""
        self.make("TASK-002", status="development", section="## Development")

        self.assertEqual({}, self.call("ls -la"))

    def test_silent_without_work(self) -> None:
        self.make("TASK-003", status="todo", section="## To Do")

        self.assertEqual({}, self.call("git commit -m 'правка'"))

    def test_never_blocks(self) -> None:
        """Подсказка не запрет: код возврата 0, решения о запрете в ответе нет."""
        self.make("TASK-004", status="development", section="## Development")

        answer = self.call("git push")

        self.assertNotIn("permissionDecision", json.dumps(answer))


class DeliveryTest(Project):
    """Часть поставки: отсутствие видно валидатору и чинится кнопкой."""

    CFG = PLAIN_CFG

    def hook_path(self) -> Path:
        return self.tasks.parent / ".claude" / "hooks" / "work-hint.py"

    def codes(self) -> list[str]:
        from backend.validator import validate_project

        cfg = {**self.CFG, "harnesses": {"claude": True, "opencode": False}}
        return [d["code"] for d in validate_project(self.tasks, cfg)["degraded"]]

    def test_missing_part_is_reported(self) -> None:
        self.hook_path().unlink()

        self.assertIn("no_hooks", self.codes())

    def test_button_restores_it(self) -> None:
        """Баннер без кнопки только сообщает о проблеме — чинить должно нажатие."""
        from backend.scaffold import scaffold_project

        self.hook_path().unlink()

        scaffold_project(self.tasks, {**self.CFG,
                                      "harnesses": {"claude": True, "opencode": False}},
                         {"parts": ["hooks"]})

        self.assertTrue(self.hook_path().is_file())
        self.assertNotIn("no_hooks", self.codes())

    def test_deployed_part_is_quiet(self) -> None:
        self.assertNotIn("no_hooks", self.codes())


class BannerButtonTest(unittest.TestCase):
    """У кода в баннере должно быть действие: иначе кнопки не будет."""

    def app_source(self) -> str:
        path = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.jsx")
        return path.read_text(encoding="utf-8")

    def test_missing_has_a_button(self) -> None:
        self.assertIn("no_hooks:", self.app_source())

    def test_outdated_opens_details(self) -> None:
        self.assertIn("outdated_hooks:", self.app_source())


class OpencodePluginTest(unittest.TestCase):
    """Плагин opencode — тот же спусковой крючок на другом языке."""

    def source(self) -> str:
        path = AGENTIC / ".opencode" / "plugin" / "work-hint.js"
        self.assertTrue(path.is_file(), "плагин отсутствует в шаблонах")
        return path.read_text(encoding="utf-8")

    def test_hooks_after_the_call(self) -> None:
        self.assertIn("tool.execute.after", self.source())

    def test_appends_instead_of_throwing(self) -> None:
        """Блокировать нельзя: коммит в середине работы законен."""
        source = self.source()
        self.assertIn("output.output", source)
        self.assertNotIn("throw new Error", source)

    def test_asks_the_script(self) -> None:
        """Решение принимает скрипт — иначе правило написано дважды."""
        self.assertIn("--work-hint", self.source())


if __name__ == "__main__":
    unittest.main()
