"""Тесты команды запуска скриптов в текстах поставки (TASK-052).

Фидбек от пользователя, развернувшего доску у себя: у него Python из Microsoft
Store, а он **не ставит лаунчер `py`**. Правила и скиллы велели агенту звать
именно `py tasks/set_status.py`, и тот спотыкался на `CommandNotFoundException`,
хотя рабочая команда была рядом — `python`.

Поэтому ни один текст поставки не имеет права предлагать одну команду как
единственную: рядом с `py` всегда должен стоять запасной вариант. Проверяем
шаблоны (их получают пользователи) и документацию.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
AGENTIC = TEMPLATES / "agentic"

# Тексты, которые читает агент в чужом проекте
AGENT_TEXTS = [AGENTIC / "rules_section.md"] + sorted(
    AGENTIC.glob(".claude/skills/*/SKILL.md"))
# Тексты, которые читает человек
HUMAN_TEXTS = [ROOT / "README.md"] + sorted((ROOT / "docs" / "help").glob("*.md"))

# Строка запуска через лаунчер Windows: «py tasks/...», «py taskboard.py»,
# «py C:\путь\taskboard.py»
_PY_CALL = re.compile(r"^\s*py\s+(\S*\.py\b|[A-Za-z]:\\)", re.MULTILINE)


def _files_calling_py(paths: list[Path]) -> list[Path]:
    return [p for p in paths if _PY_CALL.search(p.read_text(encoding="utf-8"))]


class PythonCommandFallbackTest(unittest.TestCase):
    """Где предлагается `py`, там же должен быть путь для системы без лаунчера."""

    def _assert_has_fallback(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertRegex(
            text, r"(?mi)^\s*python\s+\S*\.py\b|лаунчер",
            f"{path.name}: предлагает только `py` — на Windows из Microsoft Store "
            f"такой команды нет, агент упрётся в CommandNotFoundException")

    def test_agent_texts_have_fallback(self) -> None:
        targets = _files_calling_py(AGENT_TEXTS)
        self.assertTrue(targets, "не нашли текстов с командой `py` — проверка бессмысленна")
        for path in targets:
            with self.subTest(path=path.name):
                self._assert_has_fallback(path)

    def test_human_texts_have_fallback(self) -> None:
        for path in _files_calling_py(HUMAN_TEXTS):
            with self.subTest(path=path.name):
                self._assert_has_fallback(path)

    def test_rules_explain_how_to_choose_interpreter(self) -> None:
        """Правила объясняют выбор команды один раз — дальше можно писать коротко."""
        rules = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        self.assertIn("лаунчер", rules,
                      "в правилах не сказано, что лаунчера `py` может не быть")
        self.assertRegex(rules, r"python3?\b", "не названы альтернативные команды")


class ScriptDocstringsTest(unittest.TestCase):
    """Скрипты `tasks/*.py` печатают примеры вызова — они тоже поставка."""

    def test_scripts_show_alternative_command(self) -> None:
        for name in ("create_task.py", "set_status.py"):
            path = TEMPLATES / "tasks" / name
            with self.subTest(script=name):
                head = path.read_text(encoding="utf-8")[:2000]
                self.assertRegex(
                    head, r"(?m)^\s*python\s+tasks/|лаунчер",
                    f"{name}: в примерах только `py` — на системе без лаунчера они не работают")


if __name__ == "__main__":
    unittest.main()
