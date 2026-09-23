"""Кнопка «назад» в окне помощи: история переходов между разделами (TASK-302).

Перешёл по ссылке в другой раздел — обратной дороги не было: прежний раздел
приходилось искать в меню и заново прокручивать до места. История переходов —
стек мест «раздел + высота прокрутки», как у браузера.

Правила стека проверяются настоящим node, если он есть: модуль чистый и
импортируется без сборки. Связь с окном — по исходнику, как в соседних тестах
фронтенда: JS-раннера для компонентов в проекте нет.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
TRAIL = FRONTEND / "helpTrail.js"
MODAL = FRONTEND / "components" / "HelpModal.jsx"

NODE = shutil.which("node")


def run_js(body: str):
    """Выполнить код над модулем истории и вернуть то, что он напечатал JSON-ом."""
    script = (f"import {{ visit, back }} from {json.dumps(TRAIL.as_uri())}\n"
              f"const out = (() => {{ {body} }})()\n"
              "console.log(JSON.stringify(out))\n")
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(NODE, "node не найден — правила истории не проверить")
class TrailRulesTest(unittest.TestCase):
    """Стек переходов: что в него попадает и что возвращает «назад»."""

    def test_back_returns_place_with_scroll(self) -> None:
        out = run_js("""
            let t = visit([], { section: 'board', scroll: 420 }, 'lifecycle')
            return back(t)
        """)
        self.assertEqual(out["place"], {"section": "board", "scroll": 420})
        self.assertEqual(out["trail"], [])

    def test_several_steps_back(self) -> None:
        out = run_js("""
            let t = visit([], { section: 'start', scroll: 0 }, 'board')
            t = visit(t, { section: 'board', scroll: 100 }, 'lifecycle')
            const first = back(t)
            const second = back(first.trail)
            return [first.place.section, second.place.section, second.trail.length]
        """)
        self.assertEqual(out, ["board", "start", 0])

    def test_same_section_is_not_a_step(self) -> None:
        out = run_js("return visit([], { section: 'board', scroll: 50 }, 'board')")
        self.assertEqual(out, [])

    def test_no_current_section_is_not_a_step(self) -> None:
        """Окно ещё не загрузило раздел — возвращаться некуда."""
        out = run_js("return visit([], { section: null, scroll: 0 }, 'board')")
        self.assertEqual(out, [])

    def test_empty_trail_gives_nothing(self) -> None:
        out = run_js("return back([])")
        self.assertIsNone(out["place"])
        self.assertEqual(out["trail"], [])

    def test_trail_is_not_mutated(self) -> None:
        """React сравнивает состояние по ссылке: правка на месте не перерисует окно."""
        out = run_js("""
            const t = [{ section: 'start', scroll: 0 }]
            visit(t, { section: 'board', scroll: 0 }, 'lifecycle')
            back(t)
            return t.length
        """)
        self.assertEqual(out, 1)


def modal() -> str:
    return MODAL.read_text(encoding="utf-8")


class ModalUsesTrailTest(unittest.TestCase):
    """Окно помощи ведёт историю на каждом переходе и показывает стрелку."""

    def test_every_transition_goes_through_history(self) -> None:
        src = modal()
        self.assertIn("from '../helpTrail'", src, "окно не пользуется историей переходов")
        # Ссылка в тексте, пункт меню, найденное место — один путь перехода
        self.assertNotRegex(src, r"setCurrent\((?:target|item\.id|group\.key)\)",
                            "переход в обход истории")
        self.assertGreaterEqual(len(re.findall(r"\bgo\(", src)), 3,
                                "не все переходы (ссылка, меню, поиск) идут через историю")

    def test_arrow_only_with_history(self) -> None:
        self.assertIn("trail.length > 0 &&", modal(), "стрелка видна без истории")

    def test_arrow_names_the_section(self) -> None:
        self.assertIn("`Назад: ${", modal(), "подсказка не называет раздел")

    def test_arrow_has_hover(self) -> None:
        src = modal()
        button = src[src.index("onClick={goBack}"):]
        button = button[:button.index("</button>")]
        self.assertIn("hover:bg-", button, "у стрелки нет фона при наведении")
        self.assertIn("hover:text-", button, "стрелка не светлеет при наведении")

    def test_back_restores_scroll(self) -> None:
        src = modal()
        self.assertIn("place.scroll", src, "«назад» не возвращает высоту прокрутки")
        self.assertIn("scrollTop = restore", src, "высота не восстанавливается после загрузки")

    def test_history_lives_while_window_is_open(self) -> None:
        src = modal()
        self.assertIn("useState([])", src)
        self.assertNotIn("localStorage", src, "история не должна переживать закрытие окна")


if __name__ == "__main__":
    unittest.main()
