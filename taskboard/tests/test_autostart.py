"""Запуск таскборда при входе в систему.

Папка автозагрузки подменяется временной: настоящую трогать нельзя, иначе
тесты пропишут инструмент в автозапуск разработчика.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import autostart

ROOT = Path(__file__).resolve().parent.parent.parent


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.startup = Path(self.tmp.name) / "Startup"
        patches = [
            mock.patch.object(autostart, "startup_dir", return_value=self.startup),
            mock.patch.object(autostart, "supported", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def entry(self) -> Path:
        return self.startup / autostart.ENTRY_NAME


class EnableTest(Base):
    """Включение: запись в автозагрузке, которая действительно запускает нас."""

    def test_создаёт_запись(self):
        result = autostart.enable(ROOT)
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.entry().is_file())

    def test_запись_запускает_лаунчер_без_браузера(self):
        """Окно при каждом входе в систему человек не просил."""
        autostart.enable(ROOT)
        text = self.entry().read_text(encoding="utf-8")
        self.assertIn("taskboard.py", text)
        self.assertIn("--no-browser", text)

    def test_запись_ничего_не_спрашивает(self):
        """При входе в систему отвечать на вопросы установки некому,
        да и спросить не через что: консоли у процесса нет."""
        autostart.enable(ROOT)
        self.assertIn("--yes", self.entry().read_text(encoding="utf-8"))

    def test_запись_задаёт_рабочую_папку(self):
        """Иначе проект выводится из рабочей папки Проводника.

        Элементы автозагрузки запускаются из `C:\\Windows\\System32`, где есть
        папка `Tasks`: лаунчер принимал её за проект и делал активной (TASK-233).
        """
        autostart.enable(ROOT)
        text = self.entry().read_text(encoding="utf-8")
        self.assertIn(f'cd /d "{ROOT}"', text,
                      "запуск зависит от того, откуда его дёрнули")

    def test_повторное_включение_не_плодит_вторую_запись(self):
        autostart.enable(ROOT)
        autostart.enable(ROOT)
        self.assertEqual(len(list(self.startup.iterdir())), 1)

    def test_папки_автозагрузки_может_не_быть(self):
        self.assertFalse(self.startup.exists())
        self.assertTrue(autostart.enable(ROOT)["ok"])


class DisableTest(Base):
    def test_выключение_убирает_запись(self):
        autostart.enable(ROOT)
        self.assertTrue(autostart.disable()["ok"])
        self.assertFalse(self.entry().exists())

    def test_выключение_без_записи_не_ошибка(self):
        self.assertTrue(autostart.disable()["ok"])


class StatusTest(Base):
    def test_состояние_читается_с_диска(self):
        self.assertFalse(autostart.status(ROOT)["enabled"])
        autostart.enable(ROOT)
        self.assertTrue(autostart.status(ROOT)["enabled"])

    def test_запись_из_другой_папки_видна(self):
        """Репозиторий переехал — при входе в систему запускалось бы не то.
        Молчать об этом нельзя: человек узнает, только когда что-то не сработает."""
        autostart.enable(ROOT)
        self.assertFalse(autostart.status(ROOT)["stale"])

        other = Path(self.tmp.name) / "старая-копия"
        self.assertTrue(autostart.status(other)["stale"])

    def test_повторное_включение_чинит_запись(self):
        other = Path(self.tmp.name) / "старая-копия"
        autostart.enable(other)
        self.assertTrue(autostart.status(ROOT)["stale"])
        autostart.enable(ROOT)
        self.assertFalse(autostart.status(ROOT)["stale"])

    def test_состояние_называет_путь_записи(self):
        state = autostart.status(ROOT)
        self.assertIn(autostart.ENTRY_NAME, state["path"])


class RefreshTest(Base):
    """Самопочинка записи: у тех, кто включил автозапуск раньше, в папке
    автозагрузки лежит старый текст. Он ведёт в правильную копию, поэтому
    `stale` его нормальным и считает, — и поломка повторяется при каждом входе
    в систему, пока запись не переписана (TASK-233)."""

    def outdated(self, root: Path = ROOT) -> None:
        """Запись, какой её писала прежняя версия: без рабочей папки."""
        self.startup.mkdir(parents=True, exist_ok=True)
        crlf = chr(13) + chr(10)
        launcher = root / "taskboard.py"
        self.entry().write_text(
            '@echo off' + crlf
            + f'start "" "pythonw" "{launcher}" --no-browser' + crlf,
            encoding="utf-8", newline="")

    def test_устаревшая_запись_переписывается(self):
        self.outdated()
        self.assertTrue(autostart.refresh_if_outdated(ROOT))
        self.assertEqual(self.entry().read_text(encoding="utf-8", newline=""),
                         autostart._script(ROOT))

    def test_актуальная_запись_не_трогается(self):
        autostart.enable(ROOT)
        self.assertFalse(autostart.refresh_if_outdated(ROOT))

    def test_записи_нет_и_она_не_заводится(self):
        """Автозапуск включает человек. Молча прописать его — не починка."""
        self.assertFalse(autostart.refresh_if_outdated(ROOT))
        self.assertFalse(self.entry().exists())

    def test_запись_другой_копии_не_трогается(self):
        """Тут перезапись меняет смысл, а не форму: об этом спрашивают
        человека — предупреждением `stale` с кнопкой."""
        other = Path(self.tmp.name) / "другая-копия"
        self.outdated(other)
        before = self.entry().read_text(encoding="utf-8")
        self.assertFalse(autostart.refresh_if_outdated(ROOT))
        self.assertEqual(self.entry().read_text(encoding="utf-8"), before)
        self.assertTrue(autostart.status(ROOT)["stale"])

    def test_нечитаемая_запись_не_роняет_старт(self):
        """Файл в чужой кодировке — не повод не запуститься."""
        self.startup.mkdir(parents=True, exist_ok=True)
        self.entry().write_bytes(bytes([0xff, 0xfe, 0x00]) + b"chcp")
        self.assertFalse(autostart.refresh_if_outdated(ROOT))
        self.assertFalse(autostart.status(ROOT)["stale"] is None)


class UnsupportedPlatformTest(unittest.TestCase):
    """Где кнопки нет — показываем, что сделать руками, а не пустое место."""

    def setUp(self):
        # Подменяем саму платформу, а не признак поддержки: проверяем ровно то,
        # что человек с macOS или Linux увидит вместо кнопки
        patch = mock.patch("sys.platform", "darwin")
        patch.start()
        self.addCleanup(patch.stop)

    def test_на_macos_кнопки_нет_а_инструкция_есть(self):
        state = autostart.status(ROOT)
        self.assertFalse(state["supported"])
        self.assertIn("launchd", state["hint"])

    def test_на_linux_своя_инструкция(self):
        with mock.patch("sys.platform", "linux"):
            self.assertIn("systemd", autostart.status(ROOT)["hint"])

    def test_включение_отказывает_с_инструкцией(self):
        result = autostart.enable(ROOT)
        self.assertFalse(result["ok"])
        self.assertIn("launchd", result["error"])


class SettingsUiTest(unittest.TestCase):
    """Кнопка живёт в общих настройках: автозапуск — свойство машины."""

    def source(self) -> str:
        path = (Path(__file__).resolve().parent.parent / "frontend" / "src"
                / "components" / "SettingsModal.jsx")
        return path.read_text(encoding="utf-8")

    def test_есть_переключатель(self):
        text = self.source()
        self.assertIn("toggleAutostart", text, "автозапуск нечем включить")
        self.assertIn("Запускать при входе в систему", text)

    def test_устаревшая_запись_видна_и_чинится(self):
        """Молчаливая поломка: запись есть, но ведёт в другую копию."""
        text = self.source()
        self.assertIn("autostart.stale", text, "устаревшая запись не показывается")
        self.assertIn("Обновить запись", text, "починить её нечем")

    def test_раздел_телеграма_предупреждает_о_выключенном_автозапуске(self):
        """Там это важнее всего: без запущенного таскборда задачи из чата
        не заводятся, а телеграм хранит непрочитанное около суток."""
        text = self.source()
        block = text[text.index("tab === 'telegram'"):text.index("tab === 'tool'")]
        self.assertIn("autostart", block, "вкладка телеграма не знает про автозапуск")
        self.assertIn("Включить автозапуск", block, "включить его оттуда нечем")

    def test_без_кнопки_показана_инструкция(self):
        """На macOS и Linux вместо неработающей кнопки — что сделать руками."""
        text = self.source()
        # Ищем от самой подписи в разметке, а не от первого слова в файле:
        # оно встречается раньше в комментарии
        block = text[text.index("{label}>Автозапуск<"):text.index("{label}>Сервер<")]
        self.assertIn("autostart.hint", block,
                      "инструкция для платформы без кнопки не показывается")


if __name__ == "__main__":
    unittest.main()
