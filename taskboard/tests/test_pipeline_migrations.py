"""Тесты миграций доски при смене состава статусов пайплайна.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.migrations import apply_config_migrations, pipeline_removals  # noqa: E402
from backend.scaffold import render_sections  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-07-27
patch: ~
---

## Описание

Тестовая задача.
"""


class PipelineMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        self.tasks.mkdir(parents=True)
        self.board = self.tasks / "board.md"
        self.old = {"pipeline": ["backlog", "queued", "development", "review",
                                 "testing", "completed"]}
        self._write_board(self.old)

    def _write_board(self, cfg: dict) -> None:
        template = (Path(__file__).resolve().parent.parent / "templates" / "tasks" / "board.md")
        self.board.write_text(
            template.read_text(encoding="utf-8").format(
                sections=render_sections(load_pipeline(cfg))),
            encoding="utf-8")

    def _add_task(self, task_id: str, status: str, section: str) -> Path:
        path = self.tasks / f"{task_id}-test.md"
        path.write_text(TASK_FILE.format(task_id=task_id, title="Тестовая", status=status),
                        encoding="utf-8")
        lines = self.board.read_text(encoding="utf-8").splitlines()
        idx = lines.index(f"## {section}")
        entry = f"- {task_id} · [Тестовая]({path.name}) · Агент · 2026-07-27"
        for i in range(idx + 1, len(lines)):
            if lines[i].strip() == "_(нет)_":
                lines[i] = entry
                break
        else:
            lines.insert(idx + 2, entry)
        self.board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _status_of(self, path: Path) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("status:"):
                return line.split(":", 1)[1].strip()
        return ""

    # --- Включение статуса ---

    def test_enabled_status_gets_section_in_order(self) -> None:
        """Раздел встаёт на своё место, а не сваливается в конец доски."""
        new = {"pipeline": ["backlog", "queued", "development", "review",
                            "to_testing", "testing", "completed"]}
        actions = apply_config_migrations(self.tasks, self.old, new)

        headings = [ln for ln in self.board.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("## ")]
        self.assertEqual(["## Backlog", "## Queue", "## Development", "## Review",
                          "## To Testing", "## Testing", "## Completed"], headings)
        self.assertTrue(any("To Testing" in a for a in actions), actions)

    # --- Выключение статуса ---

    def test_disabled_empty_status_section_removed(self) -> None:
        """Пустой раздел выключенного статуса уходит с доски: файл = доска."""
        new = {"pipeline": ["backlog", "queued", "development", "testing", "completed"]}
        actions = apply_config_migrations(self.tasks, self.old, new)

        content = self.board.read_text(encoding="utf-8")
        self.assertNotIn("## Review", content)
        self.assertIn("## Testing", content)
        self.assertTrue(any("Review" in a for a in actions), actions)

    def test_disabled_status_with_tasks_needs_a_decision(self) -> None:
        """Задачи в выключаемом разделе — повод спросить, а не решать молча."""
        self._add_task("TASK-001", "review", "Review")
        new = {"pipeline": ["backlog", "queued", "development", "testing", "completed"]}

        removals = pipeline_removals(self.tasks, self.old, new)
        self.assertEqual(1, len(removals))
        self.assertEqual("review", removals[0]["status"])
        self.assertEqual(1, removals[0]["count"])
        self.assertEqual("development", removals[0]["suggested"],
                         "по умолчанию — назад, чтобы задача не проскочила проверку")

    def test_tasks_move_with_frontmatter(self) -> None:
        """Переносятся оба конца правды: строка доски и status в файле задачи."""
        task = self._add_task("TASK-001", "review", "Review")
        new = {"pipeline": ["backlog", "queued", "development", "testing", "completed"]}

        actions = apply_config_migrations(self.tasks, self.old, new,
                                          moves={"review": "development"})

        content = self.board.read_text(encoding="utf-8")
        self.assertNotIn("## Review", content)
        dev = content.split("## Development", 1)[1].split("##", 1)[0]
        self.assertIn("TASK-001", dev, "задача не переехала в целевой раздел")
        self.assertEqual("development", self._status_of(task))
        self.assertTrue(any("задач" in a for a in actions), actions)

    def test_move_target_from_user_choice(self) -> None:
        """Выбор пользователя важнее предложения по умолчанию."""
        task = self._add_task("TASK-001", "review", "Review")
        new = {"pipeline": ["backlog", "queued", "development", "testing", "completed"]}

        apply_config_migrations(self.tasks, self.old, new, moves={"review": "testing"})

        self.assertEqual("testing", self._status_of(task))
        content = self.board.read_text(encoding="utf-8")
        testing = content.split("## Testing", 1)[1].split("##", 1)[0]
        self.assertIn("TASK-001", testing)

    def test_full_reconfiguration(self) -> None:
        """Реальный сценарий: убрали review, queued→todo, completed→done, добавили cancelled.

        Раньше задачи не переезжали: перенос шёл до создания целевых разделов,
        не находил их и молча возвращал ноль — на доске оставались обе колонки.
        """
        queued = self._add_task("TASK-001", "queued", "Queue")
        done = self._add_task("TASK-002", "completed", "Completed")
        new = {"pipeline": ["backlog", "todo", "development", "testing", "done", "cancelled"]}

        removals = pipeline_removals(self.tasks, self.old, new)
        self.assertEqual({"Queue", "Completed"}, {r["section"] for r in removals})

        apply_config_migrations(self.tasks, self.old, new,
                                moves={"Queue": "todo", "Completed": "done"})

        content = self.board.read_text(encoding="utf-8")
        headings = [ln for ln in content.splitlines() if ln.startswith("## ")]
        self.assertEqual(["## Backlog", "## To Do", "## Development", "## Testing",
                          "## Done", "## Cancelled"], headings)

        todo = content.split("## To Do", 1)[1].split("##", 1)[0]
        self.assertIn("TASK-001", todo, "задача не переехала в новый раздел очереди")
        self.assertTrue(todo.startswith("\n\n- TASK-001"),
                        f"раздел слипся с заголовком: {todo[:40]!r}")
        self.assertNotIn("\n\n\n", content, "лишние пустые строки на доске")
        done_section = content.split("## Done", 1)[1].split("##", 1)[0]
        self.assertIn("TASK-002", done_section)
        self.assertEqual("todo", self._status_of(queued))
        self.assertEqual("done", self._status_of(done))

    def test_orphan_sections_picked_up_later(self) -> None:
        """Разделы, осиротевшие раньше, подбираются следующим сохранением."""
        self._add_task("TASK-001", "queued", "Queue")
        new = {"pipeline": ["backlog", "todo", "development", "testing", "completed"]}
        # Первое сохранение: пайплайн уже новый, но задача осталась в Queue
        self._write_board(self.old)
        self.board.write_text(self.board.read_text(encoding="utf-8"), encoding="utf-8")
        self._add_task("TASK-001", "queued", "Queue")

        removals = pipeline_removals(self.tasks, new, new)
        self.assertEqual(["Queue"], [r["section"] for r in removals],
                         "осиротевший раздел не замечен")

        apply_config_migrations(self.tasks, new, new, moves={"Queue": "todo"})
        content = self.board.read_text(encoding="utf-8")
        self.assertNotIn("## Queue", content)
        self.assertIn("TASK-001", content.split("## To Do", 1)[1].split("##", 1)[0])

    def test_no_removals_when_pipeline_unchanged(self) -> None:
        self.assertEqual([], pipeline_removals(self.tasks, self.old, dict(self.old)))

    # --- Правила для агентов ---

    def test_rules_section_follows_pipeline(self) -> None:
        """Иначе в AGENTS.md остаётся прежний маршрут, и агент идёт по нему."""
        rules = self.root / "AGENTS.md"
        rules.write_text(
            "# 1. Проект\n\nОписание проекта.\n\n"
            "# 2. TASK MANAGEMENT\n\n## Жизненный цикл статуса\n\n"
            "backlog → queued → development → review → testing → completed\n",
            encoding="utf-8")

        new = {"pipeline": ["backlog", "queued", "development", "local_testing", "completed"]}
        actions = apply_config_migrations(self.tasks, self.old, new)

        content = rules.read_text(encoding="utf-8")
        self.assertIn("# 1. Проект", content, "чужой текст файла не должен пострадать")
        self.assertIn("Описание проекта.", content)
        self.assertIn("backlog → queued → development → local_testing → completed", content)
        self.assertNotIn("review", content)
        self.assertTrue(any("AGENTS.md" in a for a in actions), actions)


if __name__ == "__main__":
    unittest.main()
