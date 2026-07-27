"""Тесты запуска create_task.py из UI: параметры доходят до скрипта.

Шов между API и автономным скриптом легко теряет поле — проверяем на
скрипте-заглушке, который записывает полученные аргументы.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.create_task_runner import create_task  # noqa: E402

STUB = '''import json, sys
from pathlib import Path
Path(__file__).parent.joinpath("argv.json").write_text(
    json.dumps(sys.argv[1:], ensure_ascii=False), encoding="utf-8")
print("ID: TASK-042")
'''


class CreateTaskRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "create_task.py").write_text(STUB, encoding="utf-8")
        self.cfg = {"create_script": "create_task.py"}

    def _argv(self) -> list[str]:
        return json.loads((self.tasks / "argv.json").read_text(encoding="utf-8"))

    def test_epic_is_passed_to_script(self) -> None:
        """TASK-020: эпик, указанный в UI, должен попасть во frontmatter задачи."""
        result = create_task(self.tasks, self.cfg,
                             {"title": "Задача", "epic": "E056-18500"})

        self.assertTrue(result["ok"], result)
        argv = self._argv()
        self.assertIn("-e", argv)
        self.assertEqual("E056-18500", argv[argv.index("-e") + 1])

    def test_empty_epic_not_passed(self) -> None:
        create_task(self.tasks, self.cfg, {"title": "Задача", "epic": ""})
        self.assertNotIn("-e", self._argv())

    def test_title_required(self) -> None:
        result = create_task(self.tasks, self.cfg, {"title": "   "})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
