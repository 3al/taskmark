"""Недостающие разделы доски: жалоба с кнопкой, а не тупик (TASK-129).

Пайплайн проекта и разделы `board.md` — два конца одной связки: статус включили
в настройках, а раздела под него на доске нет (доску скопировали из другого
проекта, маршрут поменяли позже). Колонки для такого статуса не будет, и задача
в нём на доске не покажется.

Валидатор про это говорил мягким предупреждением — строкой без кнопки, из
которой человеку некуда пойти. Разделы создаются тем же `ensure_section`, что и
раздел очереди, поэтому расхождение чинится одной кнопкой и относится к
деградациям, а не к предупреждениям.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.queue_ops import ensure_pipeline_sections  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from backend.validator import validate_project  # noqa: E402
from tests.test_set_status_script import render_board  # noqa: E402


def drop_section(board: Path, title: str) -> None:
    """Убрать раздел `## title` целиком — так выглядит доска чужого проекта."""
    lines = board.read_text(encoding="utf-8").splitlines(keepends=True)
    out, skipping = [], False
    for line in lines:
        if line.startswith("## "):
            skipping = line[3:].strip().lower() == title.lower()
        if not skipping:
            out.append(line)
    board.write_text("".join(out), encoding="utf-8")


class MissingSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.board = self.tasks / "board.md"
        self.board.write_text(render_board(), encoding="utf-8")
        self.cfg: dict = {}

    def issue(self) -> dict | None:
        report = validate_project(self.tasks, self.cfg)
        for d in report["degraded"]:
            if d["code"] == "no_board_sections":
                return d
        return None

    def test_full_board_is_silent(self) -> None:
        self.assertIsNone(self.issue(), "разделы на месте, а жалоба есть")

    def test_missing_sections_are_a_fixable_degradation(self) -> None:
        """Пропавший раздел — деградация с кодом: у неё есть кнопка."""
        drop_section(self.board, "Testing")
        drop_section(self.board, "Completed")

        found = self.issue()
        self.assertIsNotNone(found, "пропавшие разделы остались незамеченными")
        self.assertEqual(found["names"], ["Testing", "Completed"],
                         "кнопке нужно знать, что именно создавать")
        for title in ("Testing", "Completed"):
            self.assertIn(title, found["message"],
                          "человеку надо видеть, каких разделов не хватает")

    def test_missing_sections_are_not_repeated_as_a_warning(self) -> None:
        """Одна проблема — одна строка: дубль в предупреждениях сбивает с толку."""
        drop_section(self.board, "Testing")

        warnings = validate_project(self.tasks, self.cfg)["warnings"]
        self.assertFalse([w for w in warnings if "статусов пайплайна" in w],
                         "та же жалоба осталась второй строкой без кнопки")


class EnsureSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.board = Path(self._tmp.name) / "board.md"
        self.board.write_text(render_board(), encoding="utf-8")
        self.pipeline = load_pipeline({})

    def titles(self) -> list[str]:
        return [line[3:].strip() for line in
                self.board.read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")]

    def test_creates_every_missing_section(self) -> None:
        before = self.titles()
        drop_section(self.board, "Testing")
        drop_section(self.board, "Completed")

        created = ensure_pipeline_sections(self.board, self.pipeline)

        self.assertEqual(created, ["Testing", "Completed"], "созданы не те разделы")
        self.assertEqual(self.titles(), before,
                         "разделы вернулись не на свои места по маршруту")

    def test_nothing_to_do_is_not_an_error(self) -> None:
        self.assertEqual(ensure_pipeline_sections(self.board, self.pipeline), [])

    def test_created_section_gets_the_placeholder(self) -> None:
        """Пустой раздел без заглушки читается как «строки потерялись»."""
        drop_section(self.board, "Testing")
        ensure_pipeline_sections(self.board, self.pipeline)

        body = self.board.read_text(encoding="utf-8")
        after = body[body.index("## Testing"):]
        self.assertIn("_(нет)_", after[:after.index("## ", 3)])


class UiKnowsTheButtonTest(unittest.TestCase):
    """Код и его кнопка живут в разных файлах и расходятся молча."""

    def test_degradation_has_a_button(self) -> None:
        app = (Path(__file__).resolve().parent.parent
               / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("no_board_sections:", app,
                      "код деградации не заведён в DEGRADED_FIX — строка будет без кнопки")


if __name__ == "__main__":
    unittest.main()
