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

        self.assertIn("notices.bind(watcher.send", text)


class StickySwitchTest(unittest.TestCase):
    """Ждать закрытия или таять — выбор человека, у каждого источника свой."""

    def test_по_умолчанию_всё_тает(self):
        """Висящее до крестика просят сами — обычно когда стопку не прочесть."""
        for kind in notices.NOTICES:
            with self.subTest(kind=kind):
                self.assertFalse(notices.is_sticky(kind, {}))

    def test_галочка_источника_включает_ожидание(self):
        cfg = {"notice_sticky": {"agent": True}}

        self.assertTrue(notices.is_sticky("agent", cfg))
        self.assertFalse(notices.is_sticky("update", cfg))

    def test_событие_несёт_готовый_ответ(self):
        """Показу остаётся «да» или «нет»: режим считает бэкенд."""
        self.assertIn("sticky", notices.build("agent", "текст"))

    def test_хранятся_только_отличия_от_умолчания(self):
        """Слепок всех источников заморозил бы поставку (config-defaults-freeze)."""
        self.assertEqual({}, notices.normalize_sticky({"agent": False}))
        self.assertEqual({"agent": True}, notices.normalize_sticky({"agent": True}))

    def test_мусор_и_чужие_ключи_отбрасываются(self):
        self.assertEqual({}, notices.normalize_sticky({"чужое": True}))
        self.assertEqual({}, notices.normalize_sticky({"agent": "да"}))
        self.assertEqual({}, notices.normalize_sticky("не словарь"))

    def test_форма_знает_состояние_каждого_источника(self):
        state = {s["kind"]: s for s in notices.sources_state(
            {"notice_sticky": {"agent": True}})}

        self.assertTrue(state["agent"]["sticky"])
        self.assertFalse(state["update"]["sticky"])

    def test_настройка_объяснена_при_наведении(self):
        source = (self.SRC / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")

        self.assertIn("notice_sticky", source)
        self.assertIn("ждут закрытия", source)
        self.assertIn("не гаснут по таймеру", source, "подсказки при наведении нет")

    SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


class SoundSwitchTest(unittest.TestCase):
    """Звук — вторая настройка источника, и живёт она отдельно от `sticky`."""

    SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"

    def test_по_умолчанию_молчат_все(self):
        """Обновление не должно вдруг начать шуметь у того, кто не просил."""
        for kind in notices.NOTICES:
            with self.subTest(kind=kind):
                self.assertFalse(notices.has_sound(kind, {}))

    def test_галочка_включает_звук_только_своему_источнику(self):
        cfg = {"notice_sound": {"agent": True}}

        self.assertTrue(notices.has_sound("agent", cfg))
        self.assertFalse(notices.has_sound("update", cfg))

    def test_звук_не_путается_с_ожиданием_закрытия(self):
        """Две настройки у одного источника — два разных ключа конфига."""
        cfg = {"notice_sticky": {"agent": True}}

        self.assertFalse(notices.has_sound("agent", cfg))
        self.assertFalse(notices.is_sticky("agent", {"notice_sound": {"agent": True}}))

    def test_событие_несёт_готовый_ответ(self):
        """Показу остаётся проиграть или промолчать: решает бэкенд."""
        self.assertIn("sound", notices.build("agent", "текст"))

    def test_хранятся_только_включённые(self):
        """Умолчание — тишина, и слепок всех источников заморозил бы её."""
        self.assertEqual({}, notices.normalize_sound({"agent": False}))
        self.assertEqual({"agent": True}, notices.normalize_sound({"agent": True}))

    def test_мусор_и_чужие_ключи_отбрасываются(self):
        self.assertEqual({}, notices.normalize_sound({"чужое": True}))
        self.assertEqual({}, notices.normalize_sound({"agent": "да"}))
        self.assertEqual({}, notices.normalize_sound("не словарь"))

    def test_форма_знает_состояние_каждого_источника(self):
        state = {s["kind"]: s for s in notices.sources_state(
            {"notice_sound": {"agent": True}})}

        self.assertTrue(state["agent"]["sound"])
        self.assertFalse(state["update"]["sound"])


class SoundConfigApiTest(unittest.TestCase):
    """Настройка доезжает до конфига и возвращается форме."""

    def setUp(self) -> None:
        import tempfile

        from backend import config as config_mod

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self._saved = (config_mod.GLOBAL_CONFIG_FILE, config_mod.GLOBAL_DIR)
        config_mod.GLOBAL_CONFIG_FILE = tmp / "config.json"
        config_mod.GLOBAL_DIR = tmp
        self.config_mod = config_mod
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        (self.config_mod.GLOBAL_CONFIG_FILE,
         self.config_mod.GLOBAL_DIR) = self._saved

    def test_форма_сохраняет_звук_и_громкость(self):
        from backend.app import ConfigIn, api_save_config

        api_save_config(ConfigIn(updates={"notice_sound": {"agent": True},
                                          "notice_volume": "80"}))
        cfg = self.config_mod.load_global_config()

        self.assertEqual({"agent": True}, cfg["notice_sound"])
        self.assertEqual(80, cfg["notice_volume"])

    def test_громкость_вне_границ_зажимается_при_сохранении(self):
        from backend.app import ConfigIn, api_save_config

        api_save_config(ConfigIn(updates={"notice_volume": 1000}))

        self.assertEqual(notices.VOLUME_RANGE[1],
                         self.config_mod.load_global_config()["notice_volume"])

    def test_форма_получает_границы_громкости(self):
        """Иначе ползунок знал бы их из зашитого в JS числа."""
        from backend.app import api_get_config

        self.assertEqual(list(notices.VOLUME_RANGE),
                         api_get_config().get("notice_volume_range"))


class SoundFrontendTest(unittest.TestCase):
    """Что в звуке проверяется текстом, а не ухом."""

    SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"

    def form(self) -> str:
        return (self.SRC / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")

    def notices_jsx(self) -> str:
        return (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

    def test_у_источника_своя_галочка_звука(self):
        form = self.form()

        self.assertIn("notice_sound", form)
        self.assertIn("звук", form)

    def test_громкость_одна_на_всех(self):
        form = self.form()

        self.assertIn("notice_volume", form)
        self.assertIn("notice_volume_range", form, "границы зашиты в JS вместо бэкенда")

    def test_громкость_гаснет_без_единого_звучащего_источника(self):
        """Регулятор остаётся на месте, но крутить его нечему."""
        form = self.form()

        self.assertIn("anySound", form, "нет проверки «звучит ли хоть один источник»")
        self.assertIn("disabled={!anySound}", form, "регулятор не гаснет")

    def test_показ_играет_только_звучащему_уведомлению(self):
        source = self.notices_jsx()

        self.assertIn("notice.sound", source, "звук не привязан к признаку события")

    def test_запрет_автозапуска_не_роняет_показ(self):
        """До первого клика браузер звук не пустит — и это обычное состояние."""
        source = (self.SRC / "sound.js").read_text(encoding="utf-8")

        self.assertIn("catch", source, "отказ браузера не перехвачен")

    def test_сигнал_живёт_одним_модулем(self):
        """Его зовут двое — показ и настройки; вторая копия разошлась бы."""
        sound = (self.SRC / "sound.js").read_text(encoding="utf-8")

        self.assertIn("AudioContext", sound)
        for name, text in (("Notices.jsx", self.notices_jsx()),
                           ("SettingsModal.jsx", self.form())):
            with self.subTest(file=name):
                self.assertIn("from '../sound'", text, "модуль не подключён")
                self.assertNotIn("AudioContext", text, "вторая копия сигнала")

    def test_ползунок_даёт_послушать_выбранное(self):
        """Громкость выбирают ухом: цифра в процентах о ней ничего не говорит."""
        form = self.form()

        self.assertIn("onMouseUp", form, "сигнал не звучит по отпусканию мыши")
        self.assertIn("onKeyUp", form, "с клавиатуры ползунок остаётся немым")

    def test_звук_не_тянет_внешний_файл(self):
        """Доска работает офлайн, а бинарник в поставке пришлось бы обновлять."""
        source = (self.SRC / "sound.js").read_text(encoding="utf-8")

        self.assertIn("AudioContext", source)
        self.assertNotIn(".mp3", source)
        self.assertNotIn(".wav", source)

    def test_справка_объясняет_звук_и_первый_клик(self):
        docs = (Path(__file__).resolve().parents[2] / "docs" / "help"
                / "02-board.md").read_text(encoding="utf-8")

        self.assertIn("звук", docs.lower())
        self.assertIn("громкост", docs.lower())


class VolumeTest(unittest.TestCase):
    """Громкость общая: у звука один регулятор на все источники."""

    def test_умолчание_из_поставки(self):
        self.assertIn("notice_volume", DEFAULTS)
        low, high = notices.VOLUME_RANGE
        self.assertTrue(low <= DEFAULTS["notice_volume"] <= high)

    def test_значение_зажимается_в_границы(self):
        low, high = notices.VOLUME_RANGE
        default = DEFAULTS["notice_volume"]

        self.assertEqual(high, notices.normalize_volume(high + 50, default))
        self.assertEqual(low, notices.normalize_volume(low - 50, default))

    def test_строка_из_формы_понимается(self):
        """Форма шлёт числа строками, и «40» — то же самое, что 40."""
        self.assertEqual(40, notices.normalize_volume("40", DEFAULTS["notice_volume"]))

    def test_непонятное_заменяется_умолчанием(self):
        """Настройка вспомогательная: ронять из-за неё сохранение формы незачем."""
        default = DEFAULTS["notice_volume"]

        self.assertEqual(default, notices.normalize_volume("громко", default))
        self.assertEqual(default, notices.normalize_volume(None, default))


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


class IdleDelayTest(unittest.TestCase):
    """Через сколько минут простоя терминала звать человека."""

    def test_дефолт_три_минуты_в_границах(self):
        low, high = notices.IDLE_MINUTES_RANGE
        self.assertEqual(3, DEFAULTS["notice_idle_minutes"])
        self.assertTrue(low <= DEFAULTS["notice_idle_minutes"] <= high)

    def test_значение_из_формы_прижимается_к_границам(self):
        low, high = notices.IDLE_MINUTES_RANGE
        self.assertEqual(7, notices.normalize_idle_minutes("7", 3))
        self.assertEqual(high, notices.normalize_idle_minutes(10_000, 3))
        self.assertEqual(low, notices.normalize_idle_minutes(-1, 3))

    def test_ноль_допустим_это_звать_сразу(self):
        self.assertEqual(0, notices.normalize_idle_minutes(0, 3))

    def test_непонятное_значение_берёт_дефолт(self):
        self.assertEqual(3, notices.normalize_idle_minutes("скоро", 3))
        self.assertEqual(3, notices.normalize_idle_minutes(None, 3))

    def test_настройка_сохраняется_в_глобальный_конфиг(self):
        """Хук читает глобальный конфиг сам — значение должно туда доехать."""
        from backend import app as app_module

        saved = {}
        with mock.patch.object(app_module.registry, "get_active", return_value=None), \
                mock.patch.object(app_module, "load_global_config",
                                  return_value=dict(DEFAULTS)), \
                mock.patch.object(app_module, "save_global_config",
                                  side_effect=saved.update):
            app_module.api_save_config(
                app_module.ConfigIn(updates={"notice_idle_minutes": "500"}))

        self.assertEqual(notices.IDLE_MINUTES_RANGE[1],
                         saved.get("notice_idle_minutes"))


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

    def test_наведение_останавливает_всю_стопку(self):
        """Пока читают одну карточку, соседние не должны исчезать."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")
        card, stack = source.split("export default function Notices", 1)

        self.assertNotIn("setPaused", card, "пауза осталась локальной у карточки")
        self.assertIn("const [paused, setPaused] = useState(false)", stack)
        self.assertIn("paused={paused}", stack, "общая пауза не передана карточкам")

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

    def test_невидимая_доска_не_считает(self):
        """Уведомление адресовано отошедшему: сгорев в свёрнутом окне, оно не
        показалось бы вовсе, а вернуться к нему неоткуда — истории нет."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("visibilitychange", source, "видимость страницы не слушают")
        self.assertIn("document.hidden", source)
        self.assertIn("|| !visible) return", source,
                      "таймер исчезания не останавливается на невидимой доске")
        self.assertIn("paused || !visible", source, "полоска отсчёта не замирает")

    def test_стопку_можно_убрать_одним_движением(self):
        """Ждущие закрытия уведомления копятся — иначе крестики жмут по очереди."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("Закрыть все", source)
        self.assertIn("closingAll", source)

    def test_карточка_уходит_складываясь(self):
        """Иначе соседи прыгают на её место в момент размонтирования."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")
        css = (self.SRC / "index.css").read_text(encoding="utf-8")

        self.assertIn("offsetHeight", source, "высота не фиксируется перед уходом")
        self.assertIn("requestAnimationFrame", source)
        self.assertIn("notice-leaving", css)
        self.assertIn("transition:", css.split(".notice-leaving", 1)[1][:400])

    def test_стопка_стоит_в_окне_доски_а_не_у_края_экрана(self):
        """Нижний край окна прячется под панелью задач системы."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("absolute top-3 right-3", source)
        self.assertNotIn("fixed bottom", source)

    def test_уведомления_выше_модальных_окон(self):
        """Затемнение окна не должно размывать сообщение, которое зовёт человека."""
        source = (self.SRC / "components" / "Notices.jsx").read_text(encoding="utf-8")

        self.assertIn("z-[60]", source)

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
        self.assertIn("notice_idle_minutes", settings)
        self.assertIn("lifetime", notices_jsx)
        self.assertIn("seconds", notices_jsx)


if __name__ == "__main__":
    unittest.main()
