"""Тесты прогресса чеклиста на превью (TASK-177).

Чеклист — план под конкретную работу, и на доске по нему видно, где работа
идёт, а где задача только открыта. Считает **бэкенд**, тем же проходом по
файлам задач, что тип и размер: доска перерисовывается на любую правку в
`tasks/`, и лишний обход платится за каждое действие агента.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import CARD_FLAGS, DEFAULTS, card_style  # noqa: E402
from backend.task_parser import annotate_marks  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"

TASK = """---
id: {task_id}
title: Тест
status: development
---

## Описание

Текст.
{checklist}
## Комментарии

- **2026-08-13 02:00** · Модель · строка
"""


class ProgressCountTest(unittest.TestCase):
    """Сколько пунктов закрыто — считается по секции «Чеклист»."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)

    def _task(self, checklist: str, task_id: str = "TASK-001") -> None:
        (self.tasks_dir / f"{task_id}-test.md").write_text(
            TASK.format(task_id=task_id, checklist=checklist), encoding="utf-8")

    def _progress(self, cfg: dict | None = None) -> dict | None:
        board = {"columns": [{"status": "development", "groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-test.md"}]}]}]}
        annotate_marks(self.tasks_dir, board, cfg if cfg is not None else DEFAULTS)
        return board["columns"][0]["groups"][0]["tasks"][0].get("progress")

    def test_counts_done_and_total(self) -> None:
        self._task("""
## Чеклист

- [x] сделано
- [x] тоже сделано
- [ ] ещё нет
""")
        self.assertEqual({"done": 2, "total": 3}, self._progress())

    def test_no_section_no_progress(self) -> None:
        """План не вели — прогрессу взяться неоткуда, и `0/0` рисовать нельзя."""
        self._task("")
        self.assertIsNone(self._progress())

    def test_empty_section_is_silent(self) -> None:
        """Заголовок есть, пунктов нет — то же самое, что плана нет."""
        self._task("""
## Чеклист

Заведу пункты позже.
""")
        self.assertIsNone(self._progress())

    def test_checked_uppercase_counts(self) -> None:
        """Файл правят руками: `- [X]` — та же закрытая галочка."""
        self._task("""
## Чеклист

- [X] сделано
- [ ] нет
""")
        self.assertEqual({"done": 1, "total": 2}, self._progress())

    def test_checkboxes_of_other_sections_ignored(self) -> None:
        """Считается план работы, а не любые галочки в файле."""
        self._task("""
## Чеклист

- [x] сделано

## История доработок

- [ ] замечание ревью
""")
        self.assertEqual({"done": 1, "total": 1}, self._progress())

    def test_setting_off_removes_the_field(self) -> None:
        """Выключенный показ — поля нет вовсе: фронт не решает, что показывать."""
        self._task("""
## Чеклист

- [x] сделано
- [ ] нет
""")
        cfg = dict(DEFAULTS, card_show_progress=False)
        self.assertIsNone(self._progress(cfg))

    def test_nothing_done_is_silent(self) -> None:
        """Ни одного закрытого пункта — работа не начата, и молчание это и значит.

        Полоска отвечает на вопрос «где работа идёт»; пустая она говорила бы
        ровно то же, что её отсутствие, но занимала бы строку на каждой
        карточке с планом.
        """
        self._task("""
## Чеклист

- [ ] первое
- [ ] второе
""")
        self.assertIsNone(self._progress())


class ProgressEndOfRouteTest(unittest.TestCase):
    """В конце маршрута работа кончилась — прогресс там шум, как и возраст."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)
        (self.tasks_dir / "TASK-001-test.md").write_text(
            TASK.format(task_id="TASK-001", checklist="""
## Чеклист

- [x] сделано
- [x] и это
"""), encoding="utf-8")

    def _progress(self, status: str) -> dict | None:
        from backend.statuses import load_pipeline

        board = {"columns": [{"status": status, "groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-test.md"}]}]}]}
        pipeline = load_pipeline({"pipeline": ["backlog", "development", "testing",
                                               "completed", "cancelled"]})
        annotate_marks(self.tasks_dir, board, DEFAULTS, pipeline)
        return board["columns"][0]["groups"][0]["tasks"][0].get("progress")

    def test_working_status_shows_progress(self) -> None:
        self.assertEqual({"done": 2, "total": 2}, self._progress("development"))

    def test_terminal_status_is_silent(self) -> None:
        self.assertIsNone(self._progress("completed"))

    def test_offramp_is_silent(self) -> None:
        self.assertIsNone(self._progress("cancelled"))

    def test_without_pipeline_nothing_breaks(self) -> None:
        """Пайплайн не передали — считаем как раньше, а не молчим обо всём."""
        board = {"columns": [{"status": "development", "groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-test.md"}]}]}]}
        annotate_marks(self.tasks_dir, board, DEFAULTS)
        self.assertEqual({"done": 2, "total": 2},
                         board["columns"][0]["groups"][0]["tasks"][0]["progress"])


class ProgressSettingTest(unittest.TestCase):
    """Показ прогресса — переключатель вида превью, как метка типа."""

    def test_default_is_on(self) -> None:
        self.assertIs(DEFAULTS.get("card_show_progress"), True)
        self.assertIs(card_style({})["card_show_progress"], True)
        self.assertIs(card_style({"card_show_progress": False})["card_show_progress"],
                      False)

    def test_flag_is_saved_through_api(self) -> None:
        self.assertIn("card_show_progress", CARD_FLAGS)

    def test_setting_is_global(self) -> None:
        """Теснота превью — свойство глаз и монитора, а не репозитория."""
        from backend.config import PROJECT_KEYS

        self.assertNotIn("card_show_progress", PROJECT_KEYS)

    def test_settings_form_has_the_switch(self) -> None:
        text = (FRONTEND / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")
        self.assertIn("card_show_progress", text)


class ProgressOnCardTest(unittest.TestCase):
    """Полоска живёт в нижней строке превью: слева возраст, справа эпик."""

    def setUp(self) -> None:
        self.src = (FRONTEND / "components" / "TaskCard.jsx").read_text(encoding="utf-8")

    def test_bottom_row_shows_progress(self) -> None:
        self.assertIn("task.progress", self.src)

    def test_row_appears_for_progress_alone(self) -> None:
        """У задачи без возраста и эпика строка нужна ради одной полоски."""
        row = self.src[self.src.index("task.stale_days || task.epic"):]
        self.assertIn("progress", row[:200],
                      "нижняя строка не рисуется, когда есть только прогресс")

    def test_progress_is_centered_between_neighbours(self) -> None:
        """Возраст и эпик по краям, полоска — по центру, независимо от соседей."""
        self.assertIn("grid-cols-[1fr_auto_1fr]", self.src)

    def test_progress_bar_is_always_solid(self) -> None:
        """Вид полоски один на любой план — деления у коротких раздражали.

        Прежде до восьми пунктов рисовались деления, дальше сплошная заливка:
        соседние карточки выглядели по-разному без видимой причины, и разница
        читалась как разница смысла (TASK-180).
        """
        self.assertNotIn("PROGRESS_SEGMENTS_MAX", self.src,
                         "остался порог перехода на деления")
        self.assertIn("done / total", self.src,
                      "полоска не заливается долей закрытых пунктов")

    def test_full_progress_turns_green(self) -> None:
        """Закрытый план виден цветом: «всё сделано» — отдельное состояние.

        Считать деления, чтобы понять, остался ли последний пункт, — работа,
        которую метка и должна снимать.
        """
        self.assertIn("emerald", self.src,
                      "полностью закрытый план не отличается цветом")


if __name__ == "__main__":
    unittest.main()
