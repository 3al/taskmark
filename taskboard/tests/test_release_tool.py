"""Скрипт выпуска: арифметика версий, changelog, готовность к релизу.

Здесь проверяется то, что можно проверить без сети: подсчёт следующей версии,
разбор и сборка секций changelog, определение преград, чтение истории выпусков.
История читает git — для неё поднимается свой репозиторий во временной папке:
теги и тела коммитов нужны настоящие, а трогать репозиторий проекта нельзя.
Запись и публикация не тестируются: они за узким интерфейсом и необратимы.

Скрипт лежит в `tools/`, а не в `taskboard/`: он знает про VERSION и теги
этого репозитория и в поставку пользователям не идёт.
"""

import importlib.util
import json
import subprocess
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


class TestHistory(unittest.TestCase):
    """История выпусков: теги и тела релизных коммитов своего репозитория.

    Репозиторий поднимается во временной папке: история должна читаться из
    любого репозитория, а не только из того, где лежит скрипт.
    """

    def setUp(self):
        self.tool = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "release@example.com")
        self._git("config", "user.name", "Тест выпуска")
        self._git("config", "commit.gpgsign", "false")
        self._git("config", "tag.gpgsign", "false")

    def _git(self, *args):
        return subprocess.run(("git", *args), cwd=self.root, check=True,
                              capture_output=True, text=True,
                              encoding="utf-8").stdout.strip()

    def _release(self, version, tasks="", annotated=True):
        """Релизный коммит и тег на него — как их делает `--apply`."""
        (self.root / "VERSION").write_text(version + "\n", encoding="utf-8")
        self._git("add", "-A")
        body = "Задачи выпуска: " + tasks if tasks else "Выпуск без привязки к задачам."
        self._git("commit", "-m", f"Релиз {version}", "-m", body)
        tag = "v" + version
        if annotated:
            self._git("tag", "-a", tag, "-m", f"Taskmark {version}")
        else:
            self._git("tag", tag)
        return tag

    def test_без_тегов_история_пуста(self):
        # Пустой список, а не отказ: репозиторий без выпусков — нормальное состояние
        self.assertEqual(self.tool.history(self.root), [])

    def test_выпуски_идут_от_свежего_к_старому(self):
        self._release("1.0.0")
        self._release("1.1.0")
        versions = [entry["version"] for entry in self.tool.history(self.root)]
        self.assertEqual(versions, ["1.1.0", "1.0.0"])

    def test_состав_берётся_из_тела_релизного_коммита(self):
        self._release("1.0.0", tasks="TASK-089, TASK-090, TASK-091")
        entry = self.tool.history(self.root)[0]
        self.assertEqual(entry["tasks"], ["TASK-089", "TASK-090", "TASK-091"])
        self.assertEqual(entry["tag"], "v1.0.0")
        self.assertTrue(entry["commit"], "не указан коммит выпуска")

    def test_тело_без_состава_даёт_пустой_список(self):
        # Тег мог поставить человек руками — это не ошибка чтения истории
        self._release("1.0.0")
        self.assertEqual(self.tool.history(self.root)[0]["tasks"], [])

    def test_время_берётся_из_аннотации_тега(self):
        self._release("1.0.0")
        entry = self.tool.history(self.root)[0]
        expected = self._git("for-each-ref", "--format=%(taggerdate:iso-strict)",
                             "refs/tags/v1.0.0")
        self.assertTrue(entry["annotated"], "аннотированный тег помечен как обычный")
        self.assertEqual(entry["released_at"], expected)

    def test_неаннотированный_тег_берёт_дату_коммита_и_помечается(self):
        # `git tag` без -a времени не хранит — тогда время коммита, но честно
        self._release("1.0.0", annotated=False)
        entry = self.tool.history(self.root)[0]
        self.assertFalse(entry["annotated"], "у обычного тега нет времени выпуска")
        self.assertEqual(entry["released_at"],
                         self._git("log", "-1", "--format=%cI"))

    def test_чужие_теги_историей_не_считаются(self):
        self._release("1.0.0")
        self._git("tag", "-a", "hotfix", "-m", "не выпуск")
        self.assertEqual([e["tag"] for e in self.tool.history(self.root)], ["v1.0.0"])

    def test_ответ_сериализуется(self):
        self._release("1.0.0", tasks="TASK-001")
        json.dumps(self.tool.history(self.root), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
