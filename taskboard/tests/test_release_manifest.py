"""Согласованность релиза: VERSION ↔ release.json ↔ CHANGELOG.md.

Релиз объявляется четырьмя фактами в одном коммите: файл VERSION, манифест
release.json, верхняя секция CHANGELOG.md и git-тег. Без CI это ручной ритуал,
который забывается — поэтому три из четырёх сверяются тестом (про тег знает
только сам git, его здесь не трогаем: тесты не запускают подпроцессы).

Манифест дублирует заметки из CHANGELOG намеренно: его читают по сети, когда
репозитория у пользователя нет. Дубль без проверки разъедется, отсюда сверка.
"""

import json
import re
import unittest

from backend import version

ROOT = version.VERSION_FILE.resolve().parent.parent
MANIFEST = ROOT / "release.json"
CHANGELOG = ROOT / "CHANGELOG.md"

# Секция версии в CHANGELOG: «## [1.4.0] — 2026-08-01» до следующей такой же
_SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
                      re.S | re.M)


def _top_section() -> re.Match:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = _SECTION.search(text)
    assert match is not None, "в CHANGELOG.md нет ни одной секции вида «## [версия]»"
    return match


class TestManifest(unittest.TestCase):

    def setUp(self):
        self.assertTrue(MANIFEST.is_file(), f"нет манифеста: {MANIFEST}")
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_поля_на_месте(self):
        for key in ("version", "tag", "date", "notes"):
            with self.subTest(key=key):
                self.assertIn(key, self.manifest)
                self.assertTrue(str(self.manifest[key]).strip(), f"пустое поле {key}")

    def test_версия_совпадает_с_файлом_VERSION(self):
        self.assertEqual(self.manifest["version"], version.current())

    def test_тег_это_версия_с_префиксом_v(self):
        self.assertEqual(self.manifest["tag"], "v" + self.manifest["version"])

    def test_версия_разбирается(self):
        self.assertTrue(version.is_valid(self.manifest["version"]))

    def test_дата_в_формате_ггггммдд(self):
        self.assertRegex(self.manifest["date"], r"^\d{4}-\d{2}-\d{2}$")


class TestChangelog(unittest.TestCase):

    def test_верхняя_секция_это_текущая_версия(self):
        # Классический промах релиза: версию подняли, changelog забыли
        self.assertEqual(_top_section().group("version"), version.current())

    def test_заметки_манифеста_совпадают_с_секцией(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = _top_section().group("body").strip()
        self.assertEqual(manifest["notes"].strip(), expected,
                         "release.json и CHANGELOG.md разошлись — "
                         "манифест собирается из верхней секции changelog")

    def test_секция_не_пустая(self):
        self.assertTrue(_top_section().group("body").strip())


class TestLicense(unittest.TestCase):
    """Файл лицензии — часть поставки: без него условия использования не заданы."""

    def test_лицензия_на_месте(self):
        license_file = ROOT / "LICENSE"
        self.assertTrue(license_file.is_file(), "нет файла LICENSE в корне")
        text = license_file.read_text(encoding="utf-8")
        self.assertIn("PolyForm Small Business License 1.0.0", text)
        self.assertIn("Required Notice:", text)


if __name__ == "__main__":
    unittest.main()
