"""Действия жизненного цикла оставляют след в «Комментариях».

TASK-140: файл задачи, прочитанный сверху вниз, должен давать полную картину
того, что с задачей происходило. Переводы статуса писались и раньше; здесь —
простой (блокировка, пауза), смена типа и переименование.

Оба пути записи проверяются вместе: доска (backend) и автономный
`tasks/set_status.py`. Формулировки у них общие — разъедутся тексты, разъедётся
и чтение истории.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.notes import (  # noqa: E402
    BLOCK_TEXT,
    BLOCKS_TEXT,
    PAUSE_TEXT,
    RESUME_TEXT,
    TITLE_TEXT,
    TRANSITION_RE,
    TYPE_TEXT,
    UNBLOCK_TEXT,
    UNBLOCKS_TEXT,
)
from backend.stall import block, clear_stall, set_paused, unblock  # noqa: E402
from backend.task_parser import set_task_title, set_task_type  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
type: feature
status: development
created: 2026-08-07 10:00
blocked_by: ~
---

## Описание

Тестовая задача.

## Комментарии

## История коммитов
"""


class LifecycleNotes(unittest.TestCase):
    """Общая песочница: две задачи в пустом проекте."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks = Path(self.tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")

    def make_task(self, task_id: str, title: str) -> Path:
        path = self.tasks / f"{task_id}-{title.lower()}.md"
        path.write_text(TASK_FILE.format(task_id=task_id, title=title), encoding="utf-8")
        return path

    def notes(self, task_id: str) -> list[str]:
        """Строки секции «Комментарии» файла задачи."""
        path = next(self.tasks.glob(f"{task_id}*.md"))
        lines = path.read_text(encoding="utf-8").splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Комментарии")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                   len(lines))
        return [ln for ln in lines[start + 1:end] if ln.strip()]

    def assertNote(self, task_id: str, text: str) -> str:
        """В хронологии задачи есть строка с такой сутью — вернуть её."""
        found = [ln for ln in self.notes(task_id) if ln.endswith(text)]
        self.assertTrue(found, f"{task_id}: нет строки «{text}» в {self.notes(task_id)}")
        return found[0]


class BoardLifecycleNotesTest(LifecycleNotes):
    """Путь доски: те же операции идут через backend."""

    def test_block_writes_both_ends(self) -> None:
        block(self.tasks, "TASK-014", "TASK-013")

        note = self.assertNote("TASK-014", BLOCK_TEXT.format(ids="TASK-013"))
        self.assertIn("· доска ·", note)
        self.assertNote("TASK-013", BLOCKS_TEXT.format(id="TASK-014"))

    def test_unblock_writes_both_ends(self) -> None:
        block(self.tasks, "TASK-014", "TASK-013")
        unblock(self.tasks, "TASK-014", "TASK-013")

        self.assertNote("TASK-014", UNBLOCK_TEXT.format(ids="TASK-013"))
        self.assertNote("TASK-013", UNBLOCKS_TEXT.format(id="TASK-014"))

    def test_repeated_block_writes_once(self) -> None:
        """Повторный вызов чинит односторонние ссылки, но событий не выдумывает."""
        block(self.tasks, "TASK-014", "TASK-013")
        block(self.tasks, "TASK-014", "TASK-013")

        text = BLOCK_TEXT.format(ids="TASK-013")
        self.assertEqual(1, len([ln for ln in self.notes("TASK-014") if ln.endswith(text)]))

    def test_pause_and_resume(self) -> None:
        set_paused(self.tasks, "TASK-013", "ждём ответ контрагента")
        self.assertNote("TASK-013", PAUSE_TEXT.format(reason="ждём ответ контрагента"))

        set_paused(self.tasks, "TASK-013", "")
        self.assertNote("TASK-013", RESUME_TEXT)

    def test_resume_without_pause_is_silent(self) -> None:
        set_paused(self.tasks, "TASK-013", "")
        self.assertEqual([], self.notes("TASK-013"))

    def test_clear_stall_writes_what_it_removed(self) -> None:
        """Переезд в конец маршрута снимает простой — и это тоже событие."""
        block(self.tasks, "TASK-014", "TASK-013")
        set_paused(self.tasks, "TASK-014", "ждём стенд")
        clear_stall(self.tasks, "TASK-014")

        self.assertNote("TASK-014", UNBLOCK_TEXT.format(ids="TASK-013"))
        self.assertNote("TASK-014", RESUME_TEXT)
        self.assertNote("TASK-013", UNBLOCKS_TEXT.format(id="TASK-014"))

    def test_type_change(self) -> None:
        set_task_type(self.tasks, "TASK-013", "bug")
        self.assertNote("TASK-013", TYPE_TEXT.format(now="bug", was="feature"))

    def test_same_type_is_silent(self) -> None:
        set_task_type(self.tasks, "TASK-013", "feature")
        self.assertEqual([], self.notes("TASK-013"))

    def test_title_change(self) -> None:
        set_task_title(self.tasks, "TASK-013", "Первая, но переименованная")
        self.assertNote("TASK-013",
                        TITLE_TEXT.format(now="Первая, но переименованная", was="Первая"))

    def test_lifecycle_notes_are_not_read_as_transitions(self) -> None:
        """Разбор переходов не должен принимать эти строки за смену статуса."""
        set_task_type(self.tasks, "TASK-013", "bug")
        set_paused(self.tasks, "TASK-013", "ждём ответ → и решение")
        set_task_title(self.tasks, "TASK-013", "Было → стало")

        for line in self.notes("TASK-013"):
            self.assertIsNone(TRANSITION_RE.match(line), line)


class ScriptLifecycleNotesTest(LifecycleNotes):
    """Путь автономного скрипта: тексты те же, подпись — источник перехода."""

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(0, done.returncode, done.stderr)
        return done

    def test_block_and_unblock(self) -> None:
        self.run_script("TASK-014", "--block", "TASK-013", "--agent", "Claude Opus 5")

        note = self.assertNote("TASK-014", BLOCK_TEXT.format(ids="TASK-013"))
        self.assertIn("· скрипт (Claude Opus 5) ·", note)
        self.assertNote("TASK-013", BLOCKS_TEXT.format(id="TASK-014"))

        self.run_script("TASK-014", "--unblock", "TASK-013")
        self.assertNote("TASK-014", UNBLOCK_TEXT.format(ids="TASK-013"))
        self.assertNote("TASK-013", UNBLOCKS_TEXT.format(id="TASK-014"))

    def test_pause_and_resume(self) -> None:
        self.run_script("TASK-013", "--pause", "ждём стенд")
        self.assertNote("TASK-013", PAUSE_TEXT.format(reason="ждём стенд"))

        self.run_script("TASK-013", "--resume")
        self.assertNote("TASK-013", RESUME_TEXT)

    def test_type_change(self) -> None:
        self.run_script("TASK-013", "--type", "bug")
        self.assertNote("TASK-013", TYPE_TEXT.format(now="bug", was="feature"))


class MirrorTest(unittest.TestCase):
    """Формулировки в скрипте — копия бэкендовых: расхождение ломает чтение."""

    def test_texts_match(self) -> None:
        spec = importlib.util.spec_from_file_location("set_status_notes", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for name in ("BLOCK_TEXT", "UNBLOCK_TEXT", "BLOCKS_TEXT", "UNBLOCKS_TEXT",
                     "PAUSE_TEXT", "RESUME_TEXT", "TYPE_TEXT", "TITLE_TEXT"):
            with self.subTest(name=name):
                import backend.notes as notes
                self.assertEqual(getattr(notes, name), getattr(module, name, None))


if __name__ == "__main__":
    unittest.main()
