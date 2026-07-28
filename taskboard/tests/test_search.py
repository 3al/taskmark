"""Тесты поиска по доске (TASK-008).

Поиск идёт по файлам задач, а не по тому, что видно на карточке: пользователь
ищет «где я это писал», а написано оно обычно в описании, а не в заголовке.
Запрос — живой ввод человека, поэтому он литерал, а не регулярка: `C++`,
`api()` и `.md` не должны ломать поиск.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.search import search_tasks  # noqa: E402

TASK = """---
id: {id}
title: {title}
status: {status}
---

## Описание

{body}
"""


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name) / "tasks"
        self.tasks_dir.mkdir(parents=True)
        self._task("TASK-001", "Поиск-фильтр по доске", "Живой фильтр без кнопок")
        self._task("TASK-002", "Падение вотчера", "Watchdog умирает молча на macOS")
        self._task("TASK-003", "Сборка фронтенда", "Нужен npm run build и коммит dist")

    def _task(self, task_id: str, title: str, body: str, status: str = "todo") -> Path:
        path = self.tasks_dir / f"{task_id}-{title.lower().replace(' ', '-')}.md"
        path.write_text(TASK.format(id=task_id, title=title, body=body, status=status),
                        encoding="utf-8")
        return path

    def _ids(self, query: str) -> list[str]:
        return [item["id"] for item in search_tasks(self.tasks_dir, query)]

    # --- Что находится ---

    def test_finds_by_title(self) -> None:
        self.assertEqual(self._ids("фильтр"), ["TASK-001"])

    def test_finds_by_body(self) -> None:
        """Главное: искать по содержанию, а не только по видимому заголовку."""
        self.assertEqual(self._ids("watchdog"), ["TASK-002"])

    def test_finds_by_id(self) -> None:
        self.assertEqual(self._ids("TASK-003"), ["TASK-003"])

    def test_case_insensitive(self) -> None:
        self.assertEqual(self._ids("ВОТЧЕРА"), ["TASK-002"])

    def test_no_matches(self) -> None:
        self.assertEqual(self._ids("нетакогослова"), [])

    def test_empty_query_matches_nothing(self) -> None:
        """Пустой запрос — не «все задачи», а выключенный фильтр."""
        self.assertEqual(self._ids(""), [])
        self.assertEqual(self._ids("   "), [])

    def test_query_is_literal_not_regex(self) -> None:
        """Спецсимволы регулярок — обычный текст: иначе поиск падает на C++ и (api)."""
        self._task("TASK-004", "Сноска (api)", "Вызов api() и путь .md")
        self.assertEqual(self._ids("api()"), ["TASK-004"])
        self.assertEqual(self._ids("(api)"), ["TASK-004"])
        self.assertEqual(self._ids(".md"), ["TASK-004"])

    # --- Что возвращается ---

    def test_result_carries_title_and_counts(self) -> None:
        self._task("TASK-005", "Логи", "лог, ещё лог и снова лог")
        item = next(i for i in search_tasks(self.tasks_dir, "лог") if i["id"] == "TASK-005")
        self.assertEqual(item["title"], "Логи")
        self.assertGreaterEqual(item["hits"], 3, "число попаданий не посчитано")

    def test_excerpt_shows_context(self) -> None:
        """Фрагмент нужен, чтобы понять, почему задача попала в выдачу."""
        item = next(i for i in search_tasks(self.tasks_dir, "молча") if i["id"] == "TASK-002")
        self.assertIn("молча", item["excerpt"].lower())
        self.assertLessEqual(len(item["excerpt"]), 200, "фрагмент разросся в целый абзац")

    def test_title_match_is_flagged(self) -> None:
        """Совпадение в заголовке ценнее совпадения в теле — фронт ставит такие выше."""
        by_title = next(i for i in search_tasks(self.tasks_dir, "вотчера") if i["id"] == "TASK-002")
        self.assertTrue(by_title["in_title"])
        by_body = next(i for i in search_tasks(self.tasks_dir, "watchdog") if i["id"] == "TASK-002")
        self.assertFalse(by_body["in_title"])

    def test_id_comes_from_filename_not_frontmatter(self) -> None:
        """Идентичность задаёт имя файла: `id:` во frontmatter может отстать.

        В живом проекте нашёлся TASK-000 с `id: TASK-120` — выдача указывала
        бы на задачу, которой нет ни на доске, ни на диске.
        """
        path = self._task("TASK-006", "Разошедшийся id", "уникальноеслово")
        path.write_text(path.read_text(encoding="utf-8").replace("id: TASK-006", "id: TASK-120"),
                        encoding="utf-8")
        self.assertEqual(self._ids("уникальноеслово"), ["TASK-006"])

    def test_frontmatter_not_searched(self) -> None:
        """Служебные поля не должны ловить запрос: `todo` есть в каждой задаче."""
        self.assertEqual(self._ids("todo"), [])


FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


class SearchUiTest(unittest.TestCase):
    """Фронт проверяем по исходнику: JS-раннера в проекте нет (как в test_pipeline_editor)."""

    def test_header_has_live_filter(self) -> None:
        src = (FRONTEND / "components" / "Header.jsx").read_text(encoding="utf-8")
        self.assertIn("onQuery", src, "в шапке нет поля поиска")
        self.assertIn("onChange", src, "фильтр не живой — ввод ничего не запускает")

    def test_search_is_debounced(self) -> None:
        """Запрос на каждую букву — лишняя нагрузка на файловую систему."""
        src = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("setTimeout", src, "поиск шлётся без паузы после ввода")
        self.assertIn("clearTimeout", src, "таймер предыдущего ввода не снимается")

    def test_board_filtered_by_result(self) -> None:
        src = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("visibleColumns", src, "доска не фильтруется по результату поиска")

    def test_modal_highlights_matches(self) -> None:
        src = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("rehypeHighlight", src, "в открытой задаче совпадения не подсвечиваются")

    def test_highlight_skips_code(self) -> None:
        """Подсветка внутри <pre>/<code> ломает моноширинную вёрстку."""
        src = (FRONTEND / "highlight.jsx").read_text(encoding="utf-8")
        self.assertIn("'code'", src)
        self.assertIn("'pre'", src)


if __name__ == "__main__":
    unittest.main()
