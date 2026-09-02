"""Проект по рабочей папке: чужая папка tasks/ активным проектом не становится.

Запись автозагрузки не задавала рабочую папку, и Проводник запускал лаунчер из
`C:\\Windows\\System32`. Там существует `Tasks` — хранилище планировщика заданий
Windows, — и лаунчер, выводивший проект из рабочей папки, регистрировал его
активным. Следующий запуск падал ещё на старте наблюдателя: читать эту папку
обычному пользователю нельзя (TASK-233).

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import validator  # noqa: E402
from backend.fs_browse import looks_like_project  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"


def load_launcher():
    """Загрузить taskboard.py как модуль: он вне пакета и без зависимостей."""
    path = ROOT.parent / "taskboard.py"
    spec = importlib.util.spec_from_file_location("taskboard_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TmpTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def project(self, name: str = "проект") -> Path:
        """Папка, которую инструмент действительно разворачивал: с доской."""
        tasks = self.root / name / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "board.md").write_text("# Доска\n", encoding="utf-8")
        return tasks

    def foreign(self, name: str = "System32") -> Path:
        """Чужая папка с именем tasks: хранилище планировщика, домен волта,
        каталог исходников — их на диске сколько угодно."""
        tasks = self.root / name / "Tasks"
        tasks.mkdir(parents=True)
        return tasks


class LooksLikeProjectTest(TmpTest):
    """Признак проекта — доска или конфиг внутри папки задач, а не её имя."""

    def test_папка_с_доской_это_проект(self) -> None:
        self.assertTrue(looks_like_project(self.project()))

    def test_папка_с_конфигом_проекта_это_проект(self) -> None:
        """Доску могли снести, но проект от этого чужим не стал."""
        tasks = self.foreign("свой")
        (tasks / ".taskboard.json").write_text("{}", encoding="utf-8")
        self.assertTrue(looks_like_project(tasks))

    def test_голая_папка_tasks_проектом_не_считается(self) -> None:
        self.assertFalse(looks_like_project(self.foreign()))

    def test_несуществующая_папка_проектом_не_считается(self) -> None:
        self.assertFalse(looks_like_project(self.root / "нет-такой"))


class ResolveTasksDirTest(TmpTest):
    """Что лаунчер берёт активным проектом и почему отказывается."""

    def setUp(self) -> None:
        super().setUp()
        self.launcher = load_launcher()

    def resolve(self, explicit, cwd):
        return self.launcher.resolve_tasks_dir(explicit, cwd)

    def test_папка_проекта_под_рабочей_берётся(self) -> None:
        tasks = self.project()
        path, reason = self.resolve(None, tasks.parent)
        self.assertEqual(path, tasks)
        self.assertEqual(reason, "")

    def test_чужая_папка_tasks_проектом_не_становится(self) -> None:
        """Тот самый System32: папка есть, доски в ней нет."""
        foreign = self.foreign()
        path, reason = self.resolve(None, foreign.parent)
        self.assertEqual(path, foreign)
        self.assertEqual(reason, "not_project")

    def test_рабочая_папка_без_tasks(self) -> None:
        _, reason = self.resolve(None, self.root)
        self.assertEqual(reason, "missing")

    def test_явный_путь_не_проверяется_на_признак_проекта(self) -> None:
        """`--tasks-dir` назвал человек — спорить не с кем: развернуть
        структуру в пустую папку он вправе."""
        foreign = self.foreign()
        path, reason = self.resolve(str(foreign), self.root)
        self.assertEqual(path, foreign)
        self.assertEqual(reason, "")

    def test_явный_путь_к_несуществующей_папке_всё_равно_отказ(self) -> None:
        _, reason = self.resolve(str(self.root / "нет-такой"), self.root)
        self.assertEqual(reason, "missing")

    def test_рабочая_папка_считается_один_раз(self) -> None:
        """Второе место, выводящее проект из cwd, — второй такой же баг."""
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")
        self.assertEqual(text.count('cwd / "tasks"'), 1, text.count('cwd / "tasks"'))
        self.assertIn("resolve_tasks_dir(", text)


class HeadlessLaunchTest(TmpTest):
    """Запуск без консоли: автозапуск идёт через `pythonw`.

    У такого процесса нет ни stdout, ни stderr — `sys.stdout` равен `None`.
    `uvicorn.run` на этом падал: его конфиг логирования ссылается на
    `ext://sys.stdout`, и `dictConfig` на `None` бросает `ValueError`. Лаунчер
    доходил до регистрации проекта и умирал молча — сервер при входе в систему
    не поднимался вовсе (TASK-233).
    """

    def setUp(self) -> None:
        super().setUp()
        self.launcher = load_launcher()
        patch = mock.patch.object(self.launcher, "UPDATE_DIR", self.root / ".taskboard")
        patch.start()
        self.addCleanup(patch.stop)

    def test_без_потоков_заводится_файл_лога(self) -> None:
        # Ровно условия pythonw из Проводника: потоков нет
        with mock.patch.multiple(self.launcher.sys, stdout=None, stderr=None):
            self.launcher.ensure_log_stream(8765)
            stream = self.launcher.sys.stdout
            self.assertIsNotNone(stream, "писать по-прежнему некуда")
            self.assertIs(self.launcher.sys.stderr, stream,
                          "stderr остался пустым — падение uvicorn не запишется")
            stream.close()
        self.assertTrue(self.launcher.log_file(8765).is_file())

    def test_с_консолью_ничего_не_подменяется(self) -> None:
        """Обычный запуск из терминала пишет человеку, а не в файл."""
        before = self.launcher.sys.stdout
        self.launcher.ensure_log_stream(8765)
        self.assertIs(self.launcher.sys.stdout, before)
        self.assertFalse(self.launcher.log_file(8765).exists())

    def test_поток_заводится_до_старта_сервера(self) -> None:
        """Порядок решает: подмена после `uvicorn.run` бессмысленна."""
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")
        body = text[text.index("def main() -> None:"):]
        self.assertLess(body.index("ensure_log_stream("), body.index("uvicorn.run("),
                        "поток заводится позже старта сервера")


class UnreadableProjectTest(TmpTest):
    """Нечитаемая папка задач — отдельный случай, а не «нет доски»."""

    def test_читаемость_проверяется_по_настоящему(self) -> None:
        self.assertTrue(validator.readable(self.root))
        self.assertFalse(validator.readable(self.root / "нет-такой"))

    def test_отчёт_называет_папку_недоступной(self) -> None:
        tasks = self.foreign()
        with mock.patch.object(validator, "readable", return_value=False):
            report = validator.validate_project(tasks, {})
        self.assertEqual(report["structure"], "unreadable")
        self.assertFalse(report["ok"])
        self.assertTrue(any(str(tasks) in c for c in report["critical"]),
                        report["critical"])

    def test_читаемая_папка_без_доски_осталась_прежним_случаем(self) -> None:
        """Разворачивать структуру в свою пустую папку по-прежнему можно."""
        report = validator.validate_project(self.foreign(), {})
        self.assertEqual(report["structure"], "no_board")


class UnreadableProjectUiTest(unittest.TestCase):
    """Недоступному проекту нельзя предлагать разворачивание структуры."""

    def source(self) -> str:
        return (SRC / "App.jsx").read_text(encoding="utf-8")

    def test_у_недоступного_проекта_своё_окно(self) -> None:
        self.assertIn("unreadable", self.source(),
                      "UI не отличает недоступную папку от папки без доски")

    def test_предлагается_убрать_проект(self) -> None:
        self.assertIn("Убрать проект", self.source(),
                      "мусорный проект нечем убрать с доски")

    def test_разворачивание_структуры_туда_не_предлагается(self) -> None:
        """Кнопка scaffold на System32\\Tasks — предложение писать скиллы и
        доску в системную папку Windows."""
        text = self.source()
        block = text[text.index("setShowScaffold(true)") - 2000:
                     text.index("setShowScaffold(true)")]
        self.assertIn("unreadable", block,
                      "разворачивание структуры не исключено для недоступной папки")


if __name__ == "__main__":
    unittest.main()
