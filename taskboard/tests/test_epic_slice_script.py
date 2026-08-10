"""Срез эпика в автономном скрипте: `set_status.py --epic <ключ>`.

TASK-075: состав эпика собирался грепом по `epic:` — находились файлы, а статусы
и заголовки приходилось добирать из frontmatter каждой задачи. Скрипт уже читает
и доску, и frontmatter, поэтому срез живёт рядом с `--queue`.

Порядок — **пайплайна проекта**, как в окне эпика (`backend/epics.py`): состав
читают как маршрут, по которому эпик едет. Два источника одного среза обязаны
показывать одно и то же.

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

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"

TASK = """---
id: {task_id}
title: {title}
epic: {epic}
type: feature
status: {status}
created: 2026-08-10 10:00
---

## Описание

Текст.
"""

PIPELINE = ["backlog", "todo", "development", "done", "cancelled"]

EPICS = """# Эпики

## Список эпиков

## E001-STALL — Простой задачи

## E056-18500 — Инвентаризация
"""


class EpicSliceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "epics.md").write_text(EPICS, encoding="utf-8")
        # Пайплайн задаём явно: у каждого проекта он свой, а порядок среза —
        # именно маршрут проекта, а не дефолтный набор статусов
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": PIPELINE}, ensure_ascii=False), encoding="utf-8")

        self.make("TASK-015", "Блокировки и пауза", "E001-STALL", "done")
        self.make("TASK-016", "Срез простоя", "E001-STALL", "todo")
        self.make("TASK-017", "Отменённая часть", "E001-STALL", "cancelled")
        self.make("TASK-018", "Чужая задача", "E056-18500", "todo")
        self.make("TASK-019", "Задача без эпика", "~", "todo")

    def make(self, task_id: str, title: str, epic: str, status: str) -> None:
        (self.tasks / f"{task_id}-{task_id.lower()}.md").write_text(
            TASK.format(task_id=task_id, title=title, epic=epic, status=status),
            encoding="utf-8")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_slice_lists_tasks_with_status_and_title(self) -> None:
        done = self.run_script("--epic", "E001-STALL")
        self.assertEqual(0, done.returncode, done.stderr)
        report = json.loads(done.stdout)

        self.assertEqual("E001-STALL", report["epic"])
        self.assertEqual("Простой задачи", report["name"])
        self.assertEqual(3, report["total"])

        by_id = {t["id"]: t for t in report["tasks"]}
        self.assertEqual({"TASK-015", "TASK-016", "TASK-017"}, set(by_id))
        self.assertEqual("todo", by_id["TASK-016"]["status"])
        self.assertEqual("Срез простоя", by_id["TASK-016"]["title"])
        self.assertIn("label", by_id["TASK-016"])
        self.assertTrue(by_id["TASK-016"]["file"].startswith("TASK-016"))

    def test_order_follows_pipeline_with_offramp_last(self) -> None:
        """Состав читают как маршрут: съезд (отмена) — в конце, за терминальным."""
        report = json.loads(self.run_script("--epic", "E001-STALL").stdout)
        self.assertEqual(["TASK-016", "TASK-015", "TASK-017"],
                         [t["id"] for t in report["tasks"]])

    def test_unknown_key_names_registered_epics(self) -> None:
        done = self.run_script("--epic", "E001-STALLL")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("E001-STALL", done.stderr)
        self.assertIn("E056-18500", done.stderr)

    def test_known_epic_without_tasks_is_empty_slice(self) -> None:
        """Ключ в реестре есть, задач нет — пустой срез, а не ошибка."""
        (self.tasks / "epics.md").write_text(EPICS + "\n## E900-EMPTY — Пустой\n",
                                             encoding="utf-8")
        done = self.run_script("--epic", "E900-EMPTY")
        self.assertEqual(0, done.returncode, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(0, report["total"])
        self.assertEqual([], report["tasks"])

    def test_slice_matches_backend(self) -> None:
        """Скрипт и окно эпика показывают один состав в одном порядке."""
        from backend.epics import epic_tasks
        from backend.statuses import load_pipeline

        report = json.loads(self.run_script("--epic", "E001-STALL").stdout)
        backend = epic_tasks(self.tasks, "E001-STALL", load_pipeline({"pipeline": PIPELINE}))

        self.assertEqual([t["id"] for t in backend], [t["id"] for t in report["tasks"]])
        self.assertEqual([t["status"] for t in backend],
                         [t["status"] for t in report["tasks"]])


if __name__ == "__main__":
    unittest.main()
