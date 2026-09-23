"""Цвета ролей в просмотрщике протокола брейншторма (TASK-076).

Протокол брейншторма — длинная простыня из ответов трёх агентов по раундам.
Скиллы уже пишут его по строгому формату (сессия, раунды, роли заголовками),
и на нём держится разметка: ответ каждой роли выделяется своим цветом.

Трудность в том, что ответ агента — свободный Markdown со своими заголовками
`##` / `###`: граница блока роли — следующая роль, раунд или сессия, а не
следующий заголовок. Разбор проверяется настоящим node, если он есть; связь с
окном — по исходнику, как в соседних тестах фронтенда.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
MODULE = FRONTEND / "brainstormLog.js"
PANEL = FRONTEND / "components" / "LogsPanel.jsx"

NODE = shutil.which("node")

TEAM_LOG = """# Brainstorm Team Log: TASK-001 — Пример

---

# Сессия: 2026-09-14 23:39:05

Модель: Claude Opus 5
Раунды: 2

## Раунд 1: Исследование и предложения

### Архитектор

**Подход:** слой спецификаций.

## Решения по вопросам

### 1. Место

текст архитектора

### Прагматик

текст прагматика

```
### Архитектор
в блоке кода
```

### Оппонент — наблюдения

текст оппонента

## Раунд 2: Критика

### Оппонент — критика предложений

критика

---

# Сессия: 2026-09-15 10:00:00

## Раунд 1: Снова

### Архитектор

второй заход
"""

SIMPLE_LOG = """# Brainstorm Log: TASK-002 — Пример

---

# Сессия: 2026-09-14 23:39:05

Модель: Claude Opus 5

## Архитектор

а

## Прагматик

п

## Критик

к
"""


def segments(text: str) -> list[dict]:
    script = (f"import {{ roleSegments, segmentSource }} from {json.dumps(MODULE.as_uri())}\n"
              f"const text = {json.dumps(text)}\n"
              "const out = roleSegments(text).map((s) => ({ ...s, source: segmentSource(s) }))\n"
              "console.log(JSON.stringify(out))\n")
    done = subprocess.run([NODE or "node", "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def role_text(segs: list[dict], role: str) -> str:
    return "\n".join(s["text"] for s in segs if s["role"] == role)


@unittest.skipUnless(NODE, "node не найден — разбор протокола не проверить")
class RoleSegmentsTest(unittest.TestCase):
    def test_each_role_has_its_color_key(self) -> None:
        segs = segments(TEAM_LOG)
        self.assertIn("текст архитектора", role_text(segs, "architect"))
        self.assertIn("текст прагматика", role_text(segs, "pragmatist"))
        self.assertIn("текст оппонента", role_text(segs, "judge"))

    def test_same_role_same_key_across_rounds_and_sessions(self) -> None:
        segs = segments(TEAM_LOG)
        self.assertIn("критика", role_text(segs, "judge"))
        self.assertIn("второй заход", role_text(segs, "architect"))

    def test_critic_is_the_same_role_as_opponent(self) -> None:
        segs = segments(SIMPLE_LOG)
        self.assertEqual([s["role"] for s in segs if s["role"]],
                         ["architect", "pragmatist", "judge"])

    def test_headings_inside_answer_keep_the_block(self) -> None:
        segs = segments(TEAM_LOG)
        architect = [s for s in segs if s["role"] == "architect"][0]["text"]
        self.assertIn("## Решения по вопросам", architect)
        self.assertIn("### 1. Место", architect)
        self.assertIn("текст архитектора", architect)

    def test_service_parts_have_no_role(self) -> None:
        segs = segments(TEAM_LOG)
        plain = "\n".join(s["text"] for s in segs if s["role"] is None)
        for part in ("# Brainstorm Team Log", "# Сессия: 2026-09-14", "Модель: Claude Opus 5",
                     "## Раунд 1: Исследование", "## Раунд 2: Критика", "# Сессия: 2026-09-15"):
            with self.subTest(part=part):
                self.assertIn(part, plain)
        self.assertNotIn("---", role_text(segs, "judge"),
                         "разделитель сессий попал в ответ роли")

    def test_role_heading_in_code_is_not_a_role(self) -> None:
        segs = segments(TEAM_LOG)
        self.assertIn("в блоке кода", role_text(segs, "pragmatist"))

    def test_line_numbers_survive_the_split(self) -> None:
        """Поиск ведёт на место по номеру строки — у куска он должен совпадать с файлом."""
        lines = TEAM_LOG.split("\n")
        for seg in segments(TEAM_LOG):
            source = seg["source"].split("\n")
            with self.subTest(start=seg["start"]):
                self.assertEqual(source[seg["start"] - 1], lines[seg["start"] - 1])

    def test_nothing_is_lost(self) -> None:
        segs = segments(TEAM_LOG)
        self.assertEqual("\n".join(s["text"] for s in segs), TEAM_LOG)


def panel() -> str:
    return PANEL.read_text(encoding="utf-8")


class PanelColorsTest(unittest.TestCase):
    def test_only_brainstorm_logs_are_colored(self) -> None:
        src = panel()
        self.assertIn("from '../brainstormLog'", src)
        self.assertRegex(src, r"isBrainstormLog\(current\)[^\n]*roleSegments\(",
                         "раскраска не ограничена протоколами брейншторма")

    def test_each_role_has_a_style(self) -> None:
        src = panel()
        for role in ("architect", "pragmatist", "judge"):
            with self.subTest(role=role):
                self.assertRegex(src, rf"{role}:\s*'[^']*border-l-[^']*bg-",
                                 f"у роли {role} нет полосы и фона")


if __name__ == "__main__":
    unittest.main()
