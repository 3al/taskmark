"""Выгрузка заметок релиза одной командой: `set_status.py --changelog` (TASK-094).

Скилл выпуска собирает changelog из секций «Изменение для пользователя» в файлах
задач. Читать каждый файл и вырезать секцию самому — механическая работа, ровно
та, что живёт в скрипте; заодно исчезает класс ошибок разбора вручную.

Ключевое здесь — **три состояния**, а не два: текст, пустая секция (решение
«сказать нечего») и отсутствующая секция (до задачи не добирались). Слипнутся
два последних — и по ответу нельзя понять, готов ли выпуск.

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

from tests.test_set_status_script import (SCRIPT, load_script,  # noqa: E402
                                          render_board)

# Релизного хвоста в дефолтном пайплайне нет — он приходит пресетом «С релизами».
# Режим читает состав по действию release_lock, поэтому его и объявляем
RELEASE_PIPELINE = ["backlog", "todo", "development", "testing", "ready_for_release",
                    "release_notes", "to_release", "done", "cancelled"]


def _use_release_pipeline(tasks: Path) -> None:
    cfg = {"pipeline": RELEASE_PIPELINE,
           "actions": {"create": "backlog", "pick": "todo", "start": "development",
                       "return": "development", "release_draft": "release_notes",
                       "release_lock": "to_release"}}
    (tasks / "board.md").write_text(render_board(cfg), encoding="utf-8")
    (tasks / ".taskboard.json").write_text(json.dumps(cfg), encoding="utf-8")


TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-08-01
---

## Описание

Тестовая задача.

## Комментарии

## История коммитов
"""


class ChangelogSliceTest(unittest.TestCase):
    """Срез задач статуса вместе с их текстами для changelog."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        _use_release_pipeline(self.tasks)
        self.mod = load_script()

    def _add(self, task_id: str, title: str = "Задача", status: str = "to_release",
             section: str = "## To Release", release_notes: str | None = None) -> Path:
        """Задача в разделе доски; release_notes=None — секции нет вовсе."""
        filename = f"{task_id}-test.md"
        path = self.tasks / filename
        text = TASK_FILE.format(task_id=task_id, title=title, status=status)
        if release_notes is not None:
            text = text.replace(
                "## Комментарии",
                f"## Изменение для пользователя\n\n{release_notes}\n\n## Комментарии")
        path.write_text(text, encoding="utf-8")

        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        idx = lines.index(section)
        entry = f"- {task_id} · [{title}]({filename}) · Тест · 2026-08-01"
        if idx + 2 < len(lines) and lines[idx + 2].strip() == "_(нет)_":
            lines[idx + 2] = entry
        else:
            lines.insert(idx + 2, entry)
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _slice(self, status: str = "") -> dict:
        return self.mod.changelog(self.tasks, status)

    # --- Состав среза ---

    def test_tasks_of_the_status_with_their_texts(self) -> None:
        self._add("TASK-001", "Первая", release_notes="Появилась команда истории.")
        report = self._slice("to_release")

        self.assertEqual(report["status"], "to_release")
        self.assertEqual(report["section"], "To Release")
        self.assertEqual([t["id"] for t in report["tasks"]], ["TASK-001"])
        self.assertEqual(report["tasks"][0]["title"], "Первая")
        self.assertEqual(report["tasks"][0]["notes"], "Появилась команда истории.")

    def test_default_status_is_the_approved_composition(self) -> None:
        """Без аргумента берётся цель release_lock — утверждённый состав."""
        self._add("TASK-001", release_notes="Текст.")
        self.assertEqual(self._slice()["status"], "to_release")

    def test_order_follows_the_board(self) -> None:
        """Порядок — как на доске: состав выпуска читают сверху вниз."""
        self._add("TASK-001", "Первая", release_notes="Раз.")
        self._add("TASK-002", "Вторая", release_notes="Два.")
        # вставка идёт в начало раздела, поэтому сверху окажется вторая
        self.assertEqual([t["id"] for t in self._slice()["tasks"]],
                         ["TASK-002", "TASK-001"])

    # --- Три состояния секции ---

    def test_empty_section_is_a_decision(self) -> None:
        """Секция есть, но пустая — «сказать нечего», в changelog не поедет."""
        self._add("TASK-001", release_notes="")
        self.assertEqual(self._slice()["tasks"][0]["notes"], "")

    def test_missing_section_is_not_the_same(self) -> None:
        """Секции нет — до задачи не добирались, ей нужен черновик."""
        self._add("TASK-001", release_notes=None)
        self.assertIsNone(self._slice()["tasks"][0]["notes"])

    def test_whitespace_only_section_counts_as_empty(self) -> None:
        self._add("TASK-001", release_notes="   \n\n")
        self.assertEqual(self._slice()["tasks"][0]["notes"], "")

    # --- Разбор секции ---

    def test_heading_in_the_text_is_not_a_section(self) -> None:
        """Заголовок, упомянутый в описании, за секцию не принимается."""
        path = self._add("TASK-001", release_notes=None)
        text = path.read_text(encoding="utf-8").replace(
            "Тестовая задача.",
            "Скилл создаёт секцию «## Изменение для пользователя» при переносе.")
        path.write_text(text, encoding="utf-8")

        self.assertIsNone(self._slice()["tasks"][0]["notes"],
                          "упоминание заголовка принято за саму секцию")

    def test_heading_inside_a_code_block_is_not_a_section(self) -> None:
        """Заголовок внутри блока кода — часть примера, а не структура файла.

        Описания задач сплошь и рядом показывают куски board.md и файлов задач,
        где строки начинаются с `##`.
        """
        path = self._add("TASK-001", release_notes="Настоящий текст.")
        text = path.read_text(encoding="utf-8").replace(
            "Тестовая задача.",
            "Пример файла:\n\n```\n## Изменение для пользователя\n\nчужой текст\n```")
        path.write_text(text, encoding="utf-8")

        self.assertEqual(self._slice()["tasks"][0]["notes"], "Настоящий текст.",
                         "секция прочиталась из блока кода")

    def test_section_body_stops_at_the_next_section(self) -> None:
        self._add("TASK-001", release_notes="Только это.")
        notes = self._slice()["tasks"][0]["notes"]

        self.assertNotIn("Комментарии", notes, "зацепили соседнюю секцию")
        self.assertNotIn("История коммитов", notes)

    # --- Границы ---

    def test_unknown_status_is_refused(self) -> None:
        report = self._slice("такого-нет")
        self.assertTrue(report.get("error"), "неизвестный статус принят молча")
        self.assertEqual(report["tasks"], [])

    def test_empty_section_of_the_board_is_not_an_error(self) -> None:
        """В статусе никого — пустой список, а не отказ."""
        report = self._slice()
        self.assertEqual(report["tasks"], [])
        self.assertFalse(report.get("error"))

    def test_mode_only_reads(self) -> None:
        """Режим ничего не правит: ни доску, ни файлы задач."""
        path = self._add("TASK-001", release_notes="Текст.")
        board = self.tasks / "board.md"
        before = (board.read_text(encoding="utf-8"), path.read_text(encoding="utf-8"))
        self._slice()

        self.assertEqual(before,
                         (board.read_text(encoding="utf-8"),
                          path.read_text(encoding="utf-8")))


class ChangelogCliTest(unittest.TestCase):
    """Ответ приходит json'ом и разбирается без доработки на стороне скилла."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        _use_release_pipeline(self.tasks)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8")

    def test_cli_prints_json(self) -> None:
        result = self._run("--changelog", "to_release")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "to_release")
        self.assertIsInstance(data["tasks"], list)

    def test_cli_without_status(self) -> None:
        result = self._run("--changelog")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "to_release")

    def test_cli_refuses_unknown_status(self) -> None:
        result = self._run("--changelog", "такого-нет")
        self.assertNotEqual(result.returncode, 0, "неизвестный статус принят молча")


if __name__ == "__main__":
    unittest.main()
