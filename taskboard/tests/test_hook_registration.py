"""Регистрация хука в настройках проекта — своя запись в чужом файле (TASK-198).

Файлы обработчиков едут поставкой и сверяются с эталоном, но у Claude Code хук
не работает, пока на него не сослались из `.claude/settings.json`. Этот файл
принадлежит пользователю: там его разрешения и, возможно, собственные хуки.
Модель «файл равен эталону» к нему неприменима — сверять чужое значит показывать
баннер на каждую его запись.

Приём тот же, что у секции правил внутри CLAUDE.md: правим **только свою
запись**, остальное оставляем владельцу дословно.

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

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (hook_registered, register_hook,  # noqa: E402
                              scaffold_project, unregister_hook)
from backend.validator import validate_project  # noqa: E402

CLAUDE_ONLY = {"claude": True, "opencode": False}


class RegistrationTest(unittest.TestCase):
    """Запись появляется, чужое не страдает."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": CLAUDE_ONLY}

    def settings(self) -> Path:
        return self.root / ".claude" / "settings.json"

    def read(self) -> dict:
        return json.loads(self.settings().read_text(encoding="utf-8"))

    def deploy(self) -> None:
        scaffold_project(self.tasks, self.cfg, {"harnesses": CLAUDE_ONLY})

    def test_file_is_created_when_absent(self) -> None:
        self.deploy()

        self.assertTrue(self.settings().is_file())
        self.assertTrue(hook_registered(self.root))

    def test_own_settings_survive(self) -> None:
        """Разрешения и прочее принадлежат пользователю — их не трогаем."""
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(json.dumps({
            "permissions": {"allow": ["Bash(git status)"]},
            "env": {"MY": "1"},
        }, ensure_ascii=False), encoding="utf-8")

        self.deploy()

        data = self.read()
        self.assertEqual({"allow": ["Bash(git status)"]}, data["permissions"])
        self.assertEqual({"MY": "1"}, data["env"])
        self.assertTrue(hook_registered(self.root))

    def test_foreign_hooks_survive(self) -> None:
        """Чужая запись в том же событии — не наша забота и не наша потеря."""
        foreign = {"matcher": "Edit",
                   "hooks": [{"type": "command", "command": "./my-lint.sh"}]}
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(json.dumps({"hooks": {"PostToolUse": [foreign]}},
                                              ensure_ascii=False), encoding="utf-8")

        self.deploy()

        entries = self.read()["hooks"]["PostToolUse"]
        self.assertIn(foreign, entries)
        self.assertEqual(2, len(entries))

    def test_registration_is_idempotent(self) -> None:
        self.deploy()
        register_hook(self.root, self.cfg)
        register_hook(self.root, self.cfg)

        self.assertEqual(1, len(self.read()["hooks"]["PostToolUse"]))

    def test_emptied_entry_is_reused(self) -> None:
        """Команду вынули руками: своя возвращается на место, а не дублем рядом."""
        self.deploy()
        data = self.read()
        data["hooks"]["PostToolUse"][0]["hooks"] = []
        self.settings().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        register_hook(self.root, self.cfg)

        entries = self.read()["hooks"]["PostToolUse"]
        self.assertEqual(1, len(entries), "мёртвая запись должна быть занята, а не удвоена")
        self.assertTrue(hook_registered(self.root))

    def test_dead_leftovers_are_collapsed(self) -> None:
        """Оболочка рядом с живой записью — наш мусор, и он убирается."""
        self.deploy()
        data = self.read()
        data["hooks"]["PostToolUse"].insert(0, {"matcher": "Bash", "hooks": []})
        self.settings().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        register_hook(self.root, self.cfg)

        entries = self.read()["hooks"]["PostToolUse"]
        self.assertEqual(1, len(entries), entries)
        self.assertTrue(hook_registered(self.root))

    def test_foreign_entry_with_commands_is_not_taken(self) -> None:
        """Занимаем только мёртвую: живая чужая запись с тем же matcher — не наша."""
        foreign = {"matcher": "Bash",
                   "hooks": [{"type": "command", "command": "./my-audit.sh"}]}
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(json.dumps({"hooks": {"PostToolUse": [foreign]}},
                                              ensure_ascii=False), encoding="utf-8")

        self.deploy()

        entries = self.read()["hooks"]["PostToolUse"]
        self.assertIn(foreign, entries)
        self.assertEqual(2, len(entries))

    def test_unregister_removes_only_ours(self) -> None:
        foreign = {"matcher": "Edit",
                   "hooks": [{"type": "command", "command": "./my-lint.sh"}]}
        self.deploy()
        data = self.read()
        data["hooks"]["PostToolUse"].append(foreign)
        self.settings().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        unregister_hook(self.root)

        entries = self.read()["hooks"]["PostToolUse"]
        self.assertEqual([foreign], entries)
        self.assertFalse(hook_registered(self.root))

    def test_broken_file_is_not_destroyed(self) -> None:
        """Файл пользователя с битым JSON: молчим, а не переписываем его начисто."""
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text("{ это не json", encoding="utf-8")

        self.deploy()

        self.assertEqual("{ это не json", self.settings().read_text(encoding="utf-8"))
        self.assertFalse(hook_registered(self.root))


class BannerTest(unittest.TestCase):
    """Отсутствующая регистрация видна и чинится кнопкой."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": CLAUDE_ONLY}
        scaffold_project(self.tasks, self.cfg, {"harnesses": CLAUDE_ONLY})

    def codes(self) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks, self.cfg)["degraded"]]

    def test_quiet_when_registered(self) -> None:
        self.assertNotIn("no_hook_registration", self.codes())

    def test_missing_registration_is_reported(self) -> None:
        unregister_hook(self.root)

        self.assertIn("no_hook_registration", self.codes())

    def test_button_restores_it(self) -> None:
        unregister_hook(self.root)

        scaffold_project(self.tasks, self.cfg, {"parts": ["hook_registration"]})

        self.assertNotIn("no_hook_registration", self.codes())

    def test_opencode_only_project_is_quiet(self) -> None:
        """Регистрация нужна только Claude Code: opencode подхватывает плагин сам."""
        cfg = {**DEFAULTS, "harnesses": {"claude": False, "opencode": True}}
        root = Path(self._tmp.name) / "второй"
        tasks = root / "tasks"
        scaffold_project(tasks, cfg, {"harnesses": cfg["harnesses"]})

        codes = [d["code"] for d in validate_project(tasks, cfg)["degraded"]]

        self.assertNotIn("no_hook_registration", codes)


class ButtonWiringTest(unittest.TestCase):
    """У кода в баннере должно быть действие, иначе кнопки не будет."""

    def test_code_has_an_action(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "frontend" / "src"
                  / "App.jsx").read_text(encoding="utf-8")

        self.assertIn("no_hook_registration:", source)


if __name__ == "__main__":
    unittest.main()
