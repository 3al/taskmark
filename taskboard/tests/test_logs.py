"""Логи читаются в исходной кодировке и получают подходящее представление."""

import codecs
import unittest
from pathlib import Path

from backend.config import DEFAULTS
from backend.log_files import decode_log_bytes, log_kind
from backend.scaffold import render_rules


FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


class LogApiTest(unittest.TestCase):
    def test_utf8_log_is_read_as_text(self) -> None:
        self.assertEqual(decode_log_bytes("проверка прошла".encode("utf-8")),
                         "проверка прошла")
        self.assertEqual(log_kind("run.log"), "text")

    def test_powershell_utf16_log_is_read_without_garbled_text(self) -> None:
        raw = codecs.BOM_UTF16_LE + "тесты зелёные".encode("utf-16-le")

        self.assertEqual(decode_log_bytes(raw), "тесты зелёные")

    def test_terminal_control_sequences_are_removed(self) -> None:
        self.assertEqual(decode_log_bytes(b"\x1b[31mFAIL\x1b[0m\nplain"),
                         "FAIL\nplain")

    def test_markdown_log_requests_formatted_view(self) -> None:
        self.assertEqual(log_kind("discussion.md"), "markdown")

    def test_api_uses_log_decoder_and_returns_kind(self) -> None:
        source = (FRONTEND.parent.parent / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn("read_log_text(path)", source)
        self.assertIn('"kind": log_kind(name)', source)


class LogViewerTest(unittest.TestCase):
    def test_viewer_supports_markdown_and_readable_plain_text(self) -> None:
        source = (FRONTEND / "components" / "LogsPanel.jsx").read_text(encoding="utf-8")

        self.assertIn("ReactMarkdown", source)
        self.assertIn("content?.kind === 'markdown'", source)
        self.assertIn("text-[13px]", source)

    def test_files_show_a_newest_first_chronology(self) -> None:
        source = (FRONTEND / "components" / "LogsPanel.jsx").read_text(encoding="utf-8")

        self.assertIn("sort((a, b) => b.mtime - a.mtime)", source)
        self.assertIn("formatLogDate(f.mtime)", source)

    def test_window_has_stable_dimensions(self) -> None:
        source = (FRONTEND / "components" / "LogsPanel.jsx").read_text(encoding="utf-8")

        self.assertIn("max-w-6xl", source)
        self.assertIn("h-[85vh]", source)

    def test_brainstorm_logs_are_coloured_in_the_list(self) -> None:
        source = (FRONTEND / "components" / "LogsPanel.jsx").read_text(encoding="utf-8")

        self.assertIn("isBrainstormLog(f.name)", source)
        self.assertIn("border-violet-400", source)
        self.assertIn("text-violet-200", source)
        self.assertIn("bg-violet-500/35", source)


class LogDeliveryRulesTest(unittest.TestCase):
    def test_shipped_rules_define_readable_agent_log_contract(self) -> None:
        source = render_rules({**DEFAULTS})

        self.assertIn("## Логи работы агента", source)
        self.assertIn("`tasks/logs/`", source)
        self.assertIn("Обязанности вести лог нет", source)
        self.assertIn("не создавай файл", source.lower())
        self.assertIn("UTF-8", source)
        self.assertIn("Markdown", source)
        self.assertIn("временный", source.lower())
        self.assertIn("не сохраняй", source.lower())

    def test_log_is_distinct_from_optional_project_knowledge(self) -> None:
        with_vault = render_rules({**DEFAULTS, "vault": True})
        without_vault = render_rules({**DEFAULTS, "vault": False})

        self.assertIn("Лог не заменяет Knowledge Vault", with_vault)
        self.assertIn("актуальное переиспользуемое знание", with_vault)
        self.assertNotIn("Knowledge Vault", without_vault)


if __name__ == "__main__":
    unittest.main()
