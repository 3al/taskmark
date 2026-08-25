"""Подпись перевода со скобками внутри имени модели (TASK-188).

Скрипт собирает подпись как `скрипт ({agent})`, подставляя `--agent` как есть.
Имя вида `opencode (kimi-code/k3)` даёт **вложенные** скобки, а группа автора
в разборе их запрещала — строка переставала опознаваться как перевод и в UI
не красилась цветами статусов.

Содержимое скобок разбору не нужно: поля и так разделены ` · `, и разделитель
из класса исключён. Зеркала — `backend/notes.py` и `frontend/src/markdown.jsx`,
поэтому формулы проверяются вместе.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.notes import TRANSITION_RE, transition_note  # noqa: E402

MARKDOWN = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "markdown.jsx")

# Имя модели человек задаёт сам, и скобки в нём законны
NESTED = "скрипт (opencode (kimi-code/k3))"


class NestedParenthesesTest(unittest.TestCase):
    """Разбор перехода не разбирает содержимое подписи, а только её границы."""

    def test_nested_parentheses_are_recognised(self) -> None:
        line = transition_note(NESTED, "Development", "Testing")

        m = TRANSITION_RE.match(line)

        self.assertIsNotNone(m, f"строка не опознана как перевод: {line}")
        self.assertEqual(NESTED, m.group("author"))
        self.assertEqual(("Development", "Testing"), (m.group("from"), m.group("to")))

    def test_plain_authors_still_work(self) -> None:
        for author in ("доска", "скрипт", "скрипт (Claude Opus 5)"):
            line = transition_note(author, "Development", "Testing")
            self.assertIsNotNone(TRANSITION_RE.match(line), line)

    def test_lifecycle_event_is_still_not_a_transition(self) -> None:
        """Ослабление не должно принимать за перевод соседние события."""
        for text in ("пауза: ждём ответ контрагента", "тип: bug (было feature)"):
            line = f"- **2026-08-26 00:00** · {NESTED} · {text}"
            self.assertIsNone(TRANSITION_RE.match(line), line)

    def test_plain_note_is_still_not_a_transition(self) -> None:
        line = f"- **2026-08-26 00:00** · {NESTED} · корень бага: роль читалась до темы"
        self.assertIsNone(TRANSITION_RE.match(line), line)


class MirrorTest(unittest.TestCase):
    """Фронтенд повторяет ту же проверку — формулы обязаны совпадать."""

    def setUp(self) -> None:
        self.text = MARKDOWN.read_text(encoding="utf-8")

    def move_source(self) -> re.Pattern[str]:
        """`MOVE_SOURCE` из `markdown.jsx`, переведённая в питоновскую регулярку."""
        m = re.search(r"const MOVE_SOURCE = /(.+)/\s*$", self.text, flags=re.M)
        self.assertIsNotNone(m, "MOVE_SOURCE не найдена в markdown.jsx")
        return re.compile(m.group(1))

    def test_front_recognises_nested_parentheses(self) -> None:
        tail = f" · {NESTED} · "
        self.assertIsNotNone(self.move_source().search(tail), tail)

    def test_front_still_rejects_a_plain_author(self) -> None:
        """Подпись обычного комментария переводом не считается и на фронте."""
        self.assertIsNone(self.move_source().search(" · Claude Opus 5 · "))

    def test_both_sides_forbid_the_separator_inside(self) -> None:
        """Границей подписи остаётся ` · ` — иначе разбор съест соседнее поле."""
        self.assertIn("[^·]", self.move_source().pattern)
        self.assertIn("[^·]", TRANSITION_RE.pattern)


if __name__ == "__main__":
    unittest.main()
