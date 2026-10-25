"""Тесты компонентов фронтенда (Vitest + jsdom) — часть общего прогона.

Компоненты проверяются своим раннером во `taskboard/frontend` (`npm test`), а
этот модуль запускает его из `unittest discover`: упавший тест компонента
роняет общий прогон, как любой другой. Нет `node` или зависимостей фронта —
тесты компонентов пропускаются с причиной, остальное идёт как обычно.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
DIST = FRONTEND / "dist"

NODE = shutil.which("node")
# Запуск мимо npm: на Windows `npm` — это .cmd, и без shell его не вызвать
VITEST = Path("node_modules") / "vitest" / "vitest.mjs"


def skip_reason(frontend: Path, node: str | None) -> str | None:
    """Почему тесты компонентов не запустить; None — запускать можно."""
    if not node:
        return "node не найден — тесты компонентов пропущены"
    if not (frontend / VITEST).is_file():
        return ("нет node_modules фронта — выполните npm install в taskboard/frontend, "
                "тесты компонентов пропущены")
    return None


def run_vitest(frontend: Path, fixtures: bool = False) -> subprocess.CompletedProcess:
    """Прогнать Vitest один раз (без режима наблюдения) и вернуть результат.

    `fixtures` — вместо тестов компонентов заведомо падающий тест
    (`frontend/test-fixtures`): им проверяется, что падение доходит до прогона.
    """
    assert NODE is not None
    env = {**os.environ, "VITEST_FIXTURES": "1"} if fixtures else None
    return subprocess.run([NODE, str(frontend / VITEST), "run"], env=env,
                          cwd=str(frontend), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _plain(text: str) -> str:
    """Вывод без цветовых escape-последовательностей."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class SkipReasonTest(unittest.TestCase):
    """Когда тесты компонентов пропускаются и что об этом говорится."""

    def test_no_node(self) -> None:
        self.assertIn("node не найден", skip_reason(FRONTEND, None) or "")

    def test_no_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reason = skip_reason(Path(tmp), "node")
        self.assertIn("npm install", reason or "")

    def test_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / VITEST
            runner.parent.mkdir(parents=True)
            runner.write_text("", encoding="utf-8")
            self.assertIsNone(skip_reason(Path(tmp), "node"))


REASON = skip_reason(FRONTEND, NODE)


@unittest.skipIf(REASON, REASON or "")
class ComponentTestsTest(unittest.TestCase):
    """Прогон тестов компонентов из общего прогона."""

    def test_components_pass(self) -> None:
        done = run_vitest(FRONTEND)
        self.assertEqual(done.returncode, 0,
                         "упали тесты компонентов (npm test в taskboard/frontend):\n"
                         + _plain(done.stdout + done.stderr)[-4000:])

    def test_failure_reaches_runner(self) -> None:
        """Падение теста компонента видно: код возврата и имя теста в выводе."""
        done = run_vitest(FRONTEND, fixtures=True)
        self.assertNotEqual(done.returncode, 0, "падающий тест компонента не уронил прогон")
        self.assertIn("заведомо падает", _plain(done.stdout + done.stderr))


@unittest.skipUnless((DIST / "index.html").is_file(), "фронтенд не собран")
class BundleTest(unittest.TestCase):
    """Тестовые библиотеки в собранный бандл не попадают."""

    def test_no_test_code_in_dist(self) -> None:
        for path in sorted((DIST / "assets").glob("*.js")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in ("@testing-library", "vitest", "jsdom"):
                self.assertNotIn(marker, text, f"{path.name}: в бандле есть «{marker}»")

    def test_tests_not_scanned_for_styles(self) -> None:
        """Слова из тестов не становятся классами CSS сборки."""
        config = (FRONTEND / "tailwind.config.js").read_text(encoding="utf-8")
        self.assertIn("'!./src/**/*.test.{js,jsx}'", config)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
