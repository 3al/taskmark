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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.search import parse_query, search_tasks  # noqa: E402

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


EPIC_TASK = """---
id: {id}
title: {title}
epic: {epic}
status: todo
---

## Описание

{body}
"""


class EpicFilterTest(unittest.TestCase):
    """Отбор по эпику токеном `epic:` в строке поиска.

    Эпик живёт во frontmatter, который текстовый поиск не видит, поэтому токен
    разбирается как фильтр по полю, а остаток запроса ищется текстом.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name) / "tasks"
        self.tasks_dir.mkdir(parents=True)
        self._task("TASK-001", "Сценарии до кода", "E007-SPEC", "про сценарии")
        self._task("TASK-002", "Живая спека", "E007-SPEC", "вливание спеки")
        self._task("TASK-003", "Инвентаризация", "E056-18500", "про сценарии склада")
        self._task("TASK-004", "Без эпика", "~", "сценарии тоже есть")

    def _task(self, task_id: str, title: str, epic: str, body: str) -> None:
        path = self.tasks_dir / f"{task_id}-x.md"
        path.write_text(EPIC_TASK.format(id=task_id, title=title, epic=epic, body=body),
                        encoding="utf-8")

    def _ids(self, query: str) -> list[str]:
        return sorted(item["id"] for item in search_tasks(self.tasks_dir, query))

    def test_epic_token_keeps_only_epic_tasks(self) -> None:
        self.assertEqual(self._ids("epic:E007-SPEC"), ["TASK-001", "TASK-002"])

    def test_russian_prefix_and_case(self) -> None:
        self.assertEqual(self._ids("эпик:e007-spec"), ["TASK-001", "TASK-002"])
        self.assertEqual(self._ids("EPIC:E007-spec"), ["TASK-001", "TASK-002"])

    def test_several_epics_combine_by_or(self) -> None:
        both = ["TASK-001", "TASK-002", "TASK-003"]
        self.assertEqual(self._ids("epic:E007-SPEC,E056-18500"), both)
        self.assertEqual(self._ids("epic:E007-SPEC epic:E056-18500"), both)

    def test_epic_with_text_combine_by_and(self) -> None:
        self.assertEqual(self._ids("epic:E007-SPEC сценари"), ["TASK-001"])
        self.assertEqual(self._ids("сценари epic:E056-18500"), ["TASK-003"])

    def test_unknown_epic_gives_empty_result(self) -> None:
        self.assertEqual(self._ids("epic:NOPE-1"), [])

    def test_word_without_colon_is_text(self) -> None:
        """`epic` без двоеточия — обычное слово, а не отбор и не поиск по frontmatter."""
        self.assertEqual(self._ids("epic"), [])
        self.assertEqual(self._ids("todo"), [])

    def test_unknown_field_stays_text(self) -> None:
        """Двоеточие в тексте (`C:\\путь`, `http://`) не превращается в фильтр."""
        self._task("TASK-005", "Путь", "~", "лежит в C:\\temp и на http://host")
        self.assertEqual(self._ids("C:\\temp"), ["TASK-005"])
        self.assertEqual(self._ids("http://host"), ["TASK-005"])


class ParseQueryTest(unittest.TestCase):
    """Разбор запроса: что ушло в отбор по полям, что осталось текстом."""

    def test_splits_fields_and_text(self) -> None:
        parsed = parse_query("  epic:A-1,b-2 сценари  эпик:C-3 ")
        self.assertEqual(parsed["filters"], {"epic": ["a-1", "b-2", "c-3"]})
        self.assertEqual(parsed["text"], "сценари")

    def test_text_keeps_inner_spacing(self) -> None:
        """Запрос — литерал: пробелы внутри текста не схлопываются."""
        self.assertEqual(parse_query("run  build")["text"], "run  build")

    def test_field_without_value_is_dropped(self) -> None:
        """`epic:` в процессе набора не отбирает и не ищется текстом."""
        parsed = parse_query("epic:")
        self.assertEqual(parsed, {"text": "", "filters": {}})

    def test_api_reports_text_for_highlight(self) -> None:
        """Подсветке нужен остаток запроса, а не токен `epic:…`.

        `active` отличает «отбор включён и ничего не нашёл» от «фильтра нет»:
        запрос из одного `epic:` в процессе набора доску не гасит.
        """
        from backend import app as app_module

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(app_module, "_ctx", return_value=(Path(tmp), {})):
                typed = app_module.api_search(q="epic:NOPE-1 сценари")
                half = app_module.api_search(q="epic:")

        self.assertEqual("сценари", typed["text"])
        self.assertTrue(typed["active"])
        self.assertEqual([], typed["items"])
        self.assertFalse(half["active"])


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

    def test_epic_modal_shows_epic_on_board(self) -> None:
        """Из окна эпика — на доску тем же запросом, что набрал бы человек."""
        app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
        modal = (FRONTEND / "components" / "EpicModal.jsx").read_text(encoding="utf-8")
        self.assertIn("onShowOnBoard", modal, "в окне эпика нет перехода на доску")
        self.assertIn("setQuery(`epic:${key}`)", app, "переход не подставляет запрос epic:")

    def test_highlight_uses_text_without_field_tokens(self) -> None:
        """Подсвечивается остаток запроса от сервера, а не сырая строка с `epic:`."""
        app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("query={searchText}", app)
        self.assertNotIn("query={query}\n                    matches", app)

    def test_highlight_skips_code(self) -> None:
        """Подсветка внутри <pre>/<code> ломает моноширинную вёрстку."""
        src = (FRONTEND / "highlight.jsx").read_text(encoding="utf-8")
        self.assertIn("'code'", src)
        self.assertIn("'pre'", src)


if __name__ == "__main__":
    unittest.main()
