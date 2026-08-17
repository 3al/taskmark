"""Заглушка `_(нет)_` в опустевшем разделе доски (TASK-116).

Раздел, из которого ушла последняя задача, должен остаться с заглушкой: пустой
раздел без неё читается как «сломалось», а не как «пусто». Скрипт смены статуса
это делал, а перенос мышью — нет, и после релизного прогона раздел оставался
с двумя пустыми строками между заголовками.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.queue_ops import move_task  # noqa: E402
from tests.test_set_status_script import render_board  # noqa: E402

PLACEHOLDER = "_(нет)_"

TASK = """---
id: {task_id}
title: {task_id}
epic: ~
status: {status}
created: 2026-08-02 19:37
---

## Описание

Тестовая задача.
"""


class BoardPlaceholderTest(unittest.TestCase):
    """Проект во временной папке: доска из шаблона, задачи рядом."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.board = self.tasks / "board.md"
        self.board.write_text(render_board(), encoding="utf-8")
        self.cfg = {"board_file": "board.md"}

    def fill(self, section: str, status: str, count: int, first: int = 1) -> list[str]:
        """Положить в раздел (## или ###) count задач, заменив заглушку."""
        heading = section if section.startswith("#") else f"## {section}"
        ids = [f"TASK-{i:03d}" for i in range(first, first + count)]
        entries = []
        for task_id in ids:
            (self.tasks / f"{task_id}-test.md").write_text(
                TASK.format(task_id=task_id, status=status), encoding="utf-8")
            entries.append(f"- {task_id} · [{task_id}]({task_id}-test.md) · k3 · 2026-08-01")
        lines = self.board.read_text(encoding="utf-8").splitlines()
        at = lines.index(heading)
        if lines[at + 2].strip() == PLACEHOLDER:
            lines[at + 2:at + 3] = entries
        else:
            lines[at + 2:at + 2] = entries
        self.board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ids

    def body(self, section: str) -> str:
        """Тело раздела (## или ###) до следующего заголовка того же уровня."""
        heading = section if section.startswith("#") else f"## {section}"
        level = heading.split(" ", 1)[0]
        text = self.board.read_text(encoding="utf-8")
        tail = text.split(f"{heading}\n", 1)[1]
        for stop in (f"\n{level} ", "\n## " if level == "###" else f"\n{level} "):
            tail = tail.split(stop, 1)[0]
        return tail

    # --- Перенос мышью ---

    def test_single_move_leaves_placeholder(self) -> None:
        self.fill("Development", "development", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Testing")
        self.assertIn(PLACEHOLDER, self.body("Development"),
                      "опустевший раздел остался без заглушки")

    def test_series_of_moves_leaves_placeholder(self) -> None:
        """Серия переносов подряд: заглушка возвращается на последнем."""
        ids = self.fill("Development", "development", 4)
        for task_id in ids:
            move_task(self.tasks, self.cfg, task_id, "Testing")
        dev = self.body("Development")
        self.assertIn(PLACEHOLDER, dev, "опустевший раздел остался без заглушки")
        self.assertEqual(dev.count(PLACEHOLDER), 1, f"заглушек больше одной: {dev!r}")

    def test_placeholder_not_added_while_tasks_remain(self) -> None:
        ids = self.fill("Development", "development", 2)
        move_task(self.tasks, self.cfg, ids[0], "Testing")
        self.assertNotIn(PLACEHOLDER, self.body("Development"),
                         "заглушка встала в раздел, где ещё есть задача")

    def test_target_section_loses_placeholder(self) -> None:
        self.fill("Development", "development", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Testing")
        self.assertNotIn(PLACEHOLDER, self.body("Testing"),
                         "заглушка осталась рядом с приехавшей задачей")

    def test_move_back_and_forth(self) -> None:
        """Туда-обратно: заглушка каждый раз остаётся в опустевшем разделе."""
        self.fill("Development", "development", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Testing")
        move_task(self.tasks, self.cfg, "TASK-001", "Development")
        self.assertIn(PLACEHOLDER, self.body("Testing"))
        self.assertNotIn(PLACEHOLDER, self.body("Development"))

    # --- Подразделы ### раздела приёма задач ---

    def test_emptied_subsection_gets_placeholder(self) -> None:
        """Задача ушла из подраздела — заглушку получает он, а не раздел."""
        self.fill("### Рефакторинг", "backlog", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Development")
        self.assertIn(PLACEHOLDER, self.body("### Рефакторинг"),
                      "опустевший подраздел остался без заглушки")

    def test_subsection_placeholder_does_not_leak_to_section(self) -> None:
        """У раздела с подразделами своей заглушки уровня ## не появляется."""
        self.fill("### Рефакторинг", "backlog", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Development")
        backlog = self.body("Backlog")
        head = backlog.split("### ", 1)[0]
        self.assertNotIn(PLACEHOLDER, head,
                         "заглушка встала под ## Backlog поверх подразделов")

    def test_neighbour_subsections_keep_their_placeholders(self) -> None:
        """Соседние пустые подразделы своих заглушек не теряют и не двоят."""
        self.fill("### Рефакторинг", "backlog", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Development")
        for name in ("### Рефакторинг", "### Баги", "### Уборка"):
            self.assertEqual(self.body(name).count(PLACEHOLDER), 1,
                             f"{name}: заглушек не одна — {self.body(name)!r}")

    def test_placeholder_not_added_while_subsection_has_tasks(self) -> None:
        ids = self.fill("### Рефакторинг", "backlog", 2)
        move_task(self.tasks, self.cfg, ids[0], "Development")
        self.assertNotIn(PLACEHOLDER, self.body("### Рефакторинг"),
                         "заглушка встала в подраздел, где ещё есть задача")

    def test_move_into_subsection_drops_its_own_placeholder(self) -> None:
        """Заглушка убирается из целевого подраздела, а не из первого попавшегося."""
        self.fill("Development", "development", 1)
        move_task(self.tasks, self.cfg, "TASK-001", "Backlog", group="Баги")
        self.assertNotIn(PLACEHOLDER, self.body("### Баги"),
                         "заглушка осталась рядом с приехавшей задачей")
        self.assertIn(PLACEHOLDER, self.body("### Новый функционал"),
                      "уехала заглушка чужого подраздела")

    def test_missing_target_section_created_on_the_fly(self) -> None:
        """Раздел создаётся на лету: строка задачи ниже него не должна теряться.

        Доска перечитывается заново, и вставленный блок сдвигает индексы —
        по старому индексу с доски уехала бы соседняя строка.
        """
        self.fill("Testing", "testing", 2)
        lines = self.board.read_text(encoding="utf-8").splitlines()
        at = lines.index("## Development")
        end = next(i for i in range(at + 1, len(lines)) if lines[i].startswith("## "))
        del lines[at:end]
        self.board.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = move_task(self.tasks, self.cfg, "TASK-001", "Development")
        self.assertTrue(result["ok"], result)
        board = self.board.read_text(encoding="utf-8")
        self.assertEqual(board.count("- TASK-001 "), 1, board)
        self.assertEqual(board.count("- TASK-002 "), 1, board)
        self.assertIn("- TASK-001 ", self.body("Development"))
        self.assertIn("- TASK-002 ", self.body("Testing"))

    def test_same_section_move_keeps_entry(self) -> None:
        """Перестановка внутри раздела заглушку не добавляет."""
        ids = self.fill("Development", "development", 2)
        move_task(self.tasks, self.cfg, ids[0], "Development", position=1)
        dev = self.body("Development")
        self.assertNotIn(PLACEHOLDER, dev)
        self.assertIn("- TASK-001 ", dev)


if __name__ == "__main__":
    unittest.main()
