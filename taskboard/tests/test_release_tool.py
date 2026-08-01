"""Скрипт выпуска: арифметика версий, changelog, готовность к релизу.

Здесь проверяется только то, что можно проверить без git и сети: подсчёт
следующей версии, разбор и сборка секций changelog, определение преград.
Сами git-операции и публикация — не тестируются: они за узким интерфейсом,
и подниматься ради них настоящему репозиторию незачем.

Скрипт лежит в `tools/`, а не в `taskboard/`: он знает про VERSION и теги
этого репозитория и в поставку пользователям не идёт.
"""

import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from backend import version

ROOT = version.VERSION_FILE.resolve().parent.parent
TOOL = ROOT / "tools" / "release.py"


def _load():
    """Загрузить tools/release.py как модуль: пакетом он не является."""
    spec = importlib.util.spec_from_file_location("release_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestToolExists(unittest.TestCase):

    def test_скрипт_на_месте(self):
        self.assertTrue(TOOL.is_file(), f"нет скрипта выпуска: {TOOL}")


class TestNextVersion(unittest.TestCase):

    def setUp(self):
        self.tool = _load()

    def test_разряды(self):
        cases = [
            ("1.0.0", "patch", "1.0.1"),
            ("1.0.0", "minor", "1.1.0"),
            ("1.0.0", "major", "2.0.0"),
            ("1.4.7", "minor", "1.5.0"),
            ("1.4.7", "major", "2.0.0"),
        ]
        for current, bump, expected in cases:
            with self.subTest(current=current, bump=bump):
                self.assertEqual(self.tool.next_version(current, bump), expected)

    def test_младшие_разряды_обнуляются(self):
        # Классическая ошибка руками: подняли minor, забыли обнулить patch
        self.assertEqual(self.tool.next_version("2.3.9", "minor"), "2.4.0")
        self.assertEqual(self.tool.next_version("2.3.9", "major"), "3.0.0")

    def test_неизвестный_разряд_это_ошибка(self):
        with self.assertRaises(ValueError):
            self.tool.next_version("1.0.0", "huge")

    def test_непонятная_версия_это_ошибка(self):
        with self.assertRaises(ValueError):
            self.tool.next_version("не версия", "minor")


class TestChangelog(unittest.TestCase):

    def setUp(self):
        self.tool = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "CHANGELOG.md"

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_верхняя_секция_читается(self):
        self._write("# Changelog\n\n## [1.2.0] — 2026-08-01\n\n### Добавлено\n\n- раз\n\n"
                    "## [1.1.0] — 2026-07-01\n\n- два\n")
        section = self.tool.top_section(self.path)
        self.assertEqual(section["version"], "1.2.0")
        self.assertIn("- раз", section["body"])
        self.assertNotIn("два", section["body"], "тело соседней версии просочилось")

    def test_без_секций_это_ошибка(self):
        self._write("# Changelog\n\nпока пусто\n")
        with self.assertRaises(ValueError):
            self.tool.top_section(self.path)

    def test_новая_секция_встаёт_сверху(self):
        self._write("# Changelog\n\nвступление\n\n## [1.0.0] — 2026-07-01\n\n- старое\n")
        self.tool.insert_section(self.path, "1.1.0", "2026-08-01", "### Добавлено\n\n- новое")
        text = self.path.read_text(encoding="utf-8")
        self.assertLess(text.index("[1.1.0]"), text.index("[1.0.0]"),
                        "новая версия должна оказаться выше старой")
        self.assertIn("вступление", text, "шапка файла не должна теряться")
        self.assertEqual(self.tool.top_section(self.path)["version"], "1.1.0")

    def test_манифест_собирается_из_секции(self):
        self._write("# Changelog\n\n## [1.1.0] — 2026-08-01\n\n### Добавлено\n\n- новое\n")
        manifest = self.tool.build_manifest(self.path, "1.1.0")
        self.assertEqual(manifest["version"], "1.1.0")
        self.assertEqual(manifest["tag"], "v1.1.0")
        # Манифест датируется днём сборки, а не датой секции: захардкоженная
        # дата совпадала с сегодняшней ровно один день
        self.assertEqual(manifest["date"], date.today().isoformat())
        self.assertIn("- новое", manifest["notes"])

    def test_манифест_требует_совпадения_версий(self):
        # Защита от «подняли VERSION, changelog забыли»
        self._write("# Changelog\n\n## [1.0.0] — 2026-08-01\n\n- старое\n")
        with self.assertRaises(ValueError):
            self.tool.build_manifest(self.path, "1.1.0")


class TestBlockers(unittest.TestCase):
    """Преграды собираются списком, а не падают на первой."""

    def setUp(self):
        self.tool = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "dist").mkdir()

    def _touch(self, path, mtime):
        p = self.root / path
        p.write_text("x", encoding="utf-8")
        import os
        os.utime(p, (mtime, mtime))
        return p

    def test_устаревшая_сборка_это_преграда(self):
        self._touch("dist/index.html", 1000)
        self._touch("src/App.jsx", 2000)
        self.assertFalse(self.tool.dist_is_fresh(self.root / "src", self.root / "dist"))

    def test_свежая_сборка_преградой_не_является(self):
        self._touch("src/App.jsx", 1000)
        self._touch("dist/index.html", 2000)
        self.assertTrue(self.tool.dist_is_fresh(self.root / "src", self.root / "dist"))

    def test_нет_сборки_вовсе(self):
        self._touch("src/App.jsx", 1000)
        self.assertFalse(self.tool.dist_is_fresh(self.root / "src", self.root / "dist"))


class TestCheckOutput(unittest.TestCase):
    """`--check` отдаёт json и ничего не меняет."""

    def setUp(self):
        self.tool = _load()

    def test_согласованный_changelog_преградой_не_является(self):
        # Между выпусками верхняя секция описывает установленную версию — это норма,
        # и списывать её в преграды значит пугать человека штатным состоянием
        self.assertFalse(
            [b for b in self.tool.blockers() if "разошлись" in b],
            "changelog и VERSION согласованы, преграды быть не должно")

    def test_форма_ответа(self):
        result = self.tool.check(bump="minor")
        self.assertIn("ok", result)
        self.assertEqual(result["current"], version.current())
        self.assertEqual(result["next"], self.tool.next_version(version.current(), "minor"))
        self.assertIsInstance(result["blockers"], list)

    def test_без_разряда_следующей_версии_нет(self):
        result = self.tool.check()
        self.assertIsNone(result.get("next"))

    def test_ответ_сериализуется(self):
        json.dumps(self.tool.check(bump="patch"), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
