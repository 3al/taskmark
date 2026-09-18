# -*- coding: utf-8 -*-
"""Тесты уведомлений от агента (TASK-276).

Агент живёт в терминале, а человек, отойдя от экрана, держит открытой доску:
единственный способ сказать ему «ход за вами» — всплывашка. Служба уведомлений
для этого уже есть, не хватало входа снаружи процесса: `POST /api/notify` и
развёрнутый в проект `tasks/notify.py`, который знает про порт и реестр
проектов, а агент — нет.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from backend import config as config_mod  # noqa: E402
from backend import notices  # noqa: E402
from backend.app import CAPABILITIES, NotifyIn, api_notify  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import environment_issues, scaffold_project  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
NOTIFY_TEMPLATE = TEMPLATES / "tasks" / "notify.py"
AGENTIC = TEMPLATES / "agentic"


def _load_script():
    """Загрузить шаблонный notify.py как модуль: он автономен, импортом и проверяем."""
    spec = importlib.util.spec_from_file_location("_notify_template", NOTIFY_TEMPLATE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class AgentNoticeKindTest(unittest.TestCase):
    """Вид `agent` — запись реестра, а не набор полей на месте вызова."""

    def test_реестр_знает_вид_агента(self):
        self.assertIn("agent", notices.NOTICES)

    def test_у_вида_есть_имя_источника_для_настроек(self):
        """Выключатель берётся из реестра — второй список разошёлся бы молча."""
        kinds = [src["kind"] for src in notices.sources_state({})]

        self.assertIn("agent", kinds)

    def test_снаружи_шлют_только_вид_агента(self):
        """Голосом проверки обновлений посторонний процесс говорить не должен."""
        self.assertTrue(notices.external("agent"))
        self.assertFalse(notices.external("update"))
        self.assertFalse(notices.external("task_from_chat"))

    def test_неизвестный_вид_снаружи_не_пускают(self):
        self.assertFalse(notices.external("чего-то-новенькое"))

    def test_показ_знает_про_ждущий_вид(self):
        src = ROOT / "frontend" / "src" / "components" / "Notices.jsx"
        text = src.read_text(encoding="utf-8")

        self.assertIn("notice.sticky", text)

    def test_агент_выбирает_тон_события(self):
        """Одним видом он говорит и «готово», и «всё встало»: тон у события."""
        self.assertTrue(notices.level_allowed("agent"))
        self.assertEqual(notices.SUCCESS,
                         notices.build("agent", "готово", notices.SUCCESS)["level"])

    def test_вид_с_одним_поводом_тон_не_выбирает(self):
        for kind in ("update", "task_from_chat"):
            with self.subTest(kind=kind):
                self.assertFalse(notices.level_allowed(kind))
                with self.assertRaises(ValueError):
                    notices.build(kind, "текст", notices.WARNING)

    def test_выдуманный_уровень_не_проходит(self):
        """Показ рисует известные: чужой доехал бы серой карточкой."""
        with self.assertRaises(ValueError):
            notices.build("agent", "текст", "катастрофа")

    def test_уровень_по_умолчанию_из_реестра(self):
        self.assertEqual(notices.NOTICES["agent"]["level"],
                         notices.build("agent", "текст")["level"])

    def test_показ_знает_все_уровни_службы(self):
        src = ROOT / "frontend" / "src" / "components" / "Notices.jsx"
        text = src.read_text(encoding="utf-8")
        for level in notices.LEVELS:
            with self.subTest(level=level):
                self.assertIn(f"{level}:", text, "уровень не нарисован")

    def test_лаунчер_видит_возможность_сервера(self):
        """Старый запущенный сервер не умеет /api/notify — это надо заметить."""
        self.assertTrue(CAPABILITIES.get("notify"))


class NotifyEndpointTest(unittest.TestCase):
    """`POST /api/notify` — вход в службу снаружи процесса."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self._saved = (config_mod.GLOBAL_CONFIG_FILE, config_mod.GLOBAL_DIR)
        config_mod.GLOBAL_CONFIG_FILE = tmp / "config.json"
        config_mod.GLOBAL_DIR = tmp
        self.sent: list[str] = []
        notices.bind(self.sent.append)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        config_mod.GLOBAL_CONFIG_FILE, config_mod.GLOBAL_DIR = self._saved
        notices.bind(None)

    def _disable_agent(self) -> None:
        config_mod.write_stored_global({"notice_sources": {"agent": False}})

    def test_уведомление_доезжает_до_канала(self):
        result = api_notify(NotifyIn(text="TASK-276 отдана на проверку",
                                     agent="Claude Opus 5",
                                     task="TASK-276", project="taskboard"))

        self.assertTrue(result["sent"])
        payload = json.loads(self.sent[0])
        self.assertEqual("agent", payload["kind"])
        self.assertEqual("TASK-276 отдана на проверку", payload["text"])
        self.assertEqual("Claude Opus 5", payload["agent"])
        self.assertEqual("TASK-276", payload["task"])
        self.assertEqual("taskboard", payload["project"])

    def test_уведомление_чужого_проекта_доезжает_с_подписью(self):
        """Канал один на весь реестр: доска, открытая на другом проекте, зовёт.

        Человек мог уйти работать в другой проект, пока агент трудится в этом.
        Молчать о чужом значит потерять ровно те события, которых он не ждёт,
        поэтому имя проекта едет в событии — показ назовёт его.
        """
        result = api_notify(NotifyIn(text="TASK-279 отдана на проверку",
                                     agent="Claude Opus 5", project="jp_trainer"))

        self.assertTrue(result["sent"])
        self.assertEqual("jp_trainer", json.loads(self.sent[0])["project"])

    def test_без_имени_модели_поля_в_событии_нет(self):
        """Показ отличает «автора нет» от «автор пустой» одной проверкой."""
        api_notify(NotifyIn(text="нужен ответ"))

        self.assertNotIn("agent", json.loads(self.sent[0]))

    def test_заголовок_берётся_из_реестра(self):
        """Текст — от агента, заголовок — от службы: он одинаков у всех вызовов."""
        api_notify(NotifyIn(text="нужен ответ"))

        self.assertEqual(notices.NOTICES["agent"]["title"],
                         json.loads(self.sent[0])["title"])

    def test_пустое_уведомление_не_показывают(self):
        with self.assertRaises(HTTPException) as ctx:
            api_notify(NotifyIn(text="   "))

        self.assertEqual(400, ctx.exception.status_code)
        self.assertFalse(self.sent)

    def test_уровень_доезжает_до_показа(self):
        api_notify(NotifyIn(text="упали тесты", level="warning"))

        self.assertEqual("warning", json.loads(self.sent[0])["level"])

    def test_выдуманный_уровень_отклоняют(self):
        with self.assertRaises(HTTPException) as ctx:
            api_notify(NotifyIn(text="текст", level="катастрофа"))

        self.assertEqual(400, ctx.exception.status_code)
        self.assertFalse(self.sent)

    def test_чужой_вид_снаружи_не_принимают(self):
        for kind in ("update", "task_from_chat", "чего-то-новенькое"):
            with self.subTest(kind=kind):
                with self.assertRaises(HTTPException) as ctx:
                    api_notify(NotifyIn(text="текст", kind=kind))
                self.assertEqual(400, ctx.exception.status_code)
        self.assertFalse(self.sent)

    def test_выключенный_источник_молчит_и_называет_причину(self):
        """Молчание без объяснения агент принимает за поломку и зовёт снова."""
        self._disable_agent()

        result = api_notify(NotifyIn(text="TASK-276 отдана на проверку"))

        self.assertFalse(result["sent"])
        self.assertEqual("disabled", result["reason"])
        self.assertFalse(self.sent)

    def test_без_канала_причина_другая(self):
        notices.bind(None)

        result = api_notify(NotifyIn(text="TASK-276 отдана на проверку"))

        self.assertFalse(result["sent"])
        self.assertEqual("no_listeners", result["reason"])

    def test_закрытая_доска_отправкой_не_считается(self):
        """Истории у уведомлений нет: ушедшее в пустую комнату не увидит никто.

        Ответить агенту «показано» значит сказать, что человека позвали, — а
        его не звали, и он про задачу не узнает.
        """
        notices.bind(self.sent.append, lambda: 0)

        result = api_notify(NotifyIn(text="TASK-276 отдана на проверку"))

        self.assertFalse(result["sent"])
        self.assertEqual("no_listeners", result["reason"])
        self.assertFalse(self.sent)

    def test_открытая_доска_уведомление_получает(self):
        notices.bind(self.sent.append, lambda: 2)

        self.assertTrue(api_notify(NotifyIn(text="TASK-276 на проверку"))["sent"])
        self.assertEqual(1, len(self.sent))


class NotifyScriptTest(unittest.TestCase):
    """Скрипт автономен: сервер может быть не запущен, конфига может не быть."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name) / "project" / "tasks"
        self.tasks_dir.mkdir(parents=True)
        self.script = _load_script()
        # Дом подменён на пустой: скрипт читает глобальный конфиг и реестр
        # проектов машины, и настоящие трогать в тестах нельзя
        self.home = Path(self._tmp.name) / "home"
        (self.home / ".taskboard").mkdir(parents=True)
        patch = mock.patch.object(Path, "home", staticmethod(lambda: self.home))
        patch.start()
        self.addCleanup(patch.stop)

    def test_порт_по_умолчанию_поставочный(self):
        """Конфига нет вовсе — остаётся значение поставки, а не отказ."""
        self.assertEqual(DEFAULTS["port"],
                         self.script.server_port(self.tasks_dir))

    def test_проектный_конфиг_переопределяет_порт(self):
        (self.tasks_dir / ".taskboard.json").write_text(
            json.dumps({"port": 9123}), encoding="utf-8")

        self.assertEqual(9123, self.script.server_port(self.tasks_dir))

    def test_мусор_в_конфиге_не_роняет_скрипт(self):
        (self.tasks_dir / ".taskboard.json").write_text("не json", encoding="utf-8")

        self.assertEqual(DEFAULTS["port"], self.script.server_port(self.tasks_dir))

    def _registry(self, projects: list[dict], active: str = "") -> None:
        """Реестр проектов в подменённом доме."""
        (self.home / ".taskboard" / "projects.json").write_text(
            json.dumps({"active": active, "projects": projects}),
            encoding="utf-8")

    def test_глобальный_конфиг_переопределяет_порт(self):
        (self.home / ".taskboard" / "config.json").write_text(
            json.dumps({"port": 9200}), encoding="utf-8")

        self.assertEqual(9200, self.script.server_port(self.tasks_dir))

    def test_имя_проекта_берётся_из_реестра(self):
        self._registry([{"name": "taskboard", "tasks_dir": str(self.tasks_dir)}])

        self.assertEqual("taskboard", self.script.project_name(self.tasks_dir))

    def test_чужая_папка_задач_именем_не_притворяется(self):
        """Имя соседа по реестру не подставляется: совпадать должна папка."""
        self._registry([{"name": "другой", "tasks_dir": str(self.tasks_dir / "нет")}])

        self.assertNotEqual("другой", self.script.project_name(self.tasks_dir))

    def test_проект_вне_реестра_подписан_именем_папки(self):
        """Иначе чужая всплывашка приходит без подписи и сходит за свою.

        Проект убрали с доски или перенесли папку — в реестре его нет, но
        уведомление всё равно доезжает до открытой доски. Безымянное, оно
        читается как событие открытого проекта.
        """
        self._registry([])

        self.assertEqual("project", self.script.project_name(self.tasks_dir))

    def test_активный_проект_на_подпись_не_влияет(self):
        """Скрипт называет свой проект, а не тот, что открыт на доске."""
        self._registry([{"name": "мой", "tasks_dir": str(self.tasks_dir)},
                        {"name": "открытый", "tasks_dir": str(self.home)}],
                       active="открытый")

        self.assertEqual("мой", self.script.project_name(self.tasks_dir))

    def test_без_сервера_скрипт_не_падает(self):
        """Доску не запускали — это обычное состояние, а не сбой работы агента.

        Порт берём заведомо свободный: живой сервер на поставочном порту
        получил бы от тестов настоящую всплывашку.
        """
        (self.tasks_dir / ".taskboard.json").write_text(
            json.dumps({"port": 9123}), encoding="utf-8")

        result = self.script.notify(self.tasks_dir, "TASK-276 отдана на проверку")

        self.assertFalse(result["sent"])
        self.assertEqual("no_server", result["reason"])

    def test_имя_модели_уезжает_на_доску(self):
        sent = {}

        def fake_urlopen(request, timeout=0):
            sent["body"] = json.loads(request.data.decode("utf-8"))
            raise OSError("дальше не ходим — интересен сам запрос")

        with mock.patch.object(self.script.urllib.request, "urlopen", fake_urlopen):
            self.script.notify(self.tasks_dir, "TASK-276 на проверку",
                               "Claude Opus 5", "TASK-276")

        self.assertEqual("Claude Opus 5", sent["body"]["agent"])
        self.assertEqual("TASK-276", sent["body"]["task"])

    def test_без_имени_модели_скрипт_не_зовёт(self):
        """Безымянное «вас зовут» не говорит человеку, кто именно его зовёт."""
        with mock.patch.object(sys, "argv", ["notify.py", "текст"]),                 contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                self.script.main()

        self.assertNotEqual(0, ctx.exception.code)

    def test_метка_берётся_из_сессии_среды(self):
        """По ней потом отзывают: соседняя сессия зовёт по своему поводу."""
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "ses-1"}):
            key = self.script.session_key("agent", Path("/проект/tasks"))

        self.assertIn("ses-1", key)
        self.assertTrue(key.startswith("agent:"))

    def test_без_сессии_метка_по_папке_проекта(self):
        """Среда сессии не называет — отзыв всё равно должен работать, пусть
        и грубее: одна метка на все сессии этого проекта."""
        with mock.patch.dict(os.environ, {}, clear=True):
            key = self.script.session_key("env", Path("."))

        self.assertIn(":dir:", key)
        self.assertTrue(key.startswith("env:"))

    def test_ни_сессии_ни_папки_метки_нет(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual("", self.script.session_key("env"))

    def test_скрипт_знает_те_же_уровни(self):
        """Второй список уровней разошёлся бы со службой молча."""
        from backend import notices as service

        self.assertEqual(set(service.LEVELS), set(self.script.LEVELS))

    def test_каждая_причина_молчания_названа_словами(self):
        for reason in ("no_server", "disabled", "no_listeners"):
            with self.subTest(reason=reason):
                self.assertIn(reason, self.script._REASONS)


class NotifyDeliveryTest(unittest.TestCase):
    """Скрипт — часть поставки: его отсутствие и устаревание видно баннером."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        self.cfg["harnesses"] = {"claude": True, "opencode": False}
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})

    def _codes(self) -> list[str]:
        return [issue["code"] for issue in environment_issues(self.tasks_dir, self.cfg)]

    def test_скрипт_разворачивается_в_проект(self):
        self.assertTrue((self.tasks_dir / "notify.py").is_file())

    def test_полная_поставка_молчит(self):
        self.assertNotIn("no_notify_script", self._codes())
        self.assertNotIn("outdated_notify_script", self._codes())

    def test_отсутствие_скрипта_видно(self):
        (self.tasks_dir / "notify.py").unlink()

        self.assertIn("no_notify_script", self._codes())

    def test_отставший_скрипт_видно(self):
        """Отстал от шаблона, а не правлен человеком: слепок подменяем вместе с файлом.

        Правка при неизменившемся шаблоне — кастомизация, и о ней не сообщают.
        """
        from backend import baseline

        (self.tasks_dir / "notify.py").write_text("# прежняя версия", encoding="utf-8")
        baseline.write(self.root, "notify_script", "notify.py",
                       "# прежняя версия", self.cfg)

        self.assertIn("outdated_notify_script", self._codes())

    def test_отставший_скрипт_чинится_кнопкой(self):
        from backend import baseline

        original = (self.tasks_dir / "notify.py").read_text(encoding="utf-8")
        (self.tasks_dir / "notify.py").write_text("# прежняя версия", encoding="utf-8")
        baseline.write(self.root, "notify_script", "notify.py",
                       "# прежняя версия", self.cfg)

        result = scaffold_project(self.tasks_dir, self.cfg, {"parts": ["notify_script"]})

        self.assertIn("notify.py", result["replaced"])
        self.assertEqual(original,
                         (self.tasks_dir / "notify.py").read_text(encoding="utf-8"))
        self.assertNotIn("outdated_notify_script", self._codes())

    def test_имя_скрипта_не_настройка(self):
        """Имена системных артефактов — константы поставки (TASK-053)."""
        self.cfg["notify_script"] = "своё_имя.py"
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})

        self.assertFalse((self.tasks_dir / "своё_имя.py").exists())
        self.assertTrue((self.tasks_dir / "notify.py").is_file())


class NotifyRulesTest(unittest.TestCase):
    """Правила «когда звать» едут в проект пользователя, а не живут в задаче."""

    def test_правила_называют_скрипт(self):
        text = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")

        self.assertIn("tasks/notify.py", text)
        self.assertIn("Уведомления человека", text)

    def test_правила_называют_и_когда_не_звать(self):
        """Половина правила — про шум: иначе уведомление превращается в поток."""
        text = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        section = text.split("## Уведомления человека", 1)[1].split("\n## ", 1)[0]

        self.assertIn("не** шлётся", section)
        self.assertIn("сразу после реплики человека", section)

    def test_правила_отличают_конец_ответа_от_возврата_человека(self):
        """Позвать нужно отошедшего человека, а не отметить каждый конец хода."""
        text = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        section = text.split("## Уведомления человека", 1)[1].split("\n## ", 1)[0]

        self.assertIn("конец каждого ответа", section)
        self.assertIn("последней реплики человека", section)
        self.assertIn("автономной работы", section)
        self.assertIn("Запрос разрешения", section)

    def test_handoff_учитывает_свежесть_реплики_человека(self):
        skill = AGENTIC / ".claude" / "skills" / "handoff-task" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        section = text.split("## Шаг 7. Позвать человека к доске", 1)[1]

        self.assertIn("последней реплики человека", section)
        self.assertIn("автономной работы", section)

    def test_об_уведомлении_не_отчитываются_в_итоговой_реплике(self):
        texts = [AGENTIC / "rules_section.md",
                 AGENTIC / ".claude" / "skills" / "handoff-task" / "SKILL.md"]
        for path in texts:
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("фоновый механизм", body)
                self.assertIn("не упоминай", body.lower())

    def test_передача_на_проверку_зовёт_человека(self):
        skill = AGENTIC / ".claude" / "skills" / "handoff-task" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertIn("tasks/notify.py", text)

    def test_тексты_поставки_велят_представиться(self):
        """Иначе агент зовёт безымянно, и человек не знает, кто его дёрнул."""
        texts = [AGENTIC / "rules_section.md",
                 AGENTIC / ".claude" / "skills" / "handoff-task" / "SKILL.md"]
        for path in texts:
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                call = body.split("tasks/notify.py", 1)[1].split(chr(10), 1)[0]
                self.assertIn("--agent", call)

    def test_всплывашка_показывает_модель(self):
        src = (ROOT / "frontend" / "src" / "components" / "Notices.jsx")
        text = src.read_text(encoding="utf-8")

        self.assertIn("notice.agent", text)

    def test_всплывашка_подписывает_чужой_проект(self):
        """Подпись — только у чужого: своё имя в углу собственной доски лишнее."""
        src = (ROOT / "frontend" / "src" / "components" / "Notices.jsx")
        text = src.read_text(encoding="utf-8")

        self.assertIn("notice.project !== activeProject", text)
        self.assertIn("Проект: ", text)


if __name__ == "__main__":
    unittest.main()
