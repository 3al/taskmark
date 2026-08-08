"""История переводов статуса строкой в комментариях задачи (TASK-130).

От перевода статуса не оставалось следа: хвост строки доски перезаписывается
следующим переходом, а `status:` во frontmatter хранит только текущее значение.
Ответить, когда задача ушла из разработки и сколько раз возвращалась, было
нечем.

Теперь **каждый** перевод пишет строку в «Комментарии» — и через скрипт, и
мышью через доску. Формат один: `- **дата** · <источник> · Было → Стало`, где
источник стоит в позиции автора («доска», «скрипт (Модель)»). Он часть строки
наравне со статусами: по нему видно, прошёл переход через инструмент или мимо
него — а мимо инструмента как раз и теряются хвосты задачи.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.notes import TRANSITION_RE, transition_note  # noqa: E402
from backend.queue_ops import move_task  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from tests.test_set_status_script import load_script, render_board  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

TASK = """---
id: TASK-001
title: Тестовая
status: {status}
created: 2026-08-09 10:00
---

## Описание

Текст.

## Комментарии

## История коммитов
"""


class TaskCase(unittest.TestCase):
    """Проект во временной папке: доска и файл задачи рядом, как у пользователя."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "board.md").write_text(render_board(), encoding="utf-8")
        self.cfg = {"board_file": "board.md", "status_script": "set_status.py"}

    def task(self, status: str = "development", section: str = "Development") -> Path:
        path = self.tasks / "TASK-001-test.md"
        path.write_text(TASK.format(status=status), encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        at = lines.index(f"## {section}")
        lines.insert(at + 2, "- TASK-001 · [Тестовая](TASK-001-test.md)")
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def notes(self, path: Path) -> list[str]:
        body = path.read_text(encoding="utf-8").split("## Комментарии", 1)[-1]
        return [ln for ln in body.split("\n## ", 1)[0].splitlines() if ln.strip()]

    def transitions(self, path: Path) -> list[tuple[str, str, str]]:
        """Строки переводов: (источник, было, стало)."""
        out = []
        for line in self.notes(path):
            m = TRANSITION_RE.match(line)
            if m:
                out.append((m.group("author").strip(), m.group("from"), m.group("to")))
        return out


class FormatTest(unittest.TestCase):
    """Формат строки один у обоих путей — иначе историю не прочитать разбором."""

    def test_board_and_script_lines_match(self) -> None:
        board = transition_note("доска", "Development", "Testing")
        script = transition_note("скрипт (Claude Opus 5)", "Development", "Testing")

        for line in (board, script):
            m = TRANSITION_RE.match(line)
            self.assertIsNotNone(m, f"строка не опознаётся как перевод: {line}")
            self.assertEqual((m.group("from"), m.group("to")), ("Development", "Testing"))

    def test_plain_note_is_not_a_transition(self) -> None:
        """Обычный комментарий со стрелкой переводом не считается."""
        plain = ("- **2026-08-09 10:00** · Claude Opus 5 · "
                 "секция → «Комментарии», миграция под capability")

        self.assertIsNone(TRANSITION_RE.match(plain),
                          "комментарий агента принят за перевод статуса")


class BoardMoveTest(TaskCase):
    """Перенос мышью: источник — доска."""

    def test_move_writes_transition(self) -> None:
        path = self.task()

        move_task(self.tasks, self.cfg, "TASK-001", "Testing")

        self.assertEqual(self.transitions(path),
                         [("доска", "Development", "Testing")])

    def test_return_writes_two_lines(self) -> None:
        """Возврат — два факта: сам перевод и снятое подтверждение."""
        path = self.task(status="testing", section="Testing")
        path.write_text(path.read_text(encoding="utf-8").replace(
            "created: 2026-08-09 10:00", "created: 2026-08-09 10:00\nconfirmed: verified"),
            encoding="utf-8")
        cfg = {**self.cfg, "requires": {"testing": [
            {"id": "verified", "check": "confirm", "ask": "проверку подтвердил человек"}]}}

        move_task(self.tasks, cfg, "TASK-001", "Development")

        notes = self.notes(path)
        self.assertEqual(len(notes), 2, f"ожидались две строки: {notes}")
        self.assertEqual(self.transitions(path),
                         [("доска", "Testing", "Development")])
        self.assertIn("снято подтверждение", notes[1])
        self.assertLess(notes.index(notes[0]), 1, "перевод должен идти первым")

    def test_transition_written_without_requirements(self) -> None:
        """История переводов полна и там, где требований на этапе нет вовсе."""
        path = self.task(status="testing", section="Testing")

        move_task(self.tasks, self.cfg, "TASK-001", "Development")

        self.assertEqual(self.transitions(path),
                         [("доска", "Testing", "Development")])

    def test_repair_move_writes_nothing(self) -> None:
        """Починка доски двигает строку под файл — статуса это не меняет."""
        path = self.task()

        move_task(self.tasks, self.cfg, "TASK-001", "Testing", touch_status=False)

        self.assertEqual(self.transitions(path), [])


class ScriptTest(TaskCase):
    """Перевод скриптом: источник — скрипт, модель в скобках."""

    def setUp(self) -> None:
        super().setUp()
        self.mod = load_script()

    def test_status_change_writes_transition(self) -> None:
        path = self.task()

        result = self.mod.set_status(self.tasks, "TASK-001", "testing",
                                     agent="Claude Opus 5")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(self.transitions(path),
                         [("скрипт (Claude Opus 5)", "Development", "Testing")])

    def test_without_agent_source_stays(self) -> None:
        """Модель не передали — источник всё равно виден: он не про модель."""
        path = self.task()

        self.mod.set_status(self.tasks, "TASK-001", "testing")

        self.assertEqual(self.transitions(path), [("скрипт", "Development", "Testing")])

    def test_same_status_writes_nothing(self) -> None:
        """Повторный вызов с тем же статусом — не перевод, а идемпотентность."""
        path = self.task()

        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")

        self.assertEqual(self.transitions(path), [])

    def test_transition_precedes_note(self) -> None:
        """Комментарий того же вызова идёт после перевода: сначала факт, потом суть."""
        path = self.task()

        self.mod.set_status(self.tasks, "TASK-001", "testing", agent="Claude Opus 5")
        self.mod.add_note(self.tasks, "TASK-001", "готово к проверке", agent="Claude Opus 5")

        notes = self.notes(path)
        self.assertRegex(notes[0], r"Development → Testing")
        self.assertIn("готово к проверке", notes[-1])


class MirrorTest(unittest.TestCase):
    """Скрипт и бэкенд обязаны писать одинаково — расхождение ломает разбор."""

    def test_script_reuses_same_format(self) -> None:
        text = (TEMPLATES / "tasks" / "set_status.py").read_text(encoding="utf-8")
        pattern = re.search(r"TRANSITION_TEXT\s*=\s*(.+)", text)

        self.assertIsNotNone(pattern, "в скрипте нет общей формулы строки перевода")
        self.assertIn("→", pattern.group(1))


if __name__ == "__main__":
    unittest.main()
