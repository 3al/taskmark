"""Удаление задачи с доски крестиком (TASK-043).

Убрать задачу можно было только руками — удалить файл и вычистить строку из
board.md, то есть в двух местах и с риском рассинхронизации. Крестик делает
это за раз, но возможность **выключена по умолчанию**: доска работает с
файлами пользователя, и кнопка необратимого удаления не должна оказаться под
рукой у того, кто её не просил.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS, PROJECT_KEYS  # noqa: E402
from backend.stall import stall_of  # noqa: E402
from backend.task_parser import find_task_file, parse_frontmatter  # noqa: E402
from backend.tasks_delete import delete_plan, delete_task  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
CARD = SRC / "components" / "TaskCard.jsx"
APP_PY = Path(__file__).resolve().parent.parent / "backend" / "app.py"

BOARD = """# Tasks Board

## Backlog

### Баги

- TASK-001 · [Первая](TASK-001-первая.md)
- TASK-002 · [Вторая](TASK-002-вторая.md)

## Development

- TASK-003 · [Третья](TASK-003-третья.md) · Claude Opus 5 · 2026-08-02
"""


def task_file(task_id: str, title: str, status: str, **fields: str) -> str:
    extra = "".join(f"{k}: {v}\n" for k, v in fields.items())
    return (f"---\nid: {task_id}\ntitle: {title}\nepic: ~\ntype: bug\n"
            f"status: {status}\ncreated: 2026-08-02 10:00\n{extra}---\n\n"
            "## Описание\n\nТекст.\n")


class DeleteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name)
        (self.tasks / "board.md").write_text(BOARD, encoding="utf-8")
        (self.tasks / "TASK-001-первая.md").write_text(
            task_file("TASK-001", "Первая", "backlog"), encoding="utf-8")
        (self.tasks / "TASK-002-вторая.md").write_text(
            task_file("TASK-002", "Вторая", "backlog"), encoding="utf-8")
        (self.tasks / "TASK-003-третья.md").write_text(
            task_file("TASK-003", "Третья", "development"), encoding="utf-8")
        self.cfg = {**DEFAULTS, "delete_tasks": True}

    def board(self) -> str:
        return (self.tasks / "board.md").read_text(encoding="utf-8")

    def meta(self, task_id: str) -> dict:
        path = find_task_file(self.tasks, task_id)
        assert path is not None
        return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


class DeleteTaskTest(DeleteCase):
    """Оба конца за одно действие: файла нет, строки на доске нет."""

    def test_file_and_entry_gone(self) -> None:
        result = delete_task(self.tasks, self.cfg, "TASK-001")
        self.assertTrue(result["ok"], result)
        self.assertIsNone(find_task_file(self.tasks, "TASK-001"), "файл задачи остался")
        self.assertNotIn("TASK-001", self.board(), "строка осталась на доске")

    def test_neighbours_untouched(self) -> None:
        delete_task(self.tasks, self.cfg, "TASK-001")
        self.assertIn("TASK-002", self.board())
        self.assertIsNotNone(find_task_file(self.tasks, "TASK-002"))

    def test_unknown_task_refused(self) -> None:
        result = delete_task(self.tasks, self.cfg, "TASK-404")
        self.assertFalse(result["ok"])

    def test_refused_when_disabled(self) -> None:
        """Галочка снята — удаления нет вовсе, даже мимо UI."""
        result = delete_task(self.tasks, {**DEFAULTS, "delete_tasks": False}, "TASK-001")
        self.assertFalse(result["ok"], "удаление сработало при снятой галочке")
        self.assertIsNotNone(find_task_file(self.tasks, "TASK-001"))

    def test_default_is_off(self) -> None:
        self.assertIs(DEFAULTS.get("delete_tasks"), False,
                      "необратимое удаление включено по умолчанию")

    def test_setting_belongs_to_project(self) -> None:
        self.assertIn("delete_tasks", PROJECT_KEYS)

    def test_every_project_key_is_saveable(self) -> None:
        """Настройка, которую нельзя сохранить, — это галочка-обманка.

        `delete_tasks` ровно так и не работала: в форме ставилась, до конфига
        не доезжала, потому что список разрешённых ключей API перечислялся
        руками и разошёлся с реестром проектных ключей.
        """
        text = APP_PY.read_text(encoding="utf-8")
        allowed = text[text.index("allowed = {"):text.index("updates = {k: v")]
        self.assertIn("PROJECT_KEYS", allowed,
                      "разрешённые ключи перечислены руками — реестр разойдётся снова")


class DeleteWithLinksTest(DeleteCase):
    """Ссылки на удалённую задачу не должны оставаться блокерами-призраками."""

    def setUp(self) -> None:
        super().setUp()
        # TASK-002 ждёт TASK-001; у TASK-001 обратная ссылка blocks
        (self.tasks / "TASK-002-вторая.md").write_text(
            task_file("TASK-002", "Вторая", "backlog", blocked_by="TASK-001"),
            encoding="utf-8")
        (self.tasks / "TASK-001-первая.md").write_text(
            task_file("TASK-001", "Первая", "backlog", blocks="TASK-002"),
            encoding="utf-8")

    def test_plan_names_dependants(self) -> None:
        """Диалог должен сказать, кого это заденет, до удаления."""
        plan = delete_plan(self.tasks, "TASK-001")
        self.assertEqual(plan["blocks"], ["TASK-002"])
        self.assertEqual(plan["title"], "Первая")
        self.assertEqual(plan["status"], "backlog")

    def test_backlinks_cleared(self) -> None:
        result = delete_task(self.tasks, self.cfg, "TASK-001")
        self.assertEqual(result["unblocked"], ["TASK-002"])
        self.assertEqual(stall_of(self.meta("TASK-002"))["blocked_by"], [],
                         "у соседа остался блокер-призрак")


class DeleteApiTest(unittest.TestCase):
    """Эндпоинт и кнопка: удаление живёт за галочкой и подтверждением."""

    def test_endpoint_exists(self) -> None:
        text = APP_PY.read_text(encoding="utf-8")
        self.assertIn('@app.delete("/api/tasks/{task_id}")', text,
                      "нет эндпоинта удаления задачи")
        self.assertIn("delete_plan", text, "план удаления не отдаётся UI")

    def test_card_asks_before_deleting(self) -> None:
        text = CARD.read_text(encoding="utf-8")
        self.assertIn("onDelete", text, "на карточке нет крестика")
        self.assertIn("confirm", text.lower(), "крестик удаляет без подтверждения")

    def test_cross_lives_in_the_very_corner(self) -> None:
        """Крестик — в самом углу и вне потока значков.

        В ряду он спорил с меткой типа и паузой: те рассказывают о задаче, а он
        делает с ней необратимое. Вне потока он ещё и не резервирует место —
        композиция строки не зависит от того, включено удаление или нет.
        """
        text = CARD.read_text(encoding="utf-8")
        cross = text[text.index("{onDelete && ("):text.index("Удалить задачу")]
        self.assertIn("absolute top-0 right-0", cross, "крестик не в углу карточки")
        self.assertIn("opacity-0", cross, "крестик виден постоянно и засоряет карточку")
        row = text[text.index("(type || task.paused)"):text.index("{confirming && (")]
        self.assertNotIn("onDelete", row, "крестик снова встал в ряд значков")


if __name__ == "__main__":
    unittest.main()
