"""`/api/health` не отдаёт секреты Telegram-интеграции.

Ответ health читают лаунчер (версия, папка инструмента) и доска (несекретные
флаги из `config`). Токену бота и адресу прокси с паролем там делать нечего:
эндпоинт открыт любому процессу машины. Форма настроек берёт их из
`/api/config` — там они остаются.

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

from backend import app, config, registry  # noqa: E402

TOKEN = "123456:секретный-токен"
PROXY = "http://login:пароль@proxy.example:3128"


class HealthSecretsTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name) / "home"
        home.mkdir()
        saved = (config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE,
                 registry.GLOBAL_DIR, registry.PROJECTS_FILE)
        self.addCleanup(self._restore, saved)
        config.GLOBAL_DIR = registry.GLOBAL_DIR = home
        config.GLOBAL_CONFIG_FILE = home / "config.json"
        registry.PROJECTS_FILE = home / "projects.json"

        tasks = Path(self._tmp.name) / "project" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "board.md").write_text("# Доска\n", encoding="utf-8")
        config.GLOBAL_CONFIG_FILE.write_text(json.dumps({
            "telegram": True, "telegram_token": TOKEN,
            "telegram_route": "proxy", "telegram_proxy": PROXY,
            "hide_empty_columns": True,
        }, ensure_ascii=False), encoding="utf-8")
        registry.register_project(tasks, name="project")

    @staticmethod
    def _restore(saved) -> None:
        (config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE,
         registry.GLOBAL_DIR, registry.PROJECTS_FILE) = saved

    def test_secrets_absent_anywhere_in_answer(self) -> None:
        body = json.dumps(app.api_health(), ensure_ascii=False)
        self.assertNotIn(TOKEN, body)
        self.assertNotIn("пароль", body)
        self.assertNotIn("proxy.example", body)

    def test_board_and_launcher_fields_kept(self) -> None:
        health = app.api_health()
        for key in ("version", "tool_dir", "project", "report", "capabilities"):
            self.assertIn(key, health)
        self.assertEqual("project", health["project"]["name"])
        self.assertIs(True, health["config"]["hide_empty_columns"])
        self.assertIn("delete_tasks", health["config"])
        self.assertIs(True, health["config"]["telegram"])

    def test_settings_form_still_gets_secrets(self) -> None:
        cfg = app.api_get_config()
        self.assertEqual(TOKEN, cfg["telegram_token"])
        self.assertEqual(PROXY, cfg["telegram_proxy"])


if __name__ == "__main__":
    unittest.main()
