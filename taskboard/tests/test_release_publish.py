"""Публикация выпуска: аннотация тега и GitHub Release.

Ни git, ни `gh` здесь не запускаются: сборка команд вынесена в чистые функции,
а наличие `gh` подменяется. Проверяется то, на чём это уже ломалось вживую:

- без `--cleanup=verbatim` git вырезает строки, начинающиеся с `#`, считая их
  комментариями, — заголовки групп changelog исчезают молча, а список остаётся;
- без GitHub Release значок Latest остаётся на прошлой версии, и человек со
  страницы релизов скачивает устаревший архив.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import version

TOOL = version.VERSION_FILE.resolve().parent.parent / "tools" / "release.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTagArgs(unittest.TestCase):

    def setUp(self):
        self.tool = _load()
        self.notes = Path("/tmp/notes.md")
        self.args = self.tool.tag_args("v1.2.0", self.notes)

    def test_тег_аннотированный(self):
        self.assertIn("-a", self.args)
        self.assertIn("v1.2.0", self.args)

    def test_заметки_передаются_файлом(self):
        # -m не годится: заметки многострочные, с разметкой
        self.assertIn("-F", self.args)
        # путь сравниваем как строку платформы, а не как POSIX-литерал
        self.assertIn(str(self.notes), [str(a) for a in self.args])

    def test_разметка_не_вырезается(self):
        # Без этого флага «### Добавлено» пропадает как комментарий
        self.assertIn("--cleanup=verbatim", self.args,
                      "git съест заголовки групп changelog")


class TestReleaseArgs(unittest.TestCase):

    def setUp(self):
        self.tool = _load()
        self.args = self.tool.release_args("v1.2.0", "Taskmark 1.2.0", Path("/tmp/n.md"))

    def test_создаётся_для_существующего_тега(self):
        self.assertEqual(self.args[:3], ("gh", "release", "create"))
        self.assertIn("v1.2.0", self.args)

    def test_заголовок_и_текст(self):
        self.assertIn("--title", self.args)
        self.assertIn("Taskmark 1.2.0", self.args)
        self.assertIn("--notes-file", self.args)


class TestGithubRelease(unittest.TestCase):
    """Отсутствие gh — не провал выпуска, но и не молчание."""

    def setUp(self):
        self.tool = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.notes = Path(self.tmp.name) / "notes.md"
        self.notes.write_text("### Добавлено\n\n- раз\n", encoding="utf-8")

    def test_без_gh_внятный_отказ_без_исключения(self):
        with mock.patch.object(self.tool.shutil, "which", return_value=None):
            result = self.tool.create_github_release("v1.2.0", "Taskmark 1.2.0", self.notes)
        self.assertFalse(result["ok"])
        self.assertIn("gh", result["reason"].lower())

    def test_с_gh_команда_выполняется(self):
        with mock.patch.object(self.tool.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(self.tool.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            result = self.tool.create_github_release("v1.2.0", "Taskmark 1.2.0", self.notes)
        self.assertTrue(result["ok"], result)
        called = [str(a) for a in run.call_args[0][0]]
        self.assertEqual(called[:3], ["gh", "release", "create"])

    def test_ошибка_gh_не_поднимает_исключение(self):
        import subprocess as sp
        with mock.patch.object(self.tool.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(self.tool.subprocess, "run",
                               side_effect=sp.CalledProcessError(1, "gh", stderr="нет прав")):
            result = self.tool.create_github_release("v1.2.0", "Taskmark 1.2.0", self.notes)
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"])


class TestPublishResult(unittest.TestCase):
    """Витрина не удалась — выпуск всё равно состоялся."""

    def setUp(self):
        self.tool = _load()

    def test_пуш_прошёл_release_нет(self):
        with mock.patch.object(self.tool, "_git", return_value=""), \
             mock.patch.object(self.tool, "create_github_release",
                               return_value={"ok": False, "reason": "gh не установлен"}):
            result = self.tool.publish()
        self.assertTrue(result["ok"], "провал витрины не отменяет выпуск")
        self.assertTrue(result["pushed"])
        self.assertFalse(result["release"]["ok"])
        self.assertIn("gh", result["release"]["reason"])

    def test_release_создан(self):
        with mock.patch.object(self.tool, "_git", return_value=""), \
             mock.patch.object(self.tool, "create_github_release",
                               return_value={"ok": True}):
            result = self.tool.publish()
        self.assertTrue(result["ok"])
        self.assertTrue(result["release"]["ok"])


if __name__ == "__main__":
    unittest.main()
