"""Дефолты не должны замерзать при первом запуске (TASK-088).

`load_global_config()` записывала в `~/.taskboard/config.json` весь `DEFAULTS`
целиком, и дальше значения из файла всегда побеждали дефолты. Изменение
поставки не доезжало ни до кого, у кого конфиг уже создан: обнаружилось на
`release_manifest_url` после переименования репозитория — проверка обновлений
продолжала ходить на старый адрес.

Лечение: в файл писать только то, что пользователь менял, плюс разовая чистка
существующих конфигов миграцией.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.migrations import migrate_global_config  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "backend" / "app.py"


class GlobalConfigTest(unittest.TestCase):
    """Глобальный конфиг подменяется на временный: он per-user."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = (config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE)
        config.GLOBAL_DIR = Path(self._tmp.name)
        config.GLOBAL_CONFIG_FILE = Path(self._tmp.name) / "config.json"

    def tearDown(self) -> None:
        config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE = self._orig

    def stored(self) -> dict:
        """Что реально лежит в файле, без дефолтов."""
        return json.loads(config.GLOBAL_CONFIG_FILE.read_text(encoding="utf-8"))

    def write(self, data: dict) -> None:
        config.GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        config.GLOBAL_CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")


class FirstRunTest(GlobalConfigTest):
    def test_file_is_created(self) -> None:
        """Файл по-прежнему появляется: пользователю есть что править руками."""
        config.load_global_config()

        self.assertTrue(config.GLOBAL_CONFIG_FILE.is_file(),
                        "конфиг перестал создаваться при первом запуске")

    def test_file_is_not_a_snapshot_of_defaults(self) -> None:
        config.load_global_config()

        self.assertEqual({}, self.stored(),
                         "в файл снова записан слепок дефолтов — они замёрзнут")

    def test_effective_config_is_still_complete(self) -> None:
        cfg = config.load_global_config()

        self.assertEqual(config.DEFAULTS["port"], cfg["port"])
        self.assertEqual(config.DEFAULTS["theme"], cfg["theme"])


class DefaultsFollowDeliveryTest(GlobalConfigTest):
    def test_changed_default_reaches_existing_user(self) -> None:
        """Ключ, которого пользователь не трогал, следует за поставкой."""
        self.write({"theme": "light"})

        # Как будто вышла новая версия с другим адресом манифеста
        with patch.dict(config.DEFAULTS,
                        {"release_manifest_url": "https://новый/release.json"}):
            cfg = config.load_global_config()

        self.assertEqual("https://новый/release.json", cfg["release_manifest_url"])
        self.assertEqual("light", cfg["theme"], "правка пользователя потерялась")

    def test_user_value_survives_delivery_change(self) -> None:
        self.write({"port": 9999})

        with patch.dict(config.DEFAULTS, {"port": 7777}):
            cfg = config.load_global_config()

        self.assertEqual(9999, cfg["port"], "дефолт затёр выбор пользователя")


class SaveWritesOnlyChangedTest(GlobalConfigTest):
    def test_only_changed_key_lands_in_file(self) -> None:
        config.save_global_config({"theme": "light"})

        self.assertEqual({"theme": "light"}, self.stored())

    def test_value_equal_to_default_is_not_stored(self) -> None:
        """Совпало с дефолтом — поведение то же, а ключ снова следует за поставкой."""
        config.save_global_config({"port": config.DEFAULTS["port"]})

        self.assertNotIn("port", self.stored())
        self.assertEqual(config.DEFAULTS["port"], config.load_global_config()["port"])

    def test_keys_outside_defaults_are_kept(self) -> None:
        """У пользовательских пресетов дефолта нет — их отбрасывать нельзя."""
        config.save_global_config({"criteria_presets": ["Нагрузочное"]})

        self.assertEqual(["Нагрузочное"], self.stored().get("criteria_presets"))

    def test_previous_choices_are_not_lost(self) -> None:
        config.save_global_config({"theme": "light"})
        config.save_global_config({"port": 9999})

        self.assertEqual({"theme": "light", "port": 9999}, self.stored())

    def test_returning_to_default_drops_the_key(self) -> None:
        config.save_global_config({"theme": "light"})
        config.save_global_config({"theme": config.DEFAULTS["theme"]})

        self.assertEqual({}, self.stored())


class MigrationTest(GlobalConfigTest):
    """Разовая чистка: у существующих пользователей файл — слепок дефолтов."""

    def test_default_copies_are_removed(self) -> None:
        self.write({**config.DEFAULTS, "theme": "light"})

        migrate_global_config()

        self.assertEqual({"theme": "light"}, self.stored())

    def test_user_values_survive(self) -> None:
        self.write({**config.DEFAULTS, "port": 9999, "update_check": "auto"})

        migrate_global_config()

        stored = self.stored()
        self.assertEqual(9999, stored["port"])
        self.assertEqual("auto", stored["update_check"])

    def test_reports_what_was_cleaned(self) -> None:
        self.write({**config.DEFAULTS, "port": 9999})

        removed = migrate_global_config()

        self.assertIn("theme", removed)
        self.assertNotIn("port", removed)

    def test_is_idempotent(self) -> None:
        self.write({**config.DEFAULTS, "theme": "light"})

        migrate_global_config()
        self.assertEqual([], migrate_global_config())

    def test_missing_file_is_not_created(self) -> None:
        """Мигрировать нечего — и файла из воздуха не появляется."""
        self.assertEqual([], migrate_global_config())
        self.assertFalse(config.GLOBAL_CONFIG_FILE.exists())


class StartupTest(unittest.TestCase):
    def test_migration_runs_on_startup(self) -> None:
        """Чистка бесполезна, если её никто не зовёт."""
        text = APP.read_text(encoding="utf-8")
        startup = text.split("def _startup()", 1)[-1].split("\n@app.", 1)[0]

        self.assertIn("migrate_global_config", startup,
                      "миграция не вызывается при старте сервера")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
