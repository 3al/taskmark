"""Перестановка задачи внутри раздела доски скриптом (TASK-238).

Порядок в разделе — это очередь, и агенту его переставляют по просьбе человека.
Без команды он правил `board.md` руками: скрипт держит связку доски и файла, а
ручная правка — нет. Перестановка при этом **не переход**: статус, дата в строке
и хронология задачи остаются как были.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_set_status_script import SCRIPT, TASK_FILE, load_script  # noqa: E402

BOARD = """# Доска

## Backlog

### Фичи

- TASK-001 · [Первая](TASK-001-test.md) · Claude · 2026-01-01
- TASK-002 · [Вторая](TASK-002-test.md) · Claude · 2026-01-02

### Баги

- TASK-003 · [Третья](TASK-003-test.md) · Claude · 2026-01-03

## Queue

- TASK-011 · [Одиннадцатая](TASK-011-test.md) · Claude · 2026-02-01
- TASK-012 · [Двенадцатая](TASK-012-test.md) · Claude · 2026-02-02
- TASK-013 · [Тринадцатая](TASK-013-test.md) · Claude · 2026-02-03
- TASK-014 · [Четырнадцатая](TASK-014-test.md) · Claude · 2026-02-04

## Development

_(нет)_

## Done

- TASK-021 · [Двадцать первая](TASK-021-test.md) · Claude · 2026-03-01
"""

STATUS = {"TASK-001": "backlog", "TASK-002": "backlog", "TASK-003": "backlog",
          "TASK-011": "queued", "TASK-012": "queued", "TASK-013": "queued",
          "TASK-014": "queued", "TASK-021": "done"}


class ReorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir()
        self.board = self.tasks / "board.md"
        self.board.write_text(BOARD, encoding="utf-8")
        for task_id, status in STATUS.items():
            (self.tasks / f"{task_id}-test.md").write_text(
                TASK_FILE.format(task_id=task_id, title="Тестовая", status=status)
                + "\n## Комментарии\n\n## История коммитов\n", encoding="utf-8")
        self.mod = load_script()

    def _ids(self, section: str) -> list[str]:
        """Номера задач раздела ## по порядку строк доски."""
        out, current = [], None
        for line in self.board.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
            elif current == section and line.startswith("- TASK-"):
                out.append(line[2:10])
        return out

    def _queue(self) -> list[str]:
        return [t["id"] for t in self.mod.queue(self.tasks, limit=0)["tasks"]]

    # --- Место в разделе ---

    def test_номер_ставит_задачу_на_это_место(self) -> None:
        result = self.mod.reorder(self.tasks, "TASK-014", position="2")
        self.assertTrue(result["ok"], result)
        self.assertEqual(["TASK-011", "TASK-014", "TASK-012", "TASK-013"], self._ids("Queue"))
        self.assertEqual(2, result["position"])

    def test_номер_вниз_по_очереди(self) -> None:
        self.mod.reorder(self.tasks, "TASK-011", position="3")
        self.assertEqual(["TASK-012", "TASK-013", "TASK-011", "TASK-014"], self._ids("Queue"))

    def test_номер_совпадает_с_нумерацией_queue(self) -> None:
        """Позиция, названная командой, — та самая, что потом покажет `--queue`."""
        for task_id, pos in (("TASK-013", 1), ("TASK-011", 4), ("TASK-014", 2)):
            with self.subTest(task=task_id, position=pos):
                self.mod.reorder(self.tasks, task_id, position=str(pos))
                entry = next(t for t in self.mod.queue(self.tasks, limit=0)["tasks"]
                             if t["id"] == task_id)
                self.assertEqual(pos, entry["position"])

    def test_start_и_end(self) -> None:
        self.mod.reorder(self.tasks, "TASK-013", position="start")
        self.assertEqual("TASK-013", self._queue()[0])
        self.mod.reorder(self.tasks, "TASK-013", position="end")
        self.assertEqual("TASK-013", self._queue()[-1])

    def test_after_ставит_сразу_за_названной(self) -> None:
        result = self.mod.reorder(self.tasks, "TASK-011", after="TASK-013")
        self.assertTrue(result["ok"], result)
        self.assertEqual(["TASK-012", "TASK-013", "TASK-011", "TASK-014"], self._ids("Queue"))

    def test_остальные_строки_не_переставляются_и_не_меняются(self) -> None:
        before = self.board.read_text(encoding="utf-8").splitlines()
        self.mod.reorder(self.tasks, "TASK-014", position="1")
        after = self.board.read_text(encoding="utf-8").splitlines()
        moved = "- TASK-014 · [Четырнадцатая](TASK-014-test.md) · Claude · 2026-02-04"
        self.assertIn(moved, after, "строка задачи изменилась: дата и исполнитель — "
                                    "след перехода, а перестановка переходом не является")
        self.assertEqual([ln for ln in before if ln != moved],
                         [ln for ln in after if ln != moved])

    # --- Перестановка — не переход ---

    def test_статус_и_хронология_не_трогаются(self) -> None:
        path = self.tasks / "TASK-014-test.md"
        before = path.read_text(encoding="utf-8")
        self.mod.reorder(self.tasks, "TASK-014", position="1")
        self.assertEqual(before, path.read_text(encoding="utf-8"))

    # --- Подразделы ---

    def test_after_не_перескакивает_заголовок_подраздела(self) -> None:
        """За последней задачей подраздела — значит в нём, а не под следующим ###."""
        self.mod.reorder(self.tasks, "TASK-003", after="TASK-002")
        text = self.board.read_text(encoding="utf-8")
        self.assertLess(text.index("- TASK-003"), text.index("### Баги"))
        self.assertLess(text.index("- TASK-002"), text.index("- TASK-003"))

    def test_опустевший_подраздел_получает_заглушку(self) -> None:
        self.mod.reorder(self.tasks, "TASK-003", after="TASK-001")
        lines = self.board.read_text(encoding="utf-8").splitlines()
        tail = lines[lines.index("### Баги") + 1:lines.index("## Queue")]
        self.assertIn("_(нет)_", [ln.strip() for ln in tail])

    def test_номер_в_разделе_с_подразделами_считает_насквозь(self) -> None:
        self.mod.reorder(self.tasks, "TASK-001", position="3")
        self.assertEqual(["TASK-002", "TASK-003", "TASK-001"], self._ids("Backlog"))

    # --- Отказы ---

    def test_after_из_другого_раздела_отказ(self) -> None:
        before = self.board.read_text(encoding="utf-8")
        result = self.mod.reorder(self.tasks, "TASK-011", after="TASK-021")
        self.assertFalse(result["ok"])
        self.assertIn("Done", result["error"])
        self.assertEqual(before, self.board.read_text(encoding="utf-8"))

    def test_номер_за_пределами_раздела_отказ(self) -> None:
        for bad in ("0", "5", "-1", "первая"):
            with self.subTest(position=bad):
                result = self.mod.reorder(self.tasks, "TASK-011", position=bad)
                self.assertFalse(result["ok"], result)

    def test_after_сама_за_собой_отказ(self) -> None:
        self.assertFalse(self.mod.reorder(self.tasks, "TASK-011", after="TASK-011")["ok"])

    def test_нет_на_доске_отказ(self) -> None:
        self.assertFalse(self.mod.reorder(self.tasks, "TASK-099", position="1")["ok"])

    # --- CLI ---

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")

    def test_cli_position_без_статуса_переставляет(self) -> None:
        proc = self._run("TASK-014", "--position", "1")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("TASK-014", self._queue()[0])
        self.assertIn("1", proc.stdout)

    def test_cli_after_без_статуса_переставляет(self) -> None:
        proc = self._run("TASK-011", "--after", "TASK-012")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(["TASK-012", "TASK-011"], self._queue()[:2])

    def test_cli_перестановка_не_требует_via(self) -> None:
        """Гейт источника перехода — про смену этапа, перестановку он не касается."""
        proc = self._run("TASK-013", "--position", "end")
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_cli_отказ_ненулевой_код(self) -> None:
        proc = self._run("TASK-011", "--after", "TASK-021")
        self.assertEqual(1, proc.returncode)
        self.assertEqual(["TASK-011", "TASK-012", "TASK-013", "TASK-014"], self._queue())

    def test_cli_номер_со_сменой_статуса_отказ(self) -> None:
        """Номер места — про раздел, в котором задача уже лежит."""
        proc = self._run("TASK-011", "development", "--position", "2", "--manual", "тест")
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("status: queued",
                      (self.tasks / "TASK-011-test.md").read_text(encoding="utf-8"))
        self.assertIn("TASK-011", self._ids("Queue"))


if __name__ == "__main__":
    unittest.main()
