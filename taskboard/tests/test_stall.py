"""Тесты модели простоя задачи: blocked_by / blocks / paused.

TASK-015 (эпик E001-STALL): у задачи одно производное состояние «стоит» и две
причины — ждёт другую задачу или ждёт обстоятельства. Оба конца зависимости
(`blocked_by` у одной задачи и `blocks` у другой) правит инструмент, а не
человек: разойтись они могут только от небрежности.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.stall import (  # noqa: E402
    annotate_stall,
    format_ids,
    parse_ids,
    set_blocked_by,
    set_paused,
    stall_issues,
    stall_of,
    stalled_tasks,
)
from backend.task_parser import parse_frontmatter  # noqa: E402
from backend.validator import validate_project  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-07-31 10:00
blocked_by: ~
---

## Описание

Тестовая задача.
"""


def load_script():
    """Загрузить шаблон скрипта как модуль (он живёт вне пакета)."""
    spec = importlib.util.spec_from_file_location("set_status_stall", SCRIPT)
    assert spec and spec.loader, f"не удалось загрузить {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StallProject(unittest.TestCase):
    """Общая песочница: папка tasks/ с парой задач."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)

    def make_task(self, task_id: str, title: str = "Задача", status: str = "todo") -> Path:
        path = self.tasks / f"{task_id}-{title.lower().replace(' ', '-')}.md"
        path.write_text(TASK_FILE.format(task_id=task_id, title=title, status=status),
                        encoding="utf-8")
        return path

    def meta(self, task_id: str) -> dict:
        path = next(self.tasks.glob(f"{task_id}*.md"))
        return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


class FieldParsingTest(unittest.TestCase):
    """Разбор полей: «~» и пусто — это «нет», список — через запятую."""

    def test_empty_values(self) -> None:
        for value in ("", "~", "   ", None):
            self.assertEqual([], parse_ids(value), f"значение {value!r}")

    def test_single_and_list(self) -> None:
        self.assertEqual(["TASK-013"], parse_ids("TASK-013"))
        self.assertEqual(["TASK-013", "TASK-014"], parse_ids("TASK-013, TASK-014"))
        self.assertEqual(["TASK-013", "TASK-014"], parse_ids("task-013,TASK-014"))

    def test_duplicates_collapse(self) -> None:
        self.assertEqual(["TASK-013"], parse_ids("TASK-013, TASK-013"))

    def test_format(self) -> None:
        self.assertEqual("~", format_ids([]))
        self.assertEqual("TASK-013, TASK-014", format_ids(["TASK-013", "TASK-014"]))

    def test_stall_of_meta(self) -> None:
        state = stall_of({"blocked_by": "TASK-013", "blocks": "~", "paused": "~"})
        self.assertEqual(["TASK-013"], state["blocked_by"])
        self.assertEqual([], state["blocks"])
        self.assertEqual("", state["paused"])
        self.assertTrue(state["stalled"])

    def test_paused_alone_is_stalled(self) -> None:
        state = stall_of({"paused": "ждём ответ контрагента"})
        self.assertEqual("ждём ответ контрагента", state["paused"])
        self.assertTrue(state["stalled"])

    def test_free_task(self) -> None:
        self.assertFalse(stall_of({"blocked_by": "~", "paused": "~"})["stalled"])
        self.assertFalse(stall_of({})["stalled"])


class BlockOpsTest(StallProject):
    """Простановка блокировки правит оба конца за один вызов."""

    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")
        self.make_task("TASK-015", "Третья")

    def test_block_writes_both_ends(self) -> None:
        result = set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

        self.assertTrue(result["ok"], result)
        self.assertEqual("TASK-013", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("TASK-014", self.meta("TASK-013")["blocks"])

    def test_several_blockers(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013", "TASK-015"])

        self.assertEqual(["TASK-013", "TASK-015"], parse_ids(self.meta("TASK-014")["blocked_by"]))
        self.assertEqual("TASK-014", self.meta("TASK-013")["blocks"])
        self.assertEqual("TASK-014", self.meta("TASK-015")["blocks"])

    def test_unblock_clears_other_end(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013", "TASK-015"])
        set_blocked_by(self.tasks, "TASK-014", ["TASK-015"])

        self.assertEqual(["TASK-015"], parse_ids(self.meta("TASK-014")["blocked_by"]))
        self.assertEqual("~", self.meta("TASK-013")["blocks"],
                         "снятая блокировка осталась у блокера")
        self.assertEqual("TASK-014", self.meta("TASK-015")["blocks"])

    def test_full_unblock(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-014", [])

        self.assertEqual("~", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("~", self.meta("TASK-013")["blocks"])

    def test_blocker_keeps_other_dependents(self) -> None:
        """У блокера может быть несколько зависимых — снятие одной не трёт остальных."""
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-015", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-014", [])

        self.assertEqual(["TASK-015"], parse_ids(self.meta("TASK-013")["blocks"]))

    def test_self_block_refused(self) -> None:
        result = set_blocked_by(self.tasks, "TASK-014", ["TASK-014"])

        self.assertFalse(result["ok"])
        self.assertEqual("~", self.meta("TASK-014")["blocked_by"])

    def test_unknown_task_refused(self) -> None:
        self.assertFalse(set_blocked_by(self.tasks, "TASK-404", ["TASK-013"])["ok"])

    def test_missing_blocker_is_reported_but_written(self) -> None:
        """Блокер может лежать в другом месте — поле пишем, но говорим об этом."""
        result = set_blocked_by(self.tasks, "TASK-014", ["TASK-404"])

        self.assertTrue(result["ok"], result)
        self.assertEqual(["TASK-404"], result["missing"])
        self.assertEqual("TASK-404", self.meta("TASK-014")["blocked_by"])

    def test_field_added_when_absent(self) -> None:
        """Задача, заведённая до появления полей, получает их при первой правке."""
        path = self.tasks / "TASK-020-старая.md"
        path.write_text("---\nid: TASK-020\ntitle: Старая\nstatus: todo\n---\n\nТекст.\n",
                        encoding="utf-8")

        set_blocked_by(self.tasks, "TASK-020", ["TASK-013"])

        self.assertEqual("TASK-013", self.meta("TASK-020")["blocked_by"])
        self.assertEqual("TASK-020", self.meta("TASK-013")["blocks"])

    def test_body_survives(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        path = next(self.tasks.glob("TASK-014*.md"))
        self.assertIn("## Описание", path.read_text(encoding="utf-8"))


class PauseTest(StallProject):
    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая", status="development")

    def test_pause_and_resume(self) -> None:
        set_paused(self.tasks, "TASK-013", "ждём ответ контрагента")
        self.assertEqual("ждём ответ контрагента", self.meta("TASK-013")["paused"])
        self.assertEqual("development", self.meta("TASK-013")["status"],
                         "пауза — метка, а не статус пайплайна")

        set_paused(self.tasks, "TASK-013", "")
        self.assertEqual("~", self.meta("TASK-013")["paused"])

    def test_multiline_reason_collapses(self) -> None:
        """Причина живёт одной строкой frontmatter — перенос её разорвал бы."""
        set_paused(self.tasks, "TASK-013", "ждём\nответ")
        self.assertEqual("ждём ответ", self.meta("TASK-013")["paused"])


class StalledReportTest(StallProject):
    """Срез «что сейчас стоит» — для скиллов, как --queue."""

    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")
        self.make_task("TASK-015", "Третья")

    def test_empty(self) -> None:
        report = stalled_tasks(self.tasks)
        self.assertEqual(0, report["total"])
        self.assertEqual([], report["tasks"])

    def test_reports_both_reasons(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_paused(self.tasks, "TASK-015", "ждём стенд")

        report = stalled_tasks(self.tasks)

        self.assertEqual(2, report["total"])
        by_id = {t["id"]: t for t in report["tasks"]}
        self.assertEqual(["TASK-013"], by_id["TASK-014"]["blocked_by"])
        self.assertEqual("ждём стенд", by_id["TASK-015"]["paused"])
        self.assertEqual("Вторая", by_id["TASK-014"]["title"])
        self.assertEqual("todo", by_id["TASK-014"]["status"])
        self.assertNotIn("TASK-013", by_id, "блокер сам по себе не стоит")


class IssuesTest(StallProject):
    """Проверки для валидатора: битые ссылки, расхождение концов, циклы."""

    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")

    def test_clean_project(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        self.assertEqual([], stall_issues(self.tasks))

    def test_missing_reference(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-404"])

        issues = stall_issues(self.tasks)

        self.assertTrue(any("TASK-404" in i for i in issues), issues)

    def test_one_sided_block(self) -> None:
        """Расхождение концов — ровно то, ради чего заведён инструмент."""
        path = next(self.tasks.glob("TASK-014*.md"))
        path.write_text(path.read_text(encoding="utf-8").replace(
            "blocked_by: ~", "blocked_by: TASK-013"), encoding="utf-8")

        issues = stall_issues(self.tasks)

        self.assertTrue(any("TASK-013" in i and "TASK-014" in i for i in issues), issues)

    def test_one_sided_blocks_collapse_to_one_warning(self) -> None:
        """Проект, где blocked_by ставили руками, не должен тонуть в россыпи."""
        self.make_task("TASK-016", "Четвёртая")
        for task_id, blocker in (("TASK-014", "TASK-013"), ("TASK-016", "TASK-013")):
            path = next(self.tasks.glob(f"{task_id}*.md"))
            path.write_text(path.read_text(encoding="utf-8").replace(
                "blocked_by: ~", f"blocked_by: {blocker}"), encoding="utf-8")

        issues = stall_issues(self.tasks)

        self.assertEqual(1, len(issues), issues)
        self.assertIn("TASK-014", issues[0])
        self.assertIn("TASK-016", issues[0])

    def test_fix_hint_uses_project_script_name(self) -> None:
        """Имя скрипта настраивается — подсказка не должна врать."""
        path = next(self.tasks.glob("TASK-014*.md"))
        path.write_text(path.read_text(encoding="utf-8").replace(
            "blocked_by: ~", "blocked_by: TASK-013"), encoding="utf-8")

        issues = stall_issues(self.tasks, {"status_script": "move.py"})

        self.assertIn("move.py --block", issues[0])

    def test_cycle(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-013", ["TASK-014"])

        issues = stall_issues(self.tasks)

        self.assertTrue(any("Цикл" in i for i in issues), issues)

    def test_cycle_reported_once(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-013", ["TASK-014"])

        self.assertEqual(1, sum("Цикл" in i for i in stall_issues(self.tasks)))


class AnnotateBoardTest(StallProject):
    """Карточкам доски нужно производное состояние — по образцу annotate_epics."""

    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")

    def board(self) -> dict:
        return {"columns": [{"title": "To Do", "groups": [{"tasks": [
            {"id": "TASK-013", "file": next(self.tasks.glob("TASK-013*.md")).name},
            {"id": "TASK-014", "file": next(self.tasks.glob("TASK-014*.md")).name},
        ]}]}]}

    def test_annotates_only_stalled(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

        board = annotate_stall(self.tasks, self.board())
        cards = {t["id"]: t for t in board["columns"][0]["groups"][0]["tasks"]}

        self.assertTrue(cards["TASK-014"]["stalled"])
        self.assertEqual(["TASK-013"], cards["TASK-014"]["blocked_by"])
        self.assertNotIn("stalled", cards["TASK-013"], "свободная задача полей не получает")

    def test_paused_reason_on_card(self) -> None:
        set_paused(self.tasks, "TASK-013", "ждём стенд")

        board = annotate_stall(self.tasks, self.board())
        card = board["columns"][0]["groups"][0]["tasks"][0]

        self.assertEqual("ждём стенд", card["paused"])
        self.assertTrue(card["stalled"])


class ValidatorReportTest(unittest.TestCase):
    """Проблемы простоя должны доезжать до отчёта, а не оставаться в модуле."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        self.cfg["harnesses"] = {"claude": True, "opencode": True}
        scaffold_project(self.tasks, self.cfg, {"harnesses": self.cfg["harnesses"]})

    def test_broken_reference_in_warnings(self) -> None:
        (self.tasks / "TASK-014-вторая.md").write_text(
            TASK_FILE.format(task_id="TASK-014", title="Вторая", status="backlog").replace(
                "blocked_by: ~", "blocked_by: TASK-404"), encoding="utf-8")

        warnings = validate_project(self.tasks, self.cfg)["warnings"]

        self.assertTrue(any("TASK-404" in w for w in warnings), warnings)

    def test_clean_project_has_no_stall_warnings(self) -> None:
        warnings = validate_project(self.tasks, self.cfg)["warnings"]

        self.assertFalse(any("blocked_by" in w or "Цикл" in w for w in warnings), warnings)


class CreateTaskBlockTest(unittest.TestCase):
    """Задача, заведённая с блокером, тоже обязана иметь оба конца."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": True}
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})

    def create(self, title: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks / "create_task.py"), "-t", title,
             "-d", "описание", "-c", "критерии", *args],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root), timeout=60)

    def test_blocker_gets_back_reference(self) -> None:
        self.assertEqual(0, self.create("Первая").returncode)
        done = self.create("Вторая", "-b", "TASK-001")
        self.assertEqual(0, done.returncode, done.stderr)

        blocker = next(self.tasks.glob("TASK-001*.md")).read_text(encoding="utf-8")
        blocked = next(self.tasks.glob("TASK-002*.md")).read_text(encoding="utf-8")

        self.assertIn("blocks: TASK-002", blocker)
        self.assertIn("blocked_by: TASK-001", blocked)
        self.assertEqual([], stall_issues(self.tasks))


class ScriptStallTest(StallProject):
    """Те же операции из автономного tasks/set_status.py."""

    def setUp(self) -> None:
        super().setUp()
        self.make_task("TASK-013", "Первая")
        self.make_task("TASK-014", "Вторая")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_block_and_unblock(self) -> None:
        done = self.run_script("TASK-014", "--block", "TASK-013")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("TASK-013", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("TASK-014", self.meta("TASK-013")["blocks"])

        self.run_script("TASK-014", "--unblock", "TASK-013")
        self.assertEqual("~", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("~", self.meta("TASK-013")["blocks"])

    def test_pause_and_resume(self) -> None:
        self.run_script("TASK-013", "--pause", "ждём ответ контрагента")
        self.assertEqual("ждём ответ контрагента", self.meta("TASK-013")["paused"])

        self.run_script("TASK-013", "--resume")
        self.assertEqual("~", self.meta("TASK-013")["paused"])

    def test_stalled_json(self) -> None:
        self.run_script("TASK-014", "--block", "TASK-013")
        self.run_script("TASK-013", "--pause", "ждём стенд")

        done = self.run_script("--stalled")
        report = json.loads(done.stdout)

        self.assertEqual(2, report["total"])
        by_id = {t["id"]: t for t in report["tasks"]}
        self.assertEqual(["TASK-013"], by_id["TASK-014"]["blocked_by"])
        self.assertEqual("ждём стенд", by_id["TASK-013"]["paused"])

    def test_status_change_keeps_stall_fields(self) -> None:
        """Пауза переживает смену статуса: это метка, а не этап."""
        module = load_script()
        board = self.tasks / "board.md"
        board.write_text("# Board\n\n## To Do\n\n- TASK-013 · [Первая](%s)\n\n## Development\n\n_(нет)_\n"
                         % next(self.tasks.glob("TASK-013*.md")).name, encoding="utf-8")

        self.run_script("TASK-013", "--pause", "ждём стенд")
        result = module.set_status(self.tasks, "TASK-013", "development")

        self.assertTrue(result["ok"], result)
        self.assertEqual("ждём стенд", self.meta("TASK-013")["paused"])
        self.assertEqual("development", self.meta("TASK-013")["status"])


if __name__ == "__main__":
    unittest.main()
