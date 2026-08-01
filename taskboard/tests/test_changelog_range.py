"""Что изменилось за пропущенные версии (TASK-099).

Обновившись через несколько выпусков, человек видел только «Обновлено до
версии X»: манифест несёт заметки одной версии, а локальный CHANGELOG.md
интерфейс не открывал ни разу. Пропущенное приходилось искать в репозитории.

Разбор отрезка — на бэкенде: фронт markdown не режет.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.changelog import sections, since  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """# Changelog

Все заметные изменения проекта.

## [1.3.0] — 2026-08-05

### Добавлено

- третья

## [1.2.0] — 2026-08-02

### Добавлено

- вторая

## [1.1.0] — 2026-08-01

- первая
"""


class SectionsTest(unittest.TestCase):
    def test_all_versions_in_order(self) -> None:
        found = [s["version"] for s in sections(SAMPLE)]

        self.assertEqual(["1.3.0", "1.2.0", "1.1.0"], found)

    def test_body_belongs_to_its_version(self) -> None:
        top = sections(SAMPLE)[0]

        self.assertEqual("2026-08-05", top["date"])
        self.assertIn("третья", top["body"])
        self.assertNotIn("вторая", top["body"], "тело соседней версии просочилось")

    def test_intro_is_not_a_version(self) -> None:
        self.assertNotIn("Changelog", [s["version"] for s in sections(SAMPLE)])

    def test_garbage_is_skipped(self) -> None:
        """Непонятный заголовок просто не попадает в выборку."""
        text = SAMPLE + "\n## [не-версия] — вчера\n\n- мусор\n"

        self.assertEqual(["1.3.0", "1.2.0", "1.1.0"],
                         [s["version"] for s in sections(text)])

    def test_empty_file(self) -> None:
        self.assertEqual([], sections("# Changelog\n\nпока пусто\n"))


class SinceTest(unittest.TestCase):
    """Отрезок: строго новее прежней версии, включая текущую."""

    def test_two_missed_releases(self) -> None:
        got = [s["version"] for s in since(SAMPLE, "1.1.0")]

        self.assertEqual(["1.3.0", "1.2.0"], got)

    def test_previous_version_itself_is_excluded(self) -> None:
        """Её пользователь уже видел — он на ней сидел."""
        self.assertNotIn("1.1.0", [s["version"] for s in since(SAMPLE, "1.1.0")])

    def test_one_step(self) -> None:
        self.assertEqual(["1.3.0"], [s["version"] for s in since(SAMPLE, "1.2.0")])

    def test_nothing_newer(self) -> None:
        self.assertEqual([], since(SAMPLE, "1.3.0"))

    def test_unknown_previous_version_returns_everything(self) -> None:
        """Не с чем сравнивать — лучше показать всё, чем ничего."""
        self.assertEqual(["1.3.0", "1.2.0", "1.1.0"],
                         [s["version"] for s in since(SAMPLE, "")])


class ManyMissedVersionsTest(unittest.TestCase):
    """Забыл про программу на год — не показываем стену текста."""

    def big(self, count: int = 20) -> str:
        out = ["# Changelog", ""]
        for i in range(count, 0, -1):
            out += [f"## [1.{i}.0] — 2026-08-01", "", f"- изменение {i}", ""]
        return "\n".join(out)

    def test_limit_keeps_only_the_freshest(self) -> None:
        got = since(self.big(), "1.0.0", limit=5)

        self.assertEqual(5, len(got))
        self.assertEqual(["1.20.0", "1.19.0", "1.18.0", "1.17.0", "1.16.0"],
                         [s["version"] for s in got])

    def test_zero_limit_means_everything(self) -> None:
        self.assertEqual(20, len(since(self.big(), "1.0.0", limit=0)))

    def test_limit_larger_than_available(self) -> None:
        self.assertEqual(3, len(since(SAMPLE, "", limit=50)))

    def test_api_reports_how_many_were_missed(self) -> None:
        """Урезали — скажи сколько всего, иначе человек не узнает о пропущенном."""
        text = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn('"total": len(found)', text)

    def test_modal_offers_the_rest(self) -> None:
        text = (ROOT / "frontend" / "src" / "components"
                / "UpdateModal.jsx").read_text(encoding="utf-8")

        self.assertIn("NEWS_SHOWN", text)
        self.assertIn("Показать все", text)


class WiringTest(unittest.TestCase):
    """Отрезок доезжает до окна обновления."""

    def test_api_accepts_since(self) -> None:
        text = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn("def api_changelog(since_version", text)
        self.assertIn("changelog.since(", text)

    def test_apply_records_the_version_we_leave(self) -> None:
        """Без точки отсчёта диапазон не построить."""
        text = (ROOT / "backend" / "updater.py").read_text(encoding="utf-8")

        self.assertIn('"from"', text)

    def test_launcher_returns_it_in_result(self) -> None:
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")

        self.assertIn('"from"', text)

    def test_modal_shows_the_range(self) -> None:
        text = (ROOT / "frontend" / "src" / "components"
                / "UpdateModal.jsx").read_text(encoding="utf-8")

        self.assertIn("api.changelog(done.from", text,
                      "окно не спрашивает отрезок от прежней версии")
        self.assertIn("news.sections.map(", text, "секции не выводятся")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
