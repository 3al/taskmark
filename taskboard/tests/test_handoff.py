"""Тесты буфера хэндоффа между сессиями агентов (TASK-273).

Контекст сессии умирает вместе с ней, а файл задачи покрывает только задачу.
Буфер переживает переход в новую сессию — в том числе в другую среду, где
штатного возобновления нет вовсе, — и снова становится пустым.

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

from tests.test_set_status_script import (SCRIPT, TASK_FILE,  # noqa: E402
                                          load_script, render_board)

DRAFT = """## С чего начать

Дочитать `queue_ops.py` — перенос между разделами там же.

## Грабли

Сервер не подхватывает правки, пока его не перезапустишь.
"""


class HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        (self.tasks / "board.md").write_text(render_board(), encoding="utf-8")
        self.mod = load_script()
        self.draft = self.root / "draft.md"
        self.draft.write_text(DRAFT, encoding="utf-8")
        self.buffer = self.tasks / "handoff.md"

    def _write(self, agent: str = "Claude Opus 5", harness: str = "claude") -> dict:
        return self.mod.handoff_write(self.tasks, self.draft, agent=agent,
                                      harness=harness)

    def _age(self, minutes: int) -> None:
        """Состарить буфер: время записи — это то, что стоит в его шапке."""
        from datetime import datetime, timedelta

        stamp = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
        text = self.buffer.read_text(encoding="utf-8")
        self.buffer.write_text(text.replace(text.splitlines()[1],
                                            f"written: {stamp}", 1), encoding="utf-8")

    # --- запись ---

    def test_write_creates_buffer_with_header(self) -> None:
        result = self._write()
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.buffer.is_file(), "буфер не создан")
        text = self.buffer.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "нет шапки")
        self.assertIn("agent: Claude Opus 5", text)
        self.assertIn("harness: claude", text)
        self.assertIn("Дочитать `queue_ops.py`", text, "тело черновика потерялось")

    def _task(self, task_id: str, status: str, title: str = "Тестовая") -> None:
        (self.tasks / f"{task_id}-test.md").write_text(
            TASK_FILE.format(task_id=task_id, title=title, status=status),
            encoding="utf-8")

    def test_write_takes_the_tasks_it_is_given(self) -> None:
        """Задачи сессии называет агент: какие он трогал, знает только он.

        Рабочий статус этого не заменяет — в разговоре живут и уже сданные, и
        ждущие выпуска, а стартовать новая сессия должна с той же точки.
        """
        self._task("TASK-001", "development")
        self._task("TASK-002", "testing")
        self._task("TASK-003", "ready_for_release")
        self.mod.handoff_write(self.tasks, self.draft, agent="Claude Opus 5",
                               tasks="TASK-002, TASK-003")
        header = self.buffer.read_text(encoding="utf-8")
        self.assertIn("tasks: TASK-002, TASK-003", header)
        self.assertNotIn("TASK-001", header, "подставлена задача, которой не называли")

    def test_write_falls_back_to_tasks_in_work(self) -> None:
        """Агент задач не назвал — лучше взять те, что в работе, чем ничего."""
        self._task("TASK-001", "development")
        self._write()
        self.assertIn("tasks: TASK-001", self.buffer.read_text(encoding="utf-8"))

    def test_read_returns_current_status_of_each_task(self) -> None:
        """Статус задач читается **сейчас**, а не берётся из буфера.

        Так буфер не становится вторым местом с той же правдой: в нём остаётся
        то, чего нигде нет, а состояние доедет из файлов задач.
        """
        self._task("TASK-002", "testing", title="Буфер хэндоффа")
        self.mod.handoff_write(self.tasks, self.draft, tasks="TASK-002")

        # Задачу двигают уже после записи буфера — читатель должен увидеть новое
        (self.tasks / "TASK-002-test.md").write_text(
            TASK_FILE.format(task_id="TASK-002", title="Буфер хэндоффа",
                             status="done"), encoding="utf-8")

        info = self.mod.handoff_read(self.tasks)
        entry = info["tasks"][0]
        self.assertEqual("TASK-002", entry["id"])
        self.assertEqual("done", entry["status"], "статус взят из буфера, а не из задачи")
        self.assertEqual("Буфер хэндоффа", entry["title"])

    def test_read_marks_missing_task(self) -> None:
        """Задачу могли удалить или переименовать — молчать об этом нельзя."""
        self.mod.handoff_write(self.tasks, self.draft, tasks="TASK-404")
        entry = self.mod.handoff_read(self.tasks)["tasks"][0]
        self.assertEqual("TASK-404", entry["id"])
        self.assertFalse(entry["found"])

    def test_write_refuses_when_previous_not_taken(self) -> None:
        """Буфер на месте и его не забрали — переход не состоялся.

        Ни дозаписывать, ни затирать чужое нельзя: и то и другое теряет работу
        соседней сессии молча.
        """
        self._write()
        before = self.buffer.read_text(encoding="utf-8")

        self.draft.write_text("## Другое\n\nСовсем другая работа.\n", encoding="utf-8")
        result = self._write(agent="Codex")
        self.assertFalse(result["ok"], "запись поверх незабранного буфера прошла")
        self.assertTrue(result.get("error"), "отказ без причины")
        self.assertEqual(before, self.buffer.read_text(encoding="utf-8"),
                         "чужой буфер изменился")

    def test_write_after_clear_works(self) -> None:
        self._write()
        self.mod.handoff_clear(self.tasks)
        self.assertTrue(self._write()["ok"], "после очистки записать нельзя")

    def test_write_needs_a_draft(self) -> None:
        result = self.mod.handoff_write(self.tasks, self.root / "нет.md")
        self.assertFalse(result["ok"])

    # --- чтение ---

    def test_read_empty(self) -> None:
        info = self.mod.handoff_read(self.tasks)
        self.assertFalse(info["exists"])

    def test_read_fresh(self) -> None:
        self._write()
        info = self.mod.handoff_read(self.tasks)
        self.assertTrue(info["exists"])
        self.assertTrue(info["fresh"], "только что записанный буфер не свежий")
        self.assertLess(info["age_minutes"], 2)
        self.assertIn("Дочитать `queue_ops.py`", info["body"])
        self.assertEqual("Claude Opus 5", info["agent"])
        self.assertEqual("claude", info["harness"])

    def test_read_stale_keeps_the_body(self) -> None:
        """Час — граница доверия, а не удаления: содержимое остаётся видно."""
        self._write()
        self._age(90)
        info = self.mod.handoff_read(self.tasks)
        self.assertTrue(info["exists"])
        self.assertFalse(info["fresh"], "буфер старше часа считается свежим")
        self.assertGreaterEqual(info["age_minutes"], 90)
        self.assertIn("Дочитать `queue_ops.py`", info["body"],
                      "протухший буфер потерял содержимое")

    def test_hour_is_the_border(self) -> None:
        self._write()
        self._age(59)
        self.assertTrue(self.mod.handoff_read(self.tasks)["fresh"])
        self._age(61)
        self.assertFalse(self.mod.handoff_read(self.tasks)["fresh"])

    def test_read_does_not_clear(self) -> None:
        """Чтение — не очистка: стирает отдельный вызов, и скилл зовёт его сам."""
        self._write()
        self.mod.handoff_read(self.tasks)
        self.assertTrue(self.buffer.is_file())

    # --- очистка ---

    def test_clear_removes_buffer(self) -> None:
        self._write()
        result = self.mod.handoff_clear(self.tasks)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["existed"])
        self.assertFalse(self.buffer.exists())
        self.assertFalse(self.mod.handoff_read(self.tasks)["exists"])

    def test_clear_without_buffer_is_not_an_error(self) -> None:
        result = self.mod.handoff_clear(self.tasks)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["existed"])

    # --- командная строка ---

    def test_cli_round_trip(self) -> None:
        """Скиллы зовут скрипт, а не считают возраст сами."""
        write = subprocess.run(
            [sys.executable, str(SCRIPT), "--handoff-write", str(self.draft),
             "--agent", "Claude Opus 5", "--harness", "claude",
             "--tasks", "TASK-271, TASK-272",
             "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, write.returncode, write.stderr)

        read = subprocess.run(
            [sys.executable, str(SCRIPT), "--handoff-read",
             "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, read.returncode, read.stderr)
        info = json.loads(read.stdout)
        self.assertTrue(info["exists"])
        self.assertTrue(info["fresh"])
        self.assertIn("queue_ops.py", info["body"])
        self.assertEqual(["TASK-271", "TASK-272"], [t["id"] for t in info["tasks"]])

        clear = subprocess.run(
            [sys.executable, str(SCRIPT), "--handoff-clear",
             "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, clear.returncode, clear.stderr)
        self.assertFalse(self.buffer.exists())

    def test_cli_refuses_second_write(self) -> None:
        for _ in range(1):
            self._write()
        second = subprocess.run(
            [sys.executable, str(SCRIPT), "--handoff-write", str(self.draft),
             "--agent", "Codex", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(0, second.returncode,
                            "скрипт не отказал во второй записи")


class HandoffDeliveryTest(unittest.TestCase):
    """Буфер — часть поставки: скиллы, обёртки и объявленная возможность."""

    ROOT = Path(__file__).resolve().parent.parent
    SKILLS = ROOT / "templates" / "agentic" / ".claude" / "skills"
    COMMANDS = ROOT / "templates" / "agentic" / ".opencode" / "commands"

    def test_skills_shipped(self) -> None:
        for name in ("write-handoff", "read-handoff"):
            with self.subTest(skill=name):
                self.assertTrue((self.SKILLS / name / "SKILL.md").is_file(),
                                f"скилла {name} нет в поставке")

    def test_opencode_wrappers_shipped(self) -> None:
        for name in ("write-handoff", "read-handoff"):
            with self.subTest(command=name):
                self.assertTrue((self.COMMANDS / f"{name}.md").is_file(),
                                f"обёртки opencode для {name} нет")

    def test_script_declares_capability(self) -> None:
        """Старый скрипт в проекте пользователя обязан опознаться устаревшим."""
        script = load_script()
        self.assertIn("handoff", script.SCRIPT_CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
