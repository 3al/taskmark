"""Хук-напоминание о скилле scenarios: что агент получает после вызова скилла.

Хук — часть прототипа эксперимента со сценариями и живёт в `tools/`: в
поставку пользователям он не идёт, а подключается локальной записью в
`.claude/settings.json` этого репозитория. Проверяется так, как его зовёт
среда: отдельным процессом, событие — в stdin, ответ — в stdout.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend import version

ROOT = version.VERSION_FILE.resolve().parent.parent
HOOK = ROOT / "tools" / "scenarios_hint.py"


class ScenariosHintTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "tasks").mkdir()

    def _task(self, num: str, task_type: str | None) -> None:
        type_line = f"type: {task_type}\n" if task_type is not None else ""
        (self.root / "tasks" / f"TASK-{num}-задача.md").write_text(
            f"---\nid: TASK-{num}\ntitle: Задача\n{type_line}status: todo\n---\n\n"
            "## Описание\n\ntype: bug в тексте не считается\n", encoding="utf-8")

    def _run(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HOOK)], input=stdin.encode("utf-8"),
                              capture_output=True, timeout=30)

    def _call(self, skill: str, args: str | None) -> str:
        tool_input = {"skill": skill}
        if args is not None:
            tool_input["args"] = args
        event = {"hook_event_name": "PostToolUse", "tool_name": "Skill",
                 "tool_input": tool_input, "cwd": str(self.root)}
        done = self._run(json.dumps(event, ensure_ascii=False))
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        out = done.stdout.decode("utf-8").strip()
        if not out:
            return ""
        payload = json.loads(out)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_start_and_fix_remind_scenarios_before_plan(self) -> None:
        self._task("042", "feature")
        self._task("043", "bug")
        for skill, num in (("start-task", "042"), ("fix-task", "043")):
            with self.subTest(skill=skill):
                hint = self._call(skill, f"TASK-{num} доп. контекст")
                self.assertIn(f"scenarios TASK-{num}", hint)
                self.assertNotIn("трасса", hint)
                self.assertIn("до плана", hint)

    def test_handoff_reminds_trace(self) -> None:
        self._task("042", "bug")

        hint = self._call("handoff-task", "TASK-042")

        self.assertIn("scenarios TASK-042 трасса", hint)

    def test_silent_when_nothing_to_say(self) -> None:
        self._task("042", "feature")
        self._task("044", "discussion")
        self._task("045", None)
        cases = (("new-task", "TASK-042"),      # другой скилл
                 ("start-task", "TASK-044"),    # другой тип
                 ("start-task", "TASK-045"),    # типа нет
                 ("start-task", None),          # номера нет
                 ("start-task", "TASK-999"))    # файла нет
        for skill, args in cases:
            with self.subTest(skill=skill, args=args):
                self.assertEqual(self._call(skill, args), "")

    def test_broken_input_is_silent_success(self) -> None:
        for stdin in ("", "не json", "[]", '{"tool_input": "строка"}'):
            with self.subTest(stdin=stdin):
                done = self._run(stdin)
                self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
                self.assertEqual(done.stdout.strip(), b"")


if __name__ == "__main__":
    unittest.main()
