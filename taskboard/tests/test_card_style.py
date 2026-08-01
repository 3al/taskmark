"""Размеры превью задачи настраиваются числами, но в границах (TASK-097).

«Чтобы больше влезало» — это про конкретную высоту колонки, и у каждого она
своя, поэтому величины числовые. Но свобода настройки не должна доходить до
возможности сломать доску: за границами диапазона карточка разваливается,
и границы проверяет бэкенд, а не только форма.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import (CARD_LIMITS, DEFAULTS, card_style,  # noqa: E402
                            validate_card_style)

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
CARD = SRC / "components" / "TaskCard.jsx"
APP_JSX = SRC / "App.jsx"
SETTINGS = SRC / "components" / "SettingsModal.jsx"
APP_PY = Path(__file__).resolve().parent.parent / "backend" / "app.py"


class DefaultsTest(unittest.TestCase):
    def test_every_limited_key_has_a_default(self) -> None:
        for key in CARD_LIMITS:
            self.assertIn(key, DEFAULTS, f"{key} без значения по умолчанию")

    def test_defaults_are_inside_their_limits(self) -> None:
        for key, (low, high) in CARD_LIMITS.items():
            self.assertTrue(low <= DEFAULTS[key] <= high,
                            f"дефолт {key}={DEFAULTS[key]} вне границ {low}–{high}")

    def test_card_style_falls_back_to_defaults(self) -> None:
        """Ключа в конфиге нет — берётся поставка, а не пустота."""
        style = card_style({"card_title_size": 16})

        self.assertEqual(16, style["card_title_size"])
        self.assertEqual(DEFAULTS["card_title_lines"], style["card_title_lines"])
        self.assertEqual(DEFAULTS["card_meta_size"], style["card_meta_size"])


class ValidationTest(unittest.TestCase):
    def test_value_inside_limits_passes(self) -> None:
        updates, errors = validate_card_style({"card_title_size": 16})

        self.assertEqual([], errors)
        self.assertEqual(16, updates["card_title_size"])

    def test_form_strings_become_numbers(self) -> None:
        """Поле формы шлёт «14» — это то же число, а не повод для отказа."""
        updates, errors = validate_card_style({"card_title_lines": "4"})

        self.assertEqual([], errors)
        self.assertEqual(4, updates["card_title_lines"])

    def test_value_above_limit_is_refused(self) -> None:
        _, errors = validate_card_style({"card_title_size": 40})

        self.assertTrue(errors)
        self.assertIn("card_title_size", errors[0])

    def test_value_below_limit_is_refused(self) -> None:
        _, errors = validate_card_style({"card_title_lines": 0})

        self.assertTrue(errors)

    def test_error_names_the_allowed_range(self) -> None:
        low, high = CARD_LIMITS["card_meta_size"]
        _, errors = validate_card_style({"card_meta_size": 99})

        self.assertIn(str(low), errors[0])
        self.assertIn(str(high), errors[0])

    def test_garbage_is_refused(self) -> None:
        _, errors = validate_card_style({"card_title_size": "много"})

        self.assertTrue(errors)

    def test_booleans_are_not_numbers(self) -> None:
        """True — это не «1 строка»: такое приходит только по ошибке."""
        _, errors = validate_card_style({"card_title_lines": True})

        self.assertTrue(errors)

    def test_other_keys_are_untouched(self) -> None:
        updates, errors = validate_card_style({"port": "не число"})

        self.assertEqual([], errors)
        self.assertEqual("не число", updates["port"])


class ApiTest(unittest.TestCase):
    """Контракт с фронтом: значения — на доску, границы — в форму."""

    def setUp(self) -> None:
        self.text = APP_PY.read_text(encoding="utf-8")

    def test_board_config_carries_card_style(self) -> None:
        self.assertIn('"card_style": card_style(cfg)', self.text,
                      "доска не получает размеры превью")

    def test_config_endpoint_carries_limits(self) -> None:
        self.assertIn("card_limits", self.text,
                      "форме неоткуда узнать допустимые границы")

    def test_save_refuses_invalid_values(self) -> None:
        self.assertIn("validate_card_style(updates)", self.text,
                      "сохранение не проверяет границы — форма осталась одна")


class FrontendTest(unittest.TestCase):
    """Числа доезжают до карточки, а форма ограничивает ввод границами."""

    def test_card_uses_css_variables(self) -> None:
        text = CARD.read_text(encoding="utf-8")

        self.assertIn("--card-title-size", text)
        self.assertIn("--card-title-lines", text)
        self.assertIn("--card-meta-size", text)

    def test_card_no_longer_hardcodes_title_size(self) -> None:
        """Прежние text-base и line-clamp-2 на заголовке — это и был хардкод."""
        text = CARD.read_text(encoding="utf-8")

        self.assertNotIn("text-base text-zinc-300/90", text)
        self.assertNotIn("line-clamp-2\" title={task.title}", text)

    def test_app_applies_variables_from_config(self) -> None:
        text = APP_JSX.read_text(encoding="utf-8")

        self.assertIn("card_style", text)
        self.assertIn("setProperty('--card-title-size'", text)

    def test_settings_limit_inputs_by_backend_ranges(self) -> None:
        text = SETTINGS.read_text(encoding="utf-8")

        self.assertIn("card_limits", text, "форма ограничивает поля своими числами")
        self.assertIn("card_title_size", text)
        self.assertIn("min={low}", text)
        self.assertIn("max={high}", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
