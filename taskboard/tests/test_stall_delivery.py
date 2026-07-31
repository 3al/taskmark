"""Простой в поставке: правила, скиллы, срез очереди (TASK-044).

Третий слой эпика E001-STALL: механизм простоя доезжает до агентов. У мыши
есть подтверждение (TASK-017), у агента его нет — он просто не берёт стоящую
задачу, поэтому запрет для скиллов жёсткий.

Слабое место образца, от которого отталкивались: правило про `blocked_by` жило
только в тексте правил, проверки перед стартом не было, и соблюдение держалось
на дисциплине агента.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
AGENTIC = ROOT / "templates" / "agentic"
SKILLS = AGENTIC / ".claude" / "skills"
RULES = AGENTIC / "rules_section.md"
SCRIPT = ROOT / "templates" / "tasks" / "set_status.py"
DOCS = ROOT.parent / "docs" / "help"

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: todo
created: 2026-07-31 10:00
blocked_by: {blocked_by}
paused: {paused}
---

## Описание

Тестовая задача.
"""


class QueueMarksStalledTest(unittest.TestCase):
    """Срез очереди сам показывает, что стоит: иначе агент не отличит."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        board = ["# Board", "", "## To Do", ""]
        for task_id, title, blocked, paused in (
            ("TASK-013", "Свободная", "~", "~"),
            ("TASK-014", "Заблокированная", "TASK-013", "~"),
            ("TASK-015", "На паузе", "~", "ждём стенд"),
        ):
            name = f"{task_id}-{title.lower()}.md"
            (self.tasks / name).write_text(
                TASK_FILE.format(task_id=task_id, title=title,
                                 blocked_by=blocked, paused=paused),
                encoding="utf-8")
            board.append(f"- {task_id} · [{title}]({name})")
        board += ["", "## Development", "", "_(нет)_", ""]
        (self.tasks / "board.md").write_text("\n".join(board) + "\n", encoding="utf-8")
        (self.tasks / ".taskboard.json").write_text(json.dumps(
            {"pipeline": ["backlog", "todo", "development", "done"],
             "actions": {"create": "backlog", "pick": "todo", "start": "development"}}),
            encoding="utf-8")

    def queue(self) -> dict:
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), "--queue"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        return json.loads(done.stdout)

    def test_free_task_is_not_marked(self) -> None:
        item = next(t for t in self.queue()["tasks"] if t["id"] == "TASK-013")
        self.assertFalse(item["stalled"])

    def test_blocked_task_is_marked_with_reason(self) -> None:
        item = next(t for t in self.queue()["tasks"] if t["id"] == "TASK-014")
        self.assertTrue(item["stalled"], "заблокированная задача в очереди неотличима")
        self.assertEqual(["TASK-013"], item["blocked_by"])

    def test_paused_task_is_marked(self) -> None:
        item = next(t for t in self.queue()["tasks"] if t["id"] == "TASK-015")
        self.assertTrue(item["stalled"])
        self.assertEqual("ждём стенд", item["paused"])


class RulesDescribeStallTest(unittest.TestCase):
    """Правила поставки: агент узнаёт о простое из них, а не из кода."""

    def setUp(self) -> None:
        self.rules = RULES.read_text(encoding="utf-8")

    def test_fields_are_described(self) -> None:
        for field in ("blocked_by", "blocks", "paused"):
            self.assertIn(field, self.rules, f"в правилах нет поля {field}")

    def test_commands_are_given(self) -> None:
        for flag in ("--block", "--unblock", "--pause", "--resume", "--stalled"):
            self.assertIn(flag, self.rules, f"в правилах нет команды {flag}")

    def test_hard_ban_for_agents(self) -> None:
        """У мыши есть подтверждение, у агента его нет."""
        self.assertIn("не бери", self.rules.lower(),
                      "правила не запрещают брать стоящую задачу в работу")

    def test_two_ends_rule(self) -> None:
        self.assertIn("обе", self.rules.lower(),
                      "правила не говорят, что зависимость правится с двух сторон")


class SkillsRespectStallTest(unittest.TestCase):
    """Запрет закрепляется в скиллах, а не держится на дисциплине."""

    def skill(self, name: str) -> str:
        return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")

    def test_start_task_checks_stall(self) -> None:
        text = self.skill("start-task")
        self.assertIn("--stalled", text,
                      "start-task не проверяет простой перед переводом в работу")
        self.assertIn("не бери", text.lower(),
                      "start-task берёт стоящую задачу без оговорок")

    def test_start_task_skips_stalled_in_queue(self) -> None:
        text = self.skill("start-task")
        self.assertIn("stalled", text,
                      "остановленные задачи предлагаются из очереди наравне со свободными")

    def test_finalize_reports_unblocked(self) -> None:
        text = self.skill("finalize-task")
        self.assertIn("blocks", text,
                      "finalize-task не сообщает, какие задачи разблокировались")

    def test_new_task_keeps_both_ends(self) -> None:
        text = self.skill("new-task")
        self.assertIn("blocked_by", text)
        self.assertIn("обе", text.lower(),
                      "new-task не говорит про вторую сторону зависимости")


class HelpDescribesAgentBehaviourTest(unittest.TestCase):
    def test_agentic_help_mentions_stall(self) -> None:
        text = (DOCS / "05-agentic.md").read_text(encoding="utf-8").lower()
        self.assertIn("стоит", text, "в справке про агентов ничего о простое")
        self.assertIn("блокир", text)


if __name__ == "__main__":
    unittest.main()
