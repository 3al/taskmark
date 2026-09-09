"""Единый визуальный язык исходящих сообщений Telegram."""

import unittest

from backend import telegram_messages


class CardTest(unittest.TestCase):
    def test_карточка_даёт_заголовок_задачу_поля_и_адресатов(self):
        text = telegram_messages.card(
            "✅", "Задача создана",
            task_id="TASK-264", task_title="Проверить отчёт",
            fields=[("Проект", "Первый"), ("Статус", "Бэклог")],
            mentions=["@kostya", "@petya"],
        )

        self.assertTrue(text.startswith("✅ <b>Задача создана</b>"), text)
        self.assertIn("<code>TASK-264</code> · Проверить отчёт", text)
        self.assertIn("<b>Проект:</b> Первый", text)
        self.assertIn("<b>Статус:</b> Бэклог", text)
        self.assertTrue(text.endswith("@kostya @petya"), text)

    def test_динамические_значения_экранируются(self):
        text = telegram_messages.card(
            "⚠️", "Не <создано>", body="Ошибка & <script>",
            task_id="<TASK>", task_title="A & B",
            fields=[("Проект", "<Первый>")], mentions=["@a&b"],
        )

        self.assertNotIn("<script>", text)
        self.assertIn("Не &lt;создано&gt;", text)
        self.assertIn("Ошибка &amp; &lt;script&gt;", text)
        self.assertIn("&lt;TASK&gt;", text)
        self.assertIn("A &amp; B", text)
        self.assertIn("&lt;Первый&gt;", text)
        self.assertIn("@a&amp;b", text)

    def test_длинные_значения_укладываются_в_запас_до_лимита_telegram(self):
        hostile = "<&" * 5000
        text = telegram_messages.card(
            "⚠️", hostile, body=hostile,
            task_id=hostile, task_title=hostile,
            fields=[("Поле", hostile), ("Ещё", hostile)],
            mentions=[hostile],
        )

        self.assertLessEqual(len(text), telegram_messages.MESSAGE_LIMIT)
        self.assertTrue(text.endswith("…"), text[-50:])


if __name__ == "__main__":
    unittest.main()
