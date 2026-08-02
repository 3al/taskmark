"""Переименование системных артефактов убрано из настроек (TASK-053).

Настройка обещала то, чего не делала. Данные за конфигом шли: файлы
переименовывались миграцией, сверка с шаблоном искала файл по имени из
конфига, скрипты читали конфиг сами. Но **тексты, по которым работает
агент**, остались с зашитыми именами: `set_status.py` упомянут 33 раза
в скиллах и 11 в `rules_section.md`, `board.md` — 18 и 7, `create_task.py`
— 3, `logs/` — 2. `render_rules` подставляет статусы и разделы, имена
файлов — нет.

Итог для того, кто переименовал: скиллы зовут несуществующий файл, правила
в CLAUDE.md врут, а `create_task.py` перестаёт проставлять обратную ссылку
`blocks` — он импортирует `set_status` по имени, и ошибка глохнет в WARN.

Решение: имена перестают быть настройкой и остаются константами поставки.
Разовая миграция возвращает уже переименованные артефакты к дефолтам.

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

from backend import config  # noqa: E402
from backend.migrations import retire_artifact_names  # noqa: E402

# Ключи, переставшие быть настройкой
RETIRED = ("board_file", "create_script", "status_script",
           "logs_dir", "queue_section", "queued_status", "lost_section")


class ProjectCase(unittest.TestCase):
    """Проект во временной папке: миграция трогает файлы на диске."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)

    def write_cfg(self, data: dict) -> None:
        config.project_config_path(self.tasks).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def stored(self) -> dict:
        path = config.project_config_path(self.tasks)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class RetiredKeysTest(unittest.TestCase):
    """Ключи больше не принимаются ни на одном слое конфига."""

    def test_project_keys_have_no_artifact_names(self) -> None:
        for key in RETIRED:
            self.assertNotIn(key, config.PROJECT_KEYS,
                             f"{key} больше не настройка проекта")

    def test_api_does_not_accept_artifact_names(self) -> None:
        """Эндпоинт настроек фильтрует их вместе с прочим мусором."""
        source = (Path(__file__).resolve().parent.parent
                  / "backend" / "app.py").read_text(encoding="utf-8")
        start = source.index("def api_save_config")
        allowed = source[start:source.index("updates = {", start)]
        for key in RETIRED:
            self.assertNotIn(f'"{key}"', allowed,
                             f"{key} не должен быть в списке разрешённых ключей")

    def test_release_script_survives(self) -> None:
        """Свой скрипт выпуска — не переименование, а точка расширения."""
        self.assertIn("release_script", config.PROJECT_KEYS)


class RetireMigrationTest(ProjectCase):
    """Разовый возврат переименованных артефактов к именам поставки."""

    def test_renamed_files_come_back(self) -> None:
        (self.tasks / "доска.md").write_text("# Board\n", encoding="utf-8")
        (self.tasks / "статус.py").write_text("# script\n", encoding="utf-8")
        (self.tasks / "журналы").mkdir()
        self.write_cfg({"board_file": "доска.md", "status_script": "статус.py",
                        "logs_dir": "журналы"})

        actions = retire_artifact_names(self.tasks)

        self.assertTrue((self.tasks / "board.md").is_file())
        self.assertTrue((self.tasks / "set_status.py").is_file())
        self.assertTrue((self.tasks / "logs").is_dir())
        self.assertFalse((self.tasks / "доска.md").exists())
        self.assertTrue(actions, "о переименовании нужно сказать пользователю")

    def test_keys_are_removed_from_config(self) -> None:
        (self.tasks / "доска.md").write_text("# Board\n", encoding="utf-8")
        self.write_cfg({"board_file": "доска.md", "pipeline": ["backlog", "done"]})

        retire_artifact_names(self.tasks)

        stored = self.stored()
        self.assertNotIn("board_file", stored)
        self.assertEqual(stored.get("pipeline"), ["backlog", "done"],
                         "чужие ключи миграция не трогает")

    def test_default_names_are_left_alone(self) -> None:
        """Ключ со значением поставки — не переименование, а копия дефолта."""
        (self.tasks / "board.md").write_text("# Board\n", encoding="utf-8")
        self.write_cfg({"board_file": "board.md"})

        actions = retire_artifact_names(self.tasks)

        self.assertEqual(actions, [], "переименовывать нечего")
        self.assertNotIn("board_file", self.stored(), "ключ всё равно лишний")

    def test_clean_project_is_untouched(self) -> None:
        (self.tasks / "board.md").write_text("# Board\n", encoding="utf-8")
        self.assertEqual(retire_artifact_names(self.tasks), [])

    def test_missing_file_does_not_break(self) -> None:
        """Конфиг ссылается на файл, которого нет: ключ чистим, не падаем."""
        self.write_cfg({"board_file": "доска.md"})

        actions = retire_artifact_names(self.tasks)

        self.assertEqual(actions, [])
        self.assertNotIn("board_file", self.stored())

    def test_occupied_target_is_not_overwritten(self) -> None:
        """Файл с именем поставки уже есть — чужую доску не затираем."""
        (self.tasks / "board.md").write_text("# настоящая\n", encoding="utf-8")
        (self.tasks / "доска.md").write_text("# другая\n", encoding="utf-8")
        self.write_cfg({"board_file": "доска.md"})

        retire_artifact_names(self.tasks)

        self.assertEqual((self.tasks / "board.md").read_text(encoding="utf-8"),
                         "# настоящая\n")
        self.assertTrue((self.tasks / "доска.md").exists(),
                        "переименование не состоялось — файл остаётся на месте")


if __name__ == "__main__":
    unittest.main()
