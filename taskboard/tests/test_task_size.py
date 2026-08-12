"""Тесты размера задачи (TASK-009).

Размер — оценка объёма работы (`S`, `M`, `L`, `XL`) в поле `size:` файла
задачи. Он отвечает на вопрос «браться ли за это сейчас», поэтому живёт рядом
с типом: закрытый список поставки, метка на превью доски, отбор на доске.

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

from backend.config import DEFAULTS, TASK_SIZES  # noqa: E402
from backend.scaffold import TASKS_TEMPLATES, scaffold_project  # noqa: E402
from backend.task_parser import (annotate_marks, parse_frontmatter,  # noqa: E402
                                 set_task_size)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _frontmatter(path: Path) -> dict:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return meta


class SizeCatalogTest(unittest.TestCase):
    """Один список размеров на бэкенд, скрипт и фронт — иначе разъедутся."""

    def test_catalog_covers_four_sizes(self) -> None:
        self.assertEqual(["S", "M", "L", "XL"], list(TASK_SIZES),
                         "порядок каталога — порядок возрастания объёма")

    def test_every_size_has_label_and_hint(self) -> None:
        """Буквы одной подписи мало: «L» не объясняет, чем она отличается от M."""
        for key, meta in TASK_SIZES.items():
            with self.subTest(size=key):
                self.assertTrue(meta.get("label"), f"у размера {key} нет подписи")
                self.assertTrue(meta.get("hint"), f"у размера {key} нет пояснения")

    def test_script_knows_same_sizes(self) -> None:
        from tests.test_set_status_script import load_script

        script = load_script()
        self.assertEqual(list(TASK_SIZES), list(script.TASK_SIZES))
        for key, meta in TASK_SIZES.items():
            self.assertEqual(meta["label"], script.TASK_SIZES[key]["label"],
                             f"подпись размера {key} разошлась со скриптом")

    def test_frontend_catalog_matches(self) -> None:
        text = (FRONTEND / "taskSizes.js").read_text(encoding="utf-8")
        for key, meta in TASK_SIZES.items():
            self.assertIn(f"{key}:", text, f"фронт не знает размер {key}")
            self.assertIn(meta["hint"], text,
                          f"пояснение размера {key} разошлось с бэкендом")


class SizeStoredTest(unittest.TestCase):
    """Размер пишется в frontmatter и переживает правку файла."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": True}
        scaffold_project(self.tasks_dir, cfg, {"harnesses": cfg["harnesses"]})
        subprocess.run(
            [sys.executable, str(self.tasks_dir / "create_task.py"),
             "-t", "Проверка размера", "-d", "описание", "-c", "критерии"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.task = next(self.tasks_dir.glob("TASK-*.md"))

    def status(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks_dir / "set_status.py"), *args],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))

    def test_new_task_has_no_size(self) -> None:
        """Размер — оценка, а не свойство заведения: при создании его нет."""
        self.assertIn(str(_frontmatter(self.task).get("size", "~")), ("~", "", "None"))

    def test_script_sets_size(self) -> None:
        result = self.status("TASK-001", "--size", "L")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("L", _frontmatter(self.task)["size"])

    def test_lowercase_accepted(self) -> None:
        """Человек печатает `m`, а хранится канон: буквы размера прописные."""
        self.status("TASK-001", "--size", "m")
        self.assertEqual("M", _frontmatter(self.task)["size"])

    def test_unknown_size_rejected(self) -> None:
        result = self.status("TASK-001", "--size", "XXL")
        self.assertNotEqual(0, result.returncode, "чужой размер принят")
        self.assertNotIn("size: XXL", self.task.read_text(encoding="utf-8"))

    def test_sizes_listed_by_script(self) -> None:
        """Список спрашивают у скрипта, а не помнят."""
        result = self.status("--sizes")
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(list(TASK_SIZES), [s["key"] for s in payload["sizes"]])

    def test_change_goes_to_comments(self) -> None:
        """Оценка объёма — решение агента, и оно объясняет ход работы."""
        self.status("TASK-001", "--size", "S", "--agent", "Модель")
        self.status("TASK-001", "--size", "XL", "--agent", "Модель")
        text = self.task.read_text(encoding="utf-8")
        self.assertIn("XL", text.split("## Комментарии")[1])

    def test_same_size_is_not_an_event(self) -> None:
        self.status("TASK-001", "--size", "M", "--agent", "Модель")
        before = self.task.read_text(encoding="utf-8").count("размер")
        self.status("TASK-001", "--size", "M", "--agent", "Модель")
        after = self.task.read_text(encoding="utf-8").count("размер")
        self.assertEqual(before, after, "повтор того же размера записан событием")

    def test_template_declares_size(self) -> None:
        """Поле есть в эталоне: задача без размера показывает, что его ставят."""
        template = (TASKS_TEMPLATES / "_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertIn("size: ~", template)


class SizeFromUiTest(unittest.TestCase):
    """Правка из окна задачи — тот же закрытый список, что у скрипта."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)
        (self.tasks_dir / "TASK-001-test.md").write_text(
            "---\nid: TASK-001\ntitle: Тест\nstatus: todo\n---\n\n## Описание\n\nТекст.\n",
            encoding="utf-8")

    def test_size_written(self) -> None:
        result = set_task_size(self.tasks_dir, "TASK-001", "XL")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("XL", _frontmatter(self.tasks_dir / "TASK-001-test.md")["size"])

    def test_unknown_size_refused(self) -> None:
        result = set_task_size(self.tasks_dir, "TASK-001", "огромная")
        self.assertFalse(result.get("ok"), "чужое значение принято")

    def test_size_cleared(self) -> None:
        """Оценку снимают: поставленная наугад хуже отсутствующей."""
        set_task_size(self.tasks_dir, "TASK-001", "L")
        result = set_task_size(self.tasks_dir, "TASK-001", "")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("~", _frontmatter(self.tasks_dir / "TASK-001-test.md")["size"])

    def test_patch_endpoint_knows_size(self) -> None:
        app = (Path(__file__).resolve().parent.parent
               / "backend" / "app.py").read_text(encoding="utf-8")
        model = app[app.index("class TaskUpdateIn"):app.index("class ConfigIn")]
        self.assertIn("size:", model, "поле размера не принимается API правки задачи")


class SizeOnBoardTest(unittest.TestCase):
    """Метка размера приезжает на карточку тем же проходом, что и тип."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)

    def _task(self, task_id: str, extra: str) -> None:
        (self.tasks_dir / f"{task_id}-test.md").write_text(
            f"---\nid: {task_id}\ntitle: Тест\nstatus: todo\n{extra}\n---\n\nТекст.\n",
            encoding="utf-8")

    def _board(self, *ids: str) -> dict:
        return {"columns": [{"status": "todo", "groups": [{"tasks": [
            {"id": i, "file": f"{i}-test.md"} for i in ids]}]}]}

    def _tasks(self, board: dict) -> list[dict]:
        return board["columns"][0]["groups"][0]["tasks"]

    def test_size_and_type_come_together(self) -> None:
        """Один проход по файлу: доска читает каждую задачу единожды."""
        self._task("TASK-001", "type: bug\nsize: L")
        board = annotate_marks(self.tasks_dir, self._board("TASK-001"))
        task = self._tasks(board)[0]
        self.assertEqual("bug", task["type"])
        self.assertEqual("L", task["size"])

    def test_lowercase_size_normalized(self) -> None:
        """Файл правили руками — метка всё равно рисуется."""
        self._task("TASK-001", "size: xl")
        board = annotate_marks(self.tasks_dir, self._board("TASK-001"))
        self.assertEqual("XL", self._tasks(board)[0]["size"])

    def test_unknown_size_ignored(self) -> None:
        self._task("TASK-001", "size: гигантская")
        board = annotate_marks(self.tasks_dir, self._board("TASK-001"))
        self.assertNotIn("size", self._tasks(board)[0])

    def test_task_without_size_stays_clean(self) -> None:
        self._task("TASK-001", "type: bug")
        board = annotate_marks(self.tasks_dir, self._board("TASK-001"))
        self.assertNotIn("size", self._tasks(board)[0])


class SizeFilterTest(unittest.TestCase):
    """Отбор по размеру — чипы в шапке доски, рядом с поиском и «стоят»."""

    def test_header_has_size_chips(self) -> None:
        header = (FRONTEND / "components" / "Header.jsx").read_text(encoding="utf-8")
        self.assertIn("sizes", header, "в шапке нет отбора по размеру")

    def test_board_filters_by_size(self) -> None:
        """Фильтры складываются: размер сужает найденное, а не заменяет его."""
        app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("sizesOnly", app, "доска не знает про отбор по размеру")


class SizePolicyTest(unittest.TestCase):
    """Оценку ставит агент, когда контекст изучен, — и об этом сказано в скилле."""

    SKILLS = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
              / ".claude" / "skills")

    def test_start_task_asks_for_size(self) -> None:
        text = (self.SKILLS / "start-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--size", text, "скилл старта не проставляет размер")

    def test_rules_describe_size(self) -> None:
        rules = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
                 / "rules_section.md").read_text(encoding="utf-8")
        self.assertIn("--sizes", rules,
                      "правила не говорят, где спросить список размеров")


if __name__ == "__main__":
    unittest.main()
