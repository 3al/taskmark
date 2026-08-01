"""Проверка обновлений: согласие, кэш, разбор манифеста, тихий провал.

Сеть здесь не трогается ни разу: `check_remote` принимает функцию загрузки
параметром, и тесты подставляют свою. Так проверяется главное — что запрос
вообще **не уходит**, пока пользователь не дал согласия.

Глобальный кэш подменяется временной папкой: `~/.taskboard` пользователя
тесты трогать не должны.
"""

import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from backend import updater, version

GOOD = {"version": "9.9.9", "tag": "v9.9.9", "date": "2026-08-01",
        "notes": "### Добавлено\n- что-то"}


class Base(unittest.TestCase):
    """Подменяет глобальный кэш временной папкой."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patches = [
            mock.patch.object(updater, "GLOBAL_DIR", root),
            mock.patch.object(updater, "CACHE_FILE", root / "update.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)


class TestParseManifest(Base):

    def test_нормальный_манифест(self):
        parsed = updater.parse_manifest(GOOD)
        self.assertEqual(parsed["version"], "9.9.9")
        self.assertEqual(parsed["tag"], "v9.9.9")

    def test_тег_достраивается_если_его_нет(self):
        parsed = updater.parse_manifest({"version": "1.2.3"})
        self.assertEqual(parsed["tag"], "v1.2.3")

    def test_лишние_поля_отбрасываются(self):
        parsed = updater.parse_manifest({"version": "1.2.3", "cmd": "rm -rf /"})
        self.assertNotIn("cmd", parsed)

    def test_чужой_ответ_это_ошибка(self):
        # Страница ошибки провайдера, заглушка, обрезанный json
        for bad in ([], "строка", {}, {"version": "не версия"}, {"version": 5}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    updater.parse_manifest(bad)


class TestConsent(Base):
    """Без согласия в сеть не ходим — главное свойство этой итерации."""

    def setUp(self):
        super().setUp()
        self.fetch = mock.Mock(return_value=updater.parse_manifest(GOOD))

    def test_режим_ask_не_ходит_в_сеть(self):
        updater.check_remote({"update_check": "ask"}, fetch=self.fetch)
        self.fetch.assert_not_called()

    def test_режим_off_не_ходит_в_сеть(self):
        updater.check_remote({"update_check": "off"}, fetch=self.fetch)
        self.fetch.assert_not_called()

    def test_режим_off_не_обходится_и_кнопкой(self):
        updater.check_remote({"update_check": "off"}, force=True, fetch=self.fetch)
        self.fetch.assert_not_called()

    def test_режим_manual_сам_не_ходит(self):
        # «Проверяю сам» — это ответ на вопрос, а не согласие на фоновые запросы
        updater.check_remote({"update_check": "manual"}, fetch=self.fetch)
        self.fetch.assert_not_called()

    def test_режим_manual_проверяется_кнопкой(self):
        updater.check_remote({"update_check": "manual"}, force=True, fetch=self.fetch)
        self.fetch.assert_called_once()

    def test_режим_auto_ходит(self):
        updater.check_remote({"update_check": "auto"}, fetch=self.fetch)
        self.fetch.assert_called_once()

    def test_кнопка_проверить_это_согласие(self):
        # Явное действие пользователя обходит режим ask
        updater.check_remote({"update_check": "ask"}, force=True, fetch=self.fetch)
        self.fetch.assert_called_once()

    def test_фоновая_проверка_молчит_без_согласия(self):
        with mock.patch.object(updater, "check_remote") as spy:
            updater.check_in_background({"update_check": "ask"})
            spy.assert_not_called()


class TestCache(Base):

    def test_свежий_кэш_экономит_запрос(self):
        updater.write_cache({"checked_at": time.time(), "latest": GOOD})
        fetch = mock.Mock()
        updater.check_remote({"update_check": "auto"}, fetch=fetch)
        fetch.assert_not_called()

    def test_протухший_кэш_обновляется(self):
        updater.write_cache({"checked_at": time.time() - updater.CHECK_INTERVAL - 1})
        fetch = mock.Mock(return_value=updater.parse_manifest(GOOD))
        updater.check_remote({"update_check": "auto"}, fetch=fetch)
        fetch.assert_called_once()

    def test_кнопка_обходит_свежий_кэш(self):
        updater.write_cache({"checked_at": time.time(), "latest": GOOD})
        fetch = mock.Mock(return_value=updater.parse_manifest(GOOD))
        updater.check_remote({"update_check": "auto"}, force=True, fetch=fetch)
        fetch.assert_called_once()

    def test_испорченный_кэш_не_роняет(self):
        updater.CACHE_FILE.write_text("{не json", encoding="utf-8")
        self.assertEqual(updater.read_cache(), {})


class TestSilentFailure(Base):
    """Офлайн и битый ответ ничего не ломают."""

    def _broken(self, exc):
        def fetch(url, *a, **kw):
            raise exc
        return fetch

    def test_офлайн_не_поднимает_исключение(self):
        cache = updater.check_remote(
            {"update_check": "auto"},
            fetch=self._broken(urllib.error.URLError("нет сети")))
        self.assertIn("error", cache)

    def test_прежние_сведения_переживают_ошибку(self):
        updater.write_cache({"checked_at": 0, "latest": updater.parse_manifest(GOOD)})
        cache = updater.check_remote(
            {"update_check": "auto"},
            fetch=self._broken(urllib.error.URLError("нет сети")))
        self.assertEqual(cache["latest"]["version"], "9.9.9")
        self.assertTrue(cache["error"])

    def test_мусор_вместо_json_это_тоже_тихий_провал(self):
        cache = updater.check_remote(
            {"update_check": "auto"},
            fetch=self._broken(ValueError("не json")))
        self.assertTrue(cache["error"])


class TestInstallKind(Base):

    def _root(self, with_git_dir: bool) -> Path:
        root = Path(self.tmp.name) / "install"
        root.mkdir(exist_ok=True)
        if with_git_dir:
            (root / ".git").mkdir(exist_ok=True)
        return root

    def test_репозиторий_и_git_в_path(self):
        with mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"):
            self.assertEqual(updater.install_kind(self._root(True)), "git")

    def test_репозиторий_без_бинарника_git(self):
        # Показывать команду, которую нечем выполнить, бессмысленно
        with mock.patch.object(updater.shutil, "which", return_value=None):
            self.assertEqual(updater.install_kind(self._root(True)), "nogit")

    def test_распакованный_архив(self):
        with mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"):
            self.assertEqual(updater.install_kind(self._root(False)), "plain")


class TestStatus(Base):

    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name) / "install"
        (self.root / ".git").mkdir(parents=True, exist_ok=True)

    def _status(self, latest_version: str) -> dict:
        updater.write_cache({
            "checked_at": time.time(),
            "latest": updater.parse_manifest({"version": latest_version}),
        })
        with mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"):
            return updater.status({"update_check": "auto"}, self.root)

    def test_новее_значит_доступно_обновление(self):
        self.assertTrue(self._status("999.0.0")["update_available"])

    def test_та_же_версия_не_повод_показывать_обновление(self):
        self.assertFalse(self._status(version.current())["update_available"])

    def test_версия_старее_нашей_не_предлагается(self):
        self.assertFalse(self._status("0.0.1")["update_available"])

    def test_команда_обновления_ведёт_на_тег(self):
        status = self._status("999.0.0")
        self.assertIn("v999.0.0", status["command"])
        self.assertIn("--ff-only", status["command"])

    def test_без_кэша_ничего_не_обещаем(self):
        with mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"):
            status = updater.status({"update_check": "ask"}, self.root)
        self.assertFalse(status["update_available"])
        self.assertIsNone(status["latest"])
        self.assertEqual(status["version"], version.current())

    def test_для_установки_без_git_команды_нет(self):
        updater.write_cache({
            "checked_at": time.time(),
            "latest": updater.parse_manifest({"version": "999.0.0"}),
        })
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir(exist_ok=True)
        status = updater.status({"update_check": "auto"}, plain)
        self.assertTrue(status["update_available"])
        self.assertEqual(status["command"], "")


class TestManifestUrl(Base):

    def test_адрес_берётся_из_конфига(self):
        cfg = {"release_manifest_url": "https://example.invalid/r.json"}
        self.assertEqual(updater.manifest_url(cfg), "https://example.invalid/r.json")

    def test_без_настройки_берётся_дефолт(self):
        self.assertTrue(updater.manifest_url({}).startswith("https://"))

    def test_адрес_виден_в_сводке(self):
        # Без адреса ошибка 404 неотличима от поломки инструмента
        root = Path(self.tmp.name)
        cfg = {"release_manifest_url": "https://example.invalid/r.json"}
        self.assertEqual(updater.status(cfg, root)["url"], "https://example.invalid/r.json")

    def test_запрос_уходит_по_адресу_из_конфига(self):
        fetch = mock.Mock(return_value=updater.parse_manifest(GOOD))
        cfg = {"update_check": "auto",
               "release_manifest_url": "https://example.invalid/r.json"}
        updater.check_remote(cfg, fetch=fetch)
        fetch.assert_called_once_with("https://example.invalid/r.json")


class TestCacheRoundTrip(Base):

    def test_запись_и_чтение(self):
        updater.write_cache({"checked_at": 1, "latest": GOOD})
        self.assertEqual(json.loads(updater.CACHE_FILE.read_text(encoding="utf-8"))
                         ["latest"]["version"], "9.9.9")
        self.assertEqual(updater.read_cache()["latest"]["version"], "9.9.9")


if __name__ == "__main__":
    unittest.main()
