"""Два формата ввода срока в бэкенде и автономном скрипте (TASK-253)."""
import importlib.util
import unittest
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from backend.due_input import parse_due_input


class DueInputTest(unittest.TestCase):
    def test_calendar_and_formats_in_both_copies(self):
        path = Path(__file__).resolve().parents[1] / "templates/tasks/set_status.py"
        spec = importlib.util.spec_from_file_location("due_status", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = {
            "2026-09-12": date(2026, 9, 12),
            "2 дня": date(2024, 2, 2),
            "3 недели": date(2024, 2, 21),
            "1 месяц": date(2024, 2, 29),
            "13 месяцев": date(2025, 2, 28),
            "0 дней": date(2024, 1, 31),
            "завтра": None, "12.09": None, "5д": None,
            "2 часа": None, "-1 день": None, "1.5 недели": None,
            "20260912": None, "2026-W01-1": None, "2026-02-30": None,
            "999999999999999 месяцев": None,
        }
        for parse in (parse_due_input, module.parse_due_input):
            for value, expected in cases.items():
                with self.subTest(parser=parse.__module__, value=value):
                    self.assertEqual(expected, parse(value, date(2024, 1, 31)))

    def test_real_scripts_store_dates_and_refuse_before_writing(self):
        templates = Path(__file__).resolve().parents[1] / "templates/tasks"
        with tempfile.TemporaryDirectory() as folder:
            tasks = Path(folder) / "tasks"
            tasks.mkdir()
            for name in ("create_task.py", "set_status.py", "_TEMPLATE.md"):
                shutil.copyfile(templates / name, tasks / name)
            board = tasks / "board.md"
            board.write_text("# Board\n\n## Backlog\n\n_(нет)_\n", encoding="utf-8")

            def run(script, *args):
                return subprocess.run([sys.executable, str(tasks / script), *args],
                                      cwd=folder, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")

            before = board.read_bytes()
            bad = run("create_task.py", "-t", "Плохой срок", "--due", "2026-02-30")
            self.assertNotEqual(0, bad.returncode)
            self.assertEqual([], list(tasks.glob("TASK-*.md")))
            self.assertEqual(before, board.read_bytes())
            good = run("create_task.py", "-t", "Проверить срок", "--due", "2026-09-12")
            self.assertEqual(0, good.returncode, good.stdout + good.stderr)
            path = next(tasks.glob("TASK-*.md"))
            task_id = path.name.split("-", 2)[:2]
            task_id = "-".join(task_id)
            self.assertIn("due: 2026-09-12", path.read_text(encoding="utf-8"))
            result = run("set_status.py", task_id, "--due", "3 недели", "--agent", "test")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            expected = (date.today() + timedelta(days=21)).isoformat()
            self.assertIn("due: " + expected, path.read_text(encoding="utf-8"))
            before = path.read_bytes()
            result = run("set_status.py", task_id, "--due", "2 часа")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, path.read_bytes())
