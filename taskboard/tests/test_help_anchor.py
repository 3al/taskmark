"""Якоря подразделов справки: ссылка `05-agentic.md#заголовок` и кнопки «подробнее».

Окно помощи прокручивает к заголовку, якорь которого совпал со ссылкой. Якорь
строится из заголовка по правилу GitHub, поэтому одна ссылка ведёт к одному
месту и в окне, и в файле на GitHub. Битый якорь не ломает переход — раздел
открывается сверху, — но и к месту не ведёт; тесты держат, что все якоря в
справке и в интерфейсе указывают на существующие заголовки.

Правила якоря проверяются настоящим node: модуль чистый и импортируется без
сборки. Связь с окном — по исходнику, как в соседних тестах фронтенда.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import help_docs  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
ANCHOR = FRONTEND / "helpAnchor.js"
MODAL = FRONTEND / "components" / "HelpModal.jsx"

NODE = shutil.which("node")

SECTION_FILE = re.compile(r"^(?:\./)?(?:\d+-)?([a-z0-9-]+)\.md$")


def run_js(body: str):
    """Выполнить код над модулем якорей и вернуть то, что он напечатал JSON-ом."""
    script = (f"import {{ slugify, parseHelpRef }} from {json.dumps(ANCHOR.as_uri())}\n"
              f"const out = (() => {{ {body} }})()\n"
              "console.log(JSON.stringify(out))\n")
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def _strip_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _heading_text(raw: str) -> str:
    """Текст заголовка без разметки — тот, что окно видит в отрисованном заголовке."""
    raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
    return re.sub(r"[`*_]", "", raw).strip()


def _headings() -> dict[str, list[str]]:
    """id раздела → тексты его заголовков."""
    out = {}
    for path in sorted(help_docs.DOCS_DIR.glob("*.md")):
        m = SECTION_FILE.match(path.name)
        text = _strip_code(path.read_text(encoding="utf-8"))
        out[m.group(1)] = [_heading_text(h) for h in
                           re.findall(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.M)]
    return out


@unittest.skipUnless(NODE, "node не найден — правила якоря не проверить")
class SlugTest(unittest.TestCase):
    """Якорь из заголовка — как у GitHub."""

    def test_github_rule(self) -> None:
        out = run_js("""return [
            slugify('Сценарии до кода'),
            slugify('Обновление и правки'),
            slugify('Knowledge Vault'),
            slugify('Хуки в Codex'),
            slugify('Красный баннер — критично'),
            slugify('Переход в новую сессию: буфер'),
        ]""")
        self.assertEqual(out, ["сценарии-до-кода", "обновление-и-правки", "knowledge-vault",
                               "хуки-в-codex", "красный-баннер--критично",
                               "переход-в-новую-сессию-буфер"])

    def test_ref_parts(self) -> None:
        out = run_js("""return [
            parseHelpRef('agentic#сценарии-до-кода'),
            parseHelpRef('agentic'),
            parseHelpRef('#обновление-и-правки'),
            parseHelpRef('05-agentic.md#%D1%81%D1%86'),
            parseHelpRef('agentic#%E0'),
        ]""")
        self.assertEqual(out, [
            {"section": "agentic", "anchor": "сценарии-до-кода"},
            {"section": "agentic", "anchor": ""},
            {"section": "", "anchor": "обновление-и-правки"},
            {"section": "05-agentic.md", "anchor": "сц"},
            {"section": "agentic", "anchor": "%e0"},
        ])


@unittest.skipUnless(NODE, "node не найден — якоря справки не сверить")
class AnchorsResolveTest(unittest.TestCase):
    """Каждый якорь ведёт к существующему заголовку своего раздела."""

    @classmethod
    def setUpClass(cls) -> None:
        heads = _headings()
        slugs = run_js(f"const h = {json.dumps(heads, ensure_ascii=False)}\n"
                       "return Object.fromEntries(Object.entries(h)"
                       ".map(([k, v]) => [k, v.map(slugify)]))")
        cls.slugs = {k: set(v) for k, v in slugs.items()}

    def _check(self, where: str, section: str, anchor: str) -> None:
        self.assertIn(section, self.slugs, f"{where}: нет раздела «{section}»")
        self.assertIn(anchor, self.slugs[section],
                      f"{where}: в разделе «{section}» нет заголовка с якорем «{anchor}»")

    def test_doc_links(self) -> None:
        link_re = re.compile(r"\]\((?!https?:)([^)]*#[^)]+)\)")
        for path in sorted(help_docs.DOCS_DIR.glob("*.md")):
            own = SECTION_FILE.match(path.name).group(1)
            for href in link_re.findall(_strip_code(path.read_text(encoding="utf-8"))):
                file, anchor = href.split("#", 1)
                section = SECTION_FILE.match(file).group(1) if file else own
                self._check(path.name, section, anchor.lower())

    def test_ui_buttons(self) -> None:
        """Кнопки «подробнее» и баннеры: адрес вида `раздел#якорь` в исходниках."""
        # `|| '…'` — адрес по умолчанию, когда у кода баннера своего нет
        ref_re = re.compile(r"""(?:penHelp\(|help:\s*|\|\|\s*)['"]([a-z0-9-]+)#([^'"]+)['"]""")
        found = 0
        for path in sorted(FRONTEND.rglob("*.jsx")):
            for section, anchor in ref_re.findall(path.read_text(encoding="utf-8")):
                found += 1
                self._check(path.name, section, anchor)
        self.assertTrue(found, "ни одна кнопка не ведёт на подраздел")


class ModalWiringTest(unittest.TestCase):
    """Окно помощи разбирает якорь и в ссылках, и в адресе, с которым его открыли."""

    def test_modal_uses_anchor_module(self) -> None:
        src = MODAL.read_text(encoding="utf-8")
        self.assertIn("from '../helpAnchor'", src)
        self.assertIn("parseHelpRef(section)", src,
                      "адрес открытия `раздел#якорь` не разбирается")
        self.assertIn("slugify(", src, "заголовок не сопоставляется с якорем")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
