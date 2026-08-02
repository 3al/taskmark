"""Проверка обновлений: согласие, кэш, разбор манифеста, тихий провал.

Сеть здесь не трогается ни разу: `check_remote` принимает функцию загрузки
параметром, и тесты подставляют свою. Так проверяется главное — что запрос
вообще **не уходит**, пока пользователь не дал согласия.

Глобальный кэш подменяется временной папкой: `~/.taskboard` пользователя
тесты трогать не должны.
"""

import json
import tempfile
import threading
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


class TestPeriodicCheck(Base):
    """Режим `auto` обязан проверять сам, а не при следующем запуске (TASK-125).

    Проверка звалась только из `startup` приложения, и таймера не было вовсе:
    `CHECK_INTERVAL` — троттлинг («не чаще суток»), а не расписание. Инструмент
    локальный, его держат запущенным днями — то есть у выбравшего «автоматически»
    проверка не случалась никогда, и релиз он пропускал.
    """

    def test_фоновая_проверка_повторяется(self):
        calls = []
        stop = updater.start_periodic_check(
            {"update_check": "auto"},
            interval=0.01,
            check=lambda cfg: calls.append(1),
        )
        try:
            deadline = time.time() + 2
            while len(calls) < 3 and time.time() < deadline:
                time.sleep(0.01)
        finally:
            stop()
        self.assertGreaterEqual(len(calls), 3, "проверка не повторяется по таймеру")

    def test_без_согласия_поток_не_запускается(self):
        for mode in ("ask", "manual", "off"):
            with self.subTest(mode=mode):
                calls = []
                stop = updater.start_periodic_check(
                    {"update_check": mode}, interval=0.01,
                    check=lambda cfg: calls.append(1))
                time.sleep(0.05)
                stop()
                self.assertEqual(calls, [], f"в режиме {mode} ходили в сеть сами")

    def test_остановка_срабатывает(self):
        calls = []
        stop = updater.start_periodic_check(
            {"update_check": "auto"}, interval=0.01,
            check=lambda cfg: calls.append(1))
        time.sleep(0.05)
        stop()
        after_stop = len(calls)
        time.sleep(0.05)
        self.assertEqual(len(calls), after_stop, "поток продолжает работу после остановки")

    def test_поток_демон(self):
        """Поток не должен держать выход процесса и мешать перезапуску из UI."""
        stop = updater.start_periodic_check({"update_check": "auto"}, interval=10)
        try:
            names = [t.name for t in threading.enumerate() if t.name == "update-check-loop"]
            self.assertTrue(names, "фонового потока проверки нет")
            thread = next(t for t in threading.enumerate() if t.name == "update-check-loop")
            self.assertTrue(thread.daemon, "поток не демон — процесс не завершится")
        finally:
            stop()

    def test_ошибка_проверки_не_убивает_цикл(self):
        calls = []

        def boom(cfg):
            calls.append(1)
            raise OSError("сеть отвалилась")

        stop = updater.start_periodic_check(
            {"update_check": "auto"}, interval=0.01, check=boom)
        try:
            deadline = time.time() + 2
            while len(calls) < 3 and time.time() < deadline:
                time.sleep(0.01)
        finally:
            stop()
        self.assertGreaterEqual(len(calls), 3, "цикл умер на первой сетевой ошибке")


class TestUpdateNotice(Base):
    """Найденная версия доезжает до открытой доски (TASK-126).

    Точка «доступна новая версия» читалась из кэша один раз при загрузке
    страницы: даже успешная фоновая проверка не зажигала её, пока доску не
    перезагрузят. Вместе с отсутствием периодической проверки это и делало
    авто-режим тихим — сначала некому проверить, а проверил, так некому сказать.
    """

    def test_новая_версия_поднимает_событие(self):
        sent = []
        updater.write_cache({"checked_at": 0, "latest": {"version": "0.0.1"}})
        updater.check_and_notify(
            {"update_check": "auto"}, Path(self.tmp.name), notify=sent.append,
            fetch=lambda url: updater.parse_manifest(GOOD))
        self.assertEqual(sent, ["update"], "о новой версии никому не сказали")

    def test_без_новой_версии_молчим(self):
        current = version.current()
        manifest = {**GOOD, "version": current}
        sent = []
        updater.check_and_notify(
            {"update_check": "auto"}, Path(self.tmp.name), notify=sent.append,
            fetch=lambda url: updater.parse_manifest(manifest))
        self.assertEqual(sent, [], "событие ушло, хотя новой версии нет")

    def test_повторная_находка_не_шумит(self):
        sent = []
        cfg = {"update_check": "auto"}
        fetch = lambda url: updater.parse_manifest(GOOD)  # noqa: E731
        updater.check_and_notify(cfg, Path(self.tmp.name), notify=sent.append, fetch=fetch)
        updater.check_and_notify(cfg, Path(self.tmp.name), notify=sent.append, fetch=fetch)
        self.assertEqual(sent, ["update"], "о той же версии сказали дважды")

    def test_сетевая_ошибка_не_поднимает_событие(self):
        def boom(url):
            raise OSError("сеть отвалилась")

        sent = []
        updater.check_and_notify({"update_check": "auto"}, Path(self.tmp.name),
                                 notify=sent.append, fetch=boom)
        self.assertEqual(sent, [])


class TestNoticeDelivery(Base):
    """Событие доезжает до доски: канал SSE и реакция фронта (TASK-126)."""

    def test_watcher_умеет_слать_произвольное_сообщение(self):
        from backend.watcher import TasksWatcher
        w = TasksWatcher()
        q = w.subscribe()
        try:
            w.send("update")
            self.assertEqual(q.get(timeout=1), "update")
        finally:
            w.unsubscribe(q)

    def test_сервер_подписывает_цикл_на_уведомление(self):
        src = (Path(__file__).resolve().parent.parent / "backend" / "app.py").read_text(
            encoding="utf-8")
        self.assertIn("check_and_notify", src, "цикл проверки не уведомляет доску")
        self.assertIn("watcher.send", src, "находка не уходит в канал событий")

    def test_фронт_реагирует_на_событие(self):
        src_dir = Path(__file__).resolve().parent.parent / "frontend" / "src"
        api_js = (src_dir / "api.js").read_text(encoding="utf-8")
        self.assertIn("'update'", api_js, "подписка не различает событие обновления")
        app_jsx = (src_dir / "App.jsx").read_text(encoding="utf-8")
        handler = app_jsx[app_jsx.index("subscribeChanges("):]
        handler = handler[:handler.index("[refresh])")]
        self.assertIn("updateStatus", handler, "доска не перечитывает статус обновления")
