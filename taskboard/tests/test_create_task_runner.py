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


# Скрипт проекта, развёрнутый до появления флага: ровно так ведёт себя argparse
# у старого create_task.py — код 2 и usage в stderr
OLD_STUB = r'''import json, sys
from pathlib import Path
argv = sys.argv[1:]
if "--origin" in argv:
    sys.stderr.write("usage: create_task.py [-h] [-t TITLE]\n"
                     "create_task.py: error: unrecognized arguments: --origin\n")
    sys.exit(2)
Path(__file__).parent.joinpath("argv.json").write_text(
    json.dumps(argv, ensure_ascii=False), encoding="utf-8")
print("ID: TASK-042")
'''


class OldScriptTest(unittest.TestCase):
    """Скрипты проекта обновляет человек, и бэкенд приезжает раньше них.

    Новый флаг не должен закрывать вход из чата до того, как человек нажмёт
    баннер обновления: задача важнее метки, которую он несёт.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "create_task.py").write_text(OLD_STUB, encoding="utf-8")
        self.cfg = {"create_script": "create_task.py"}

    def _argv(self) -> list[str]:
        return json.loads((self.tasks / "argv.json").read_text(encoding="utf-8"))

    def test_задача_заводится_без_метки(self) -> None:
        result = create_task(self.tasks, self.cfg,
                             {"title": "Задача", "origin": "telegram:-100"})
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(result.get("id"), "TASK-042")
        self.assertNotIn("--origin", self._argv())

    def test_остальные_поля_доезжают(self) -> None:
        create_task(self.tasks, self.cfg,
                    {"title": "Задача", "author": "@petya",
                     "origin": "telegram:-100"})
        argv = self._argv()
        self.assertEqual(argv[argv.index("--author") + 1], "@petya")

    def test_чужая_ошибка_скрипта_остаётся_ошибкой(self) -> None:
        """Повтор — только для неизвестного флага, а не для любого отказа."""
        (self.tasks / "create_task.py").write_text(
            "import sys\n"
            'sys.stderr.write("no write access")\n'
            "sys.exit(1)\n",
            encoding="utf-8")
        result = create_task(self.tasks, self.cfg, {"title": "Задача"})
        self.assertFalse(result.get("ok"))
        self.assertIn("no write access", result.get("error", ""))


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

    def test_criteria_key_absent_leaves_default(self) -> None:
        """Нет ключа — критерии выбирает скрипт (дефолт формы)."""
        create_task(self.tasks, self.cfg, {"title": "Задача"})
        self.assertNotIn("-c", self._argv())

    def test_empty_criteria_is_a_choice(self) -> None:
        """Пустой ключ — «критериев нет»: задача не должна их выдумывать."""
        create_task(self.tasks, self.cfg, {"title": "Задача", "criteria": ""})
        argv = self._argv()
        self.assertIn("-c", argv)
        self.assertEqual("", argv[argv.index("-c") + 1])

    def test_empty_task_type_is_a_choice(self) -> None:
        """Пустой ключ — «типа нет»: скрипт не должен подставлять feature."""
        create_task(self.tasks, self.cfg, {"title": "Задача", "task_type": ""})
        argv = self._argv()
        self.assertIn("--type", argv)
        self.assertEqual("", argv[argv.index("--type") + 1])

    def test_explicit_section_is_passed(self) -> None:
        """Источник без типа задаёт рубрику сам."""
        create_task(self.tasks, self.cfg,
                    {"title": "Задача", "section": "Из Telegram"})
        argv = self._argv()
        self.assertEqual("Из Telegram", argv[argv.index("--section") + 1])

    def test_section_absent_by_default(self) -> None:
        """Форма рубрику не передаёт: её выводит тип задачи."""
        create_task(self.tasks, self.cfg, {"title": "Задача"})
        self.assertNotIn("--section", self._argv())

    def test_title_required(self) -> None:
        result = create_task(self.tasks, self.cfg, {"title": "   "})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
