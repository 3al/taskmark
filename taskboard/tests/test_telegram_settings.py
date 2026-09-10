"""Настройки телеграм-интеграции: сохранение, проверка токена, выбор чата.

Сеть не трогается: клиент Bot API подменяется. Глобальный конфиг подменяется
временной папкой — `~/.taskboard` пользователя тесты трогать не должны.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import app as app_module  # noqa: E402
from backend import config, help_docs, telegram_source  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
SETTINGS = FRONTEND / "components" / "SettingsModal.jsx"


class SavedSettingsTest(unittest.TestCase):
    """Ключи возможности глобальные: бот один на человека, не на репозиторий."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = (config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE)
        config.GLOBAL_DIR = Path(self._tmp.name)
        config.GLOBAL_CONFIG_FILE = Path(self._tmp.name) / "config.json"
        self.addCleanup(self._restore)
        patch = mock.patch.object(app_module.registry, "get_active", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def _restore(self) -> None:
        config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE = self._orig

    def stored(self) -> dict:
        return json.loads(config.GLOBAL_CONFIG_FILE.read_text(encoding="utf-8"))

    def save(self, updates: dict):
        return app_module.api_save_config(app_module.ConfigIn(updates=updates))

    def test_настройки_доезжают_до_файла(self):
        with mock.patch.object(app_module, "restart_telegram_poller"):
            self.save({"telegram": True, "telegram_token": "123:AAH",
                       "telegram_username": "kostya",
                       "telegram_chats": {"-100": ["Проект"]},
                       "telegram_tag": "задача",
                       "telegram_overdue_repeat": "weekly"})
        saved = self.stored()
        self.assertTrue(saved["telegram"])
        self.assertEqual(saved["telegram_token"], "123:AAH")
        self.assertEqual(saved["telegram_chats"], {"-100": ["Проект"]})
        self.assertEqual(saved["telegram_overdue_repeat"], "weekly")

    def test_снятый_путь_доезжает_до_файла(self):
        """`False` — такое же значение, как остальные, и теряться ему нельзя.

        Ключ, которого нет в реестре, обработчик молча выбрасывает: галочка
        снимается, сохраняется — и возвращается на место.
        """
        with mock.patch.object(app_module, "restart_telegram_poller"):
            self.save({"telegram_route": False,
                       "telegram_proxy": "socks5://p:1080"})
        saved = self.stored()
        self.assertIs(saved["telegram_route"], False)
        self.assertEqual(saved["telegram_proxy"], "socks5://p:1080",
                         "адрес должен пережить выключение пути")

    def test_сохранение_перезапускает_поллер(self):
        """Иначе включённая возможность ждёт перезапуска сервера — а человек
        уже нажал «Сохранить» и ждёт, что бот заработает."""
        with mock.patch.object(app_module, "restart_telegram_poller") as restart:
            self.save({"telegram": True, "telegram_token": "123:AAH"})
        restart.assert_called_once()


class CheckTokenTest(unittest.TestCase):
    """Кнопка «Проверить»: человек должен увидеть имя своего бота."""

    def test_живой_токен_отдаёт_имя_бота(self):
        with mock.patch.object(telegram_source, "get_me",
                               return_value={"username": "team_tasks_bot"}):
            result = app_module.api_telegram_check(
                app_module.TelegramCheckIn(token="123:AAH"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["username"], "team_tasks_bot")

    def test_отказ_api_объясняется_человеку(self):
        with mock.patch.object(telegram_source, "get_me",
                               side_effect=telegram_source.TelegramError("Unauthorized")):
            with self.assertRaises(app_module.HTTPException) as ctx:
                app_module.api_telegram_check(
                    app_module.TelegramCheckIn(token="плохой"))
        self.assertIn("Unauthorized", str(ctx.exception.detail))

    def test_пустой_токен_в_сеть_не_ходит(self):
        with mock.patch.object(telegram_source, "get_me") as get_me:
            with self.assertRaises(app_module.HTTPException):
                app_module.api_telegram_check(app_module.TelegramCheckIn(token="  "))
        get_me.assert_not_called()


class SeenChatsTest(unittest.TestCase):
    """Чат выбирается по имени: id у групп — отрицательное число."""

    def chats(self, seen, cfg):
        """Ответ эндпоинта на подменённых чатах и конфиге — без сети."""
        with mock.patch.object(telegram_source, "seen_chats", return_value=seen),              mock.patch.object(app_module, "load_global_config", return_value=cfg):
            return app_module.api_telegram_chats()["chats"]

    def test_отдаёт_увиденные_чаты(self):
        seen = [{"id": -100, "title": "Разработка"}]
        self.assertEqual(self.chats(seen, {}), seen)

    def test_привязанный_чат_виден_даже_если_бот_его_не_встречал(self):
        """Иначе после перезапуска настройка невидима и нередактируема."""
        with mock.patch.object(telegram_source, "chat_title", return_value="Разработка"):
            chats = self.chats([], {"telegram_chats": {"-100": "Проект"}})
        self.assertEqual(chats, [{"id": -100, "title": "Разработка"}])

    def test_имя_не_спрашивается_второй_раз(self):
        """Бот его уже видел — лишний запрос к API ни к чему."""
        with mock.patch.object(telegram_source, "chat_title") as title:
            self.chats([{"id": -100, "title": "Разработка"}],
                       {"telegram_chats": {"-100": "Проект"}})
        title.assert_not_called()


class SettingsUiTest(unittest.TestCase):
    """Вкладка настроек: человек не знает слова BotFather — его надо провести."""

    def source(self) -> str:
        return SETTINGS.read_text(encoding="utf-8")

    def test_вкладка_объявлена_в_реестре(self):
        text = self.source()
        match = re.search(r"const TABS = \[(.+?)\]", text, re.S)
        self.assertIsNotNone(match)
        self.assertIn("telegram", match.group(1), "вкладки телеграма нет в реестре")
        self.assertRegex(match.group(1),
                         r"key: 'telegram'.*scope: 'global'",
                         "вкладка должна быть глобальной: бот один на человека")

    def test_есть_поле_токена_и_проверка(self):
        text = self.source()
        self.assertIn("telegram_token", text, "негде ввести токен")
        self.assertIn("checkTelegram", text, "нет проверки токена")

    def test_чат_выбирается_из_увиденных(self):
        text = self.source()
        self.assertIn("telegramChats", text, "список увиденных чатов не запрашивается")
        self.assertIn("telegram_chats", text, "привязку чата некуда сохранить")

    def test_галочка_возможности_выглядит_как_остальные(self):
        """Без accent-класса браузер рисует системный чекбокс — он выбивается."""
        text = self.source()
        block = text[text.index("tab === 'telegram'"):text.index("tab === 'tool'")]
        checkbox = block[block.index('type="checkbox"'):]
        self.assertIn("accent-sky-500", checkbox[:200],
                      "галочка возможности оформлена не как остальные")

    def proxy_field(self) -> str:
        """Разметка поля прокси: от подсказки в нём до конца строки полей."""
        text = self.source()
        start = text.index('placeholder="прокси:')
        return text[start:text.index('placeholder="свой адрес Bot API', start)]

    def test_путь_до_telegram_выключается_галочкой(self):
        """Иначе отказаться от прокси можно только стерев адрес."""
        text = self.source()
        self.assertIn("telegram_route", text, "выключателя пути нет")

    def test_пароль_прокси_показан_звёздочками(self):
        """Логин с паролем живут прямо в адресе — на экране их видно всем."""
        text = self.source()
        self.assertIn("maskProxy", text, "пароль в адресе прокси показан открыто")

    def test_поле_прокси_остаётся_редактируемым(self):
        """Нередактируемое поле ничем не отличается на вид.

        Человек тычет в него, ничего не происходит, и причину он не узнаёт —
        поэтому адрес правится всегда, а прячется он по уходу из поля.
        """
        block = self.proxy_field()
        self.assertNotIn("readOnly", block, "поле прокси нельзя править вслепую")
        self.assertIn("onFocus", block, "в поле человек должен видеть, что правит")
        self.assertIn("onBlur", block, "маска не возвращается после правки")

    def test_пароль_открывается_только_на_время(self):
        """Показ по удержанию: снятая на секунду маска не остаётся снятой.

        Переключатель «показать/скрыть» этим и плох — после него вставленный
        следующим адрес тоже оказывался открытым.
        """
        block = self.proxy_field()
        self.assertIn("onMouseDown", block, "глазка с удержанием нет")
        self.assertIn("onMouseUp", block, "показ не гасится по отпусканию")
        self.assertIn("onMouseLeave", block,
                      "уведённая с кнопки мышь оставила бы пароль открытым")

    def test_проверка_идёт_тем_же_путём(self):
        """Кнопка «Проверить» проверяет то, чем бот ходит сейчас."""
        text = self.source()
        block = text[text.index("const checkTelegram"):text.index("const chatBinding")]
        self.assertIn("telegram_route", block,
                      "проверка не учитывает выключенный путь")

    def test_значения_перед_рендером_переживают_пустой_конфиг(self):
        """Конфиг приезжает запросом, и на первом рендере его ещё нет.

        Разметка окна защищена `{config && …}`, а строки над `return` считаются
        **всегда**: обращение к полю без `?.` роняет всё окно в чёрный экран.
        """
        text = self.source()
        head = text[text.index("const field = "):text.index("\n  return (")]
        self.assertNotRegex(
            head, r"\bconfig\.\w",
            "обращение к конфигу до загрузки — окно упадёт на первом рендере")

    def test_список_чатов_обновляется_сам(self):
        """Человеку сказано «напишите в чат — он появится здесь»: значит сам,
        а не после закрытия и открытия окна."""
        text = self.source()
        self.assertIn("setInterval", text, "список чатов не обновляется")
        self.assertIn("clearInterval", text, "опрос не гасится при уходе с вкладки")

    def test_чат_можно_привязать_к_нескольким_проектам(self):
        text = self.source()
        self.assertIn("toggleExtraProject", text,
                      "дополнительные проекты чата выбрать нечем")

    def test_инструкция_ведёт_за_руку(self):
        text = self.source()
        self.assertIn("BotFather", text, "человек не знает, откуда берётся токен")
        self.assertIn("Group Privacy", text,
                      "без этого бот не увидит сообщения группы")

    def test_частота_повтора_просрочки_выбирается_явно(self):
        text = self.source()
        self.assertIn("telegram_overdue_repeat", text)
        for value in ("daily", "weekly", "once"):
            self.assertIn(f'value="{value}"', text)


class HelpSectionTest(unittest.TestCase):
    """Справку раздаёт сам инструмент — устаревшая врёт прямо в интерфейсе."""

    def section(self) -> str:
        ids = {item["id"] for item in help_docs.list_sections()}
        self.assertIn("telegram", ids, "в справке нет раздела про задачи из чата")
        return (help_docs.get_section("telegram") or {})["content"]

    def test_раздел_описывает_заведение_бота(self):
        text = self.section()
        for word in ("BotFather", "Group Privacy"):
            self.assertIn(word, text, f"в справке нет шага «{word}»")

    def test_раздел_предупреждает_о_границах(self):
        text = self.section()
        self.assertIn("с момента добавления", text,
                      "не сказано, что бот не видит прошлых сообщений")
        self.assertIn("нет ответа", text.lower(),
                      "не сказано, как понять, что задача не доехала")

    def test_раздел_описывает_повторы_просрочки(self):
        text = " ".join(self.section().split())
        for phrase in ("Задача просрочена", "раз в день", "раз в неделю",
                       "только один раз", "новая серия"):
            self.assertIn(phrase, text)

    def test_readme_ссылается_на_раздел(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("telegram", readme.lower(),
                      "README не знает про задачи из чата")


if __name__ == "__main__":
    unittest.main()
