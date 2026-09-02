"""Пропущенные выпуски видны до обновления, а не после.

Манифест описывает один выпуск, поэтому окно проверки показывало текст только
последней версии. Отставшему на несколько релизов это скрывало ровно то, по чему
он решает, обновляться ли: последний выпуск мог оказаться мелким патчем, а
возможность, ради которой стоило обновиться, — двумя версиями раньше (TASK-237).

Сеть здесь не трогается ни разу: загрузка передаётся параметром, и тесты
подставляют свою. Глобальный кэш подменяется временной папкой.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from backend import updater

MANIFEST = {"version": "1.19.3", "tag": "v1.19.3", "date": "2026-09-02",
            "notes": "### Исправлено\n- мелкий патч"}

# Три выпуска: между установленной 1.17.0 и свежей 1.19.3 человек пропустил два,
# и крупное изменение лежит как раз в среднем
REMOTE_CHANGELOG = """# Changelog

## [1.19.3] — 2026-09-02

### Исправлено

- мелкий патч

## [1.18.0] — 2026-08-29

### Добавлено

- крупная возможность, ради которой и обновляются

## [1.17.0] — 2026-08-27

### Изменено

- то, что у него уже есть
"""


class Base(unittest.TestCase):
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

    def check(self, manifest=MANIFEST, changelog=REMOTE_CHANGELOG, mode="manual"):
        """Прогнать проверку с подставленной загрузкой."""
        fetch = mock.Mock(return_value=updater.parse_manifest(manifest))
        fetch_text = mock.Mock(return_value=changelog)
        if isinstance(changelog, Exception):
            fetch_text = mock.Mock(side_effect=changelog)
        updater.check_remote({"update_check": mode}, force=True,
                             fetch=fetch, fetch_text=fetch_text)
        return fetch, fetch_text

    def status(self, current="1.17.0"):
        with mock.patch("backend.version.current", return_value=current):
            return updater.status({"update_check": "manual"}, Path(self.tmp.name))


class ChangelogUrlTest(unittest.TestCase):
    """Адрес выводится из адреса манифеста, а не заводится второй настройкой.

    Отдельный ключ заморозил бы ещё один дефолт в конфигах пользователя и
    разъехался бы с манифестом у того, кто сменил адрес.
    """

    def test_рядом_с_манифестом(self):
        cfg = {"release_manifest_url":
               "https://raw.githubusercontent.com/3al/taskmark/main/release.json"}
        self.assertEqual(
            updater.changelog_url(cfg),
            "https://raw.githubusercontent.com/3al/taskmark/main/CHANGELOG.md")

    def test_чужой_адрес_ведёт_за_собой(self):
        cfg = {"release_manifest_url": "https://example.org/зеркало/release.json"}
        self.assertEqual(updater.changelog_url(cfg),
                         "https://example.org/зеркало/CHANGELOG.md")


class MissedTest(Base):
    """Что окно показывает до обновления."""

    def test_показаны_все_пропущенные_а_не_только_последний(self):
        self.check()
        missed = self.status()["missed"]
        self.assertEqual([s["version"] for s in missed], ["1.19.3", "1.18.0"])

    def test_установленная_версия_не_показывается(self):
        """На ней человек и сидит — это не новость."""
        self.check()
        self.assertNotIn("1.17.0", [s["version"] for s in self.status()["missed"]])

    def test_крупное_изменение_видно_до_обновления(self):
        self.check()
        bodies = " ".join(s["body"] for s in self.status()["missed"])
        self.assertIn("крупная возможность", bodies)

    def test_отставшему_на_один_выпуск_показан_он_один(self):
        self.check()
        self.assertEqual([s["version"] for s in self.status("1.18.0")["missed"]],
                         ["1.19.3"])

    def test_после_обновления_показывать_нечего(self):
        """Кэш прежний, а версия уже свежая — список пуст, а не «всё подряд»."""
        self.check()
        self.assertEqual(self.status("1.19.3")["missed"], [])

    def test_сколько_всего_пропущено_названо_числом(self):
        self.check()
        state = self.status()
        self.assertEqual(state["missed_total"], 2)


class SoftFailureTest(Base):
    """Второй запрос не делает хрупким то, что работало."""

    def test_провал_changelog_не_ломает_проверку(self):
        self.check(changelog=urllib.error.URLError("нет сети"))
        state = self.status()
        self.assertTrue(state["update_available"], "обновление перестало находиться")
        self.assertEqual(state["latest"]["version"], "1.19.3")

    def test_без_changelog_остаётся_текст_последней_версии(self):
        """Показать хоть что-то лучше, чем пустое окно."""
        self.check(changelog=urllib.error.URLError("нет сети"))
        state = self.status()
        self.assertEqual(state["missed"], [])
        self.assertIn("мелкий патч", state["latest"]["notes"])

    def test_слишком_большой_ответ_не_кладётся_в_кэш(self):
        self.check(changelog="ц" * (updater.MAX_MANIFEST_BYTES + 1))
        self.assertEqual(self.status()["missed"], [])


class ConsentTest(Base):
    """Гейт согласия общий: второй запрос ходит там же, где первый."""

    def test_режим_off_не_пускает_и_за_changelog(self):
        fetch, fetch_text = self.check(mode="off")
        fetch.assert_not_called()
        fetch_text.assert_not_called()

    def test_без_манифеста_за_changelog_не_идём(self):
        """Манифест не пришёл — идти за текстами незачем."""
        fetch = mock.Mock(side_effect=urllib.error.URLError("нет сети"))
        fetch_text = mock.Mock(return_value=REMOTE_CHANGELOG)
        updater.check_remote({"update_check": "manual"}, force=True,
                             fetch=fetch, fetch_text=fetch_text)
        fetch_text.assert_not_called()


class ModalTest(unittest.TestCase):
    """Окно показывает пропущенное до обновления, а не только после."""

    def source(self) -> str:
        path = (Path(__file__).resolve().parent.parent / "frontend" / "src"
                / "components" / "UpdateModal.jsx")
        return path.read_text(encoding="utf-8")

    def test_окно_знает_про_пропущенные(self):
        self.assertIn("missed", self.source(),
                      "окно не показывает пропущенные выпуски до обновления")

    def test_сколько_ещё_осталось_сказано(self):
        text = self.source()
        self.assertIn("missed_total", text,
                      "число пропущенных выпусков не показывается")


if __name__ == "__main__":
    unittest.main()
