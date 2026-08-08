"""Секция «Заметки агента» переименована в «Комментарии» (TASK-131).

Имя описывало автора, а автор давно не один: в секцию пишет и человек с доски
— подтверждение требования этапа и снятие подтверждения при возврате назад.
Правильное имя — по содержимому.

Существующие задачи переезжают **разовой миграцией**, а не поддержкой двух имён
при чтении: второе имя, оставленное «на совместимость», остаётся навсегда.

Тонкость, ради которой миграция и написана осторожно: `set_status.py` живёт в
проекте развёрнутой копией и обновляется отдельно, кнопкой. Пойди миграция
впереди него — старая копия не нашла бы знакомого заголовка и завела рядом
**вторую** секцию. Поэтому проход идёт только там, где копия про новое имя уже
знает, а сам скрипт чинит отставший заголовок на месте.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.migrations import rename_notes_section  # noqa: E402
from backend.notes import LEGACY_NOTES_SECTION, NOTES_SECTION, append_note  # noqa: E402
from tests.test_set_status_script import load_script  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
SCRIPT_TEMPLATE = TEMPLATES / "tasks" / "set_status.py"

LEGACY_TASK = """---
id: TASK-001
title: Тестовая
status: backlog
---

## Описание

Текст.

## Заметки агента

- **2026-08-01 10:00** · Claude Opus 5 · первая строка

## История коммитов
"""


class ProjectCase(unittest.TestCase):
    """Проект во временной папке: миграция трогает файлы на диске."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.cfg = {"status_script": "set_status.py"}

    def deploy_script(self, *, fresh: bool = True) -> None:
        """Развёрнутая копия скрипта: нынешняя поставка или копия до TASK-131."""
        text = SCRIPT_TEMPLATE.read_text(encoding="utf-8")
        if not fresh:
            text = text.replace(', "comments"', "")
        (self.tasks / "set_status.py").write_text(text, encoding="utf-8")

    def task(self, task_id: str = "TASK-001", body: str = LEGACY_TASK) -> Path:
        path = self.tasks / f"{task_id}-test.md"
        path.write_text(body.replace("TASK-001", task_id), encoding="utf-8")
        return path


class RenameMigrationTest(ProjectCase):
    """Разовый проход по файлам задач проекта."""

    def test_heading_renamed_in_all_tasks(self) -> None:
        self.deploy_script()
        first, second = self.task("TASK-001"), self.task("TASK-002")

        actions = rename_notes_section(self.tasks, self.cfg)

        for path in (first, second):
            text = path.read_text(encoding="utf-8")
            self.assertIn(NOTES_SECTION, text, f"{path.name}: секция не переименована")
            self.assertNotIn(LEGACY_NOTES_SECTION, text, f"{path.name}: прежнее имя осталось")
        self.assertTrue(actions, "миграция не отчиталась о работе")

    def test_content_survives(self) -> None:
        """Меняется имя секции, а не её содержимое."""
        self.deploy_script()
        path = self.task()

        rename_notes_section(self.tasks, self.cfg)

        text = path.read_text(encoding="utf-8")
        self.assertIn("- **2026-08-01 10:00** · Claude Opus 5 · первая строка", text)
        self.assertLess(text.index(NOTES_SECTION), text.index("## История коммитов"),
                        "порядок секций сбился")

    def test_idempotent(self) -> None:
        self.deploy_script()
        self.task()

        rename_notes_section(self.tasks, self.cfg)
        again = rename_notes_section(self.tasks, self.cfg)

        self.assertEqual(again, [], "повторный проход снова что-то переименовал")

    def test_waits_for_deployed_script(self) -> None:
        """Копия скрипта старше нового имени — файлы не трогаем.

        Иначе она не найдёт знакомого заголовка и заведёт вторую секцию.
        """
        self.deploy_script(fresh=False)
        path = self.task()

        actions = rename_notes_section(self.tasks, self.cfg)

        self.assertEqual(actions, [])
        self.assertIn(LEGACY_NOTES_SECTION, path.read_text(encoding="utf-8"))

    def test_no_script_no_migration(self) -> None:
        """Скрипта в проекте нет — трогать файлы задач тем более незачем."""
        path = self.task()

        self.assertEqual(rename_notes_section(self.tasks, self.cfg), [])
        self.assertIn(LEGACY_NOTES_SECTION, path.read_text(encoding="utf-8"))

    def test_both_headings_left_to_human(self) -> None:
        """Обе секции в файле — это слияние содержимого, а не переименование."""
        self.deploy_script()
        path = self.task(body=LEGACY_TASK.replace(
            "## История коммитов", "## Комментарии\n\n## История коммитов"))

        rename_notes_section(self.tasks, self.cfg)

        text = path.read_text(encoding="utf-8")
        self.assertIn(LEGACY_NOTES_SECTION, text, "чужие секции слиты молча")


class BoardWriteTest(ProjectCase):
    """Запись с доски (подтверждение требования) не плодит вторую секцию."""

    def test_writes_into_legacy_section_until_migration(self) -> None:
        path = self.task()

        note = append_note(path, "проверку подтвердил человек")

        text = path.read_text(encoding="utf-8")
        self.assertIsNotNone(note)
        self.assertNotIn(NOTES_SECTION, text, "рядом со старой заведена вторая секция")
        self.assertIn("проверку подтвердил человек", text.split(LEGACY_NOTES_SECTION, 1)[-1])

    def test_creates_section_with_new_name(self) -> None:
        """Секции нет вовсе — заводим уже под нынешним именем."""
        path = self.task(body=LEGACY_TASK.replace("## Заметки агента\n\n", "")
                                         .replace("- **2026-08-01 10:00**"
                                                  " · Claude Opus 5 · первая строка\n\n", ""))

        append_note(path, "проверку подтвердил человек")

        text = path.read_text(encoding="utf-8")
        self.assertIn(NOTES_SECTION, text)
        self.assertNotIn(LEGACY_NOTES_SECTION, text)


class ScriptFallbackTest(ProjectCase):
    """Скрипт проекта: отставший заголовок чинится на месте, а не дублируется."""

    def setUp(self) -> None:
        super().setUp()
        self.mod = load_script()

    def test_add_note_renames_legacy_heading(self) -> None:
        path = self.task()

        result = self.mod.add_note(self.tasks, "TASK-001", "суть", agent="Claude Opus 5")

        self.assertTrue(result.get("ok"), result.get("error"))
        text = path.read_text(encoding="utf-8")
        self.assertIn(NOTES_SECTION, text, "заголовок не приведён к нынешнему имени")
        self.assertNotIn(LEGACY_NOTES_SECTION, text)
        self.assertEqual(text.count("## Комментарии"), 1, "секция задвоилась")

    def test_check_is_silent_on_legacy_heading(self) -> None:
        """Неприехавшая миграция — не работа агента, ругаться на неё незачем."""
        path = self.task()

        warnings = self.mod.check_task_file(path)

        self.assertEqual([w for w in warnings if "Комментарии" in w], [],
                         f"проверка ругается на прежнее имя секции: {warnings}")


class CallSitesTest(unittest.TestCase):
    """Миграция бесполезна, если её никто не зовёт."""

    APP = Path(__file__).resolve().parent.parent / "backend" / "app.py"

    def test_runs_on_startup(self) -> None:
        text = self.APP.read_text(encoding="utf-8")
        startup = text.split("def _startup()", 1)[-1].split("\n@app.", 1)[0]

        self.assertIn("rename_notes_section", startup,
                      "миграция не вызывается при старте сервера")

    def test_runs_after_scaffold(self) -> None:
        """Скрипт обновляют кнопкой — файлы должны поехать сразу за ним."""
        text = self.APP.read_text(encoding="utf-8")
        endpoint = text.split("def api_scaffold(", 1)[-1].split("\n@app.", 1)[0]

        self.assertIn("rename_notes_section", endpoint,
                      "после развёртывания окружения миграция не запускается")


if __name__ == "__main__":
    unittest.main()
