"""Служба уведомлений: модель события и его доставка (TASK-212).

Показ всплывашки проверяется руками — здесь проверяется то, что от него не
зависит: вид события берётся из реестра, текст приходит от источника, а сбой
доставки не роняет источник.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import notices  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402


class NoticeModelTest(unittest.TestCase):
    def tearDown(self) -> None:
        notices.bind(None)

    def test_вид_задаёт_уровень_и_заголовок(self):
        notice = notices.build("update", "Версия 2.0.0")

        self.assertEqual("update", notice["kind"])
        self.assertEqual(notices.NOTICES["update"]["level"], notice["level"])
        self.assertEqual(notices.NOTICES["update"]["title"], notice["title"])
        self.assertEqual("Версия 2.0.0", notice["text"])

    def test_неизвестный_вид_это_ошибка(self):
        """Молча проглоченное уведомление хуже отсутствующего."""
        with self.assertRaises(ValueError):
            notices.build("чего-то-новенькое", "текст")

    def test_поля_источника_едут_как_есть(self):
        notice = notices.build("task_from_chat", "TASK-007 · Заголовок",
                               task="TASK-007", project="taskboard")

        self.assertEqual("TASK-007", notice["task"])
        self.assertEqual("taskboard", notice["project"])

    def test_пустые_поля_не_едут(self):
        """Пустое имя проекта показывать нечем — поля просто нет."""
        notice = notices.build("task_from_chat", "текст", project="", task=None)

        self.assertNotIn("project", notice)
        self.assertNotIn("task", notice)

    def test_событие_помечено_видом_записи(self):
        """По каналу едут и слова (`changed`), и объекты — фронт их различает."""
        notice = notices.build("update", "текст")

        self.assertEqual("notice", notice["event"])

    def test_строка_канала_одна_и_читается(self):
        """SSE везёт `data:` построчно: перенос внутри разорвал бы событие."""
        line = notices.encode(notices.build("update", "первая\nвторая"))

        self.assertNotIn("\n", line)
        self.assertEqual("первая\nвторая", json.loads(line)["text"])
        self.assertIn("Доступна", line, "русский текст ушёл в \\u-экранирование")


class NoticeDeliveryTest(unittest.TestCase):
    def tearDown(self) -> None:
        notices.bind(None)

    def test_отправка_уходит_в_привязанный_канал(self):
        sent: list[str] = []
        notices.bind(sent.append)

        notices.emit("update", "Версия 2.0.0", version="2.0.0")

        self.assertEqual(1, len(sent))
        payload = json.loads(sent[0])
        self.assertEqual("update", payload["kind"])
        self.assertEqual("2.0.0", payload["version"])

    def test_без_канала_молчим_и_не_падаем(self):
        """Сервер мог не поднять SSE — источник события об этом не знает."""
        notices.bind(None)

        self.assertIsNone(notices.emit("update", "Версия 2.0.0"))

    def test_сбой_доставки_не_роняет_источник(self):
        def boom(_line: str) -> None:
            raise RuntimeError("канал отвалился")

        notices.bind(boom)

        self.assertIsNone(notices.emit("task_from_chat", "TASK-007"))


class NoticeSourcesTest(unittest.TestCase):
    """Источники событий: реестр знает их вид, второй сигнализации нет."""

    def test_реестр_знает_вид_каждого_источника(self):
        for kind in ("update", "task_from_chat"):
            with self.subTest(kind=kind):
                self.assertIn(kind, notices.NOTICES)

    def test_проверка_обновлений_зовёт_службу(self):
        src = Path(__file__).resolve().parents[1] / "backend" / "updater.py"
        text = src.read_text(encoding="utf-8")

        self.assertIn("notices.emit", text,
                      "находка обновления не переведена на общую службу")

    def test_задача_из_чата_поднимает_уведомление(self):
        src = Path(__file__).resolve().parents[1] / "backend" / "telegram_intake.py"
        text = src.read_text(encoding="utf-8")

        self.assertIn('notices.emit("task_from_chat"', text)

    def test_служба_привязана_к_каналу_доски(self):
        """Один канал на всё: второй EventSource — вторая точка отказа."""
        src = Path(__file__).resolve().parents[1] / "backend" / "app.py"
        text = src.read_text(encoding="utf-8")

        self.assertIn("notices.bind(watcher.send)", text)


class SourceSwitchTest(unittest.TestCase):
    """Выключатели источников: чего человек не хочет слышать."""

    def tearDown(self) -> None:
        notices.bind(None)

    def test_по_умолчанию_источник_говорит(self):
        """Вид, которого нет в настройках, включён: иначе новый молчал бы."""
        self.assertTrue(notices.enabled("update", {}))
        self.assertTrue(notices.enabled("task_from_chat", {"notice_sources": {}}))

    def test_выключенный_источник_молчит(self):
        self.assertFalse(notices.enabled("update", {"notice_sources": {"update": False}}))

    def test_выключенный_источник_не_отправляет(self):
        sent: list[str] = []
        notices.bind(sent.append)

        with mock.patch.object(notices, "enabled", return_value=False):
            result = notices.emit("update", "Версия 2.0.0")

        self.assertIsNone(result)
        self.assertEqual([], sent)

    def test_хранятся_только_выключенные(self):
        """Включённый вид — это отсутствие записи, а не `true` в файле."""
        stored = notices.normalize_sources({"update": False, "task_from_chat": True})

        self.assertEqual({"update": False}, stored)

    def test_неизвестные_ключи_отбрасываются(self):
        """Реестр видов задаёт поставка, а не пришедший запрос."""
        self.assertEqual({}, notices.normalize_sources({"чужое": False}))
        self.assertEqual({}, notices.normalize_sources("не словарь"))

    def test_состояние_для_формы_перечисляет_весь_реестр(self):
        state = notices.sources_state({"notice_sources": {"update": False}})

        self.assertEqual(len(notices.NOTICES), len(state))
        by_kind = {item["kind"]: item for item in state}
        self.assertFalse(by_kind["update"]["enabled"])
        self.assertTrue(by_kind["task_from_chat"]["enabled"])
        self.assertTrue(all(item["label"] for item in state), "источник без имени")


class LifetimeTest(unittest.TestCase):
    """Сколько висит всплывашка: настройка, а не число в JSX."""

    def test_дефолт_в_границах(self):
        low, high = notices.SECONDS_RANGE
        self.assertTrue(low <= DEFAULTS["notice_seconds"] <= high)

    def test_строка_из_формы_становится_числом(self):
        self.assertEqual(12, notices.normalize_seconds("12", 6))

    def test_за_границей_прижимается_к_ней(self):
        low, high = notices.SECONDS_RANGE
        self.assertEqual(high, notices.normalize_seconds(10_000, 6))
        self.assertEqual(low, notices.normalize_seconds(-5, 6))

    def test_ноль_допустим_это_не_гасить(self):
        """Ноль — «ждать крестика», а не «показать мгновенно»."""
        self.assertEqual(0, notices.normalize_seconds(0, 6))

    def test_непонятное_значение_берёт_дефолт(self):
        self.assertEqual(6, notices.normalize_seconds("долго", 6))
        self.assertEqual(6, notices.normalize_seconds(None, 6))


class FrontendTest(unittest.TestCase):
    """Всплывашка: что проверяется текстом, а не глазами."""

    SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"

    def test_канал_разбирает_уведомление(self):
        source = (self.SRC / "api.js").read_text(encoding="utf-8")

        self.assertIn("JSON.parse", source)
        self.assertIn("onNotice", source)

    def test_точка_версии_питается_тем_же_событием(self):
        """Второй сигнализации под обновления не осталось."""
        source = (self.SRC / "api.js").read_text(encoding="utf-8")

        self.assertIn("notice.kind === 'update'", source)
        self.assertNotIn("event.data === 'update'", source)

    def test_всплывашка_умеет_паузу_и_крестик(self):
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("onMouseEnter", source, "наведение не останавливает исчезание")
        self.assertIn("onMouseLeave", source)
        self.assertIn("onClick={close}", source, "крестика нет")

    def test_пауза_это_состояние_а_не_снятый_по_месту_таймер(self):
        """Тогда таймер живёт в эффекте, и остаток переживает ре-рендер."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("setPaused", source)
        self.assertIn("left.current = Math.max", source, "прожитое время не списывается")

    def test_отсчёт_виден_глазом(self):
        """Иначе «а пауза вообще работает?» проверяется только секундомером."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")
        css = (self.SRC / "index.css").read_text(encoding="utf-8")

        self.assertIn("animationPlayState", source)
        self.assertIn("notice-bar", css)

    def test_стопка_стоит_в_окне_доски_а_не_у_края_экрана(self):
        """Нижний край окна прячется под панелью задач системы."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("absolute top-3 right-3", source)
        self.assertNotIn("fixed bottom", source)

    def test_стопка_не_перехватывает_мышь(self):
        """Полоса в углу не должна ломать перетаскивание задач под ней."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("pointer-events-none", source)
        self.assertIn("pointer-events-auto", source)

    def test_чужой_проект_назван(self):
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("activeProject", source)

    def test_доска_показывает_стопку(self):
        source = (self.SRC / "App.jsx").read_text(encoding="utf-8")

        self.assertIn("<Notices", source)

    def test_форма_берёт_список_источников_с_бэкенда(self):
        """Второй перечень видов в JS разошёлся бы с реестром молча."""
        source = (self.SRC / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")

        self.assertIn("notice_kinds", source)
        self.assertIn("notice_sources", source)

    def test_время_показа_настраивается(self):
        """Число в JSX означало бы вторую копию настройки."""
        settings = (self.SRC / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")
        notices_jsx = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("notice_seconds", settings)
        self.assertIn("lifetime", notices_jsx)
        self.assertIn("seconds", notices_jsx)


if __name__ == "__main__":
    unittest.main()
