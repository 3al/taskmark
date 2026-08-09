"""Тексты выключенной возможности не должны просачиваться в поставку.

TASK-169: абзац про волт в скилле `finalize-task` лежал вне блока
`<!-- vault -->` и уезжал в проекты без волта — скилл ссылался на возможность,
которой у них нет, и на скилл `write-vault`, который туда не разворачивается.

Проверка общая, а не про один абзац: маркеры расставляют руками, и следующая
такая строка появится так же незаметно.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.scaffold import (AGENTIC_TEMPLATES, COMMANDS_TEMPLATES,  # noqa: E402
                              OPTIONAL_BLOCKS, SKILLS_TEMPLATES,
                              feature_skills, strip_optional_blocks)

# Слова, по которым возможность себя выдаёт. Не только её имя: понятие живёт
# в тексте синонимами («внешняя память», «таксономия», путь `vault/SYS`), и
# поиск по одному слову утечку бы пропустил
FEATURE_WORDS = {
    "vault": re.compile(r"волт|vault|внешн\w+\s+памят|таксоном", re.IGNORECASE),
    # Одного «merge request» мало: та же вещь пишется как «MR», «merge/pull
    # request», а способ её прочитать — как MCP или `glab`
    "review_sources": re.compile(r"\bMR\b|merge request|merge/pull request"
                                 r"|pull request|MCP|\bglab\b", re.IGNORECASE),
}


class OptionalLeaksTest(unittest.TestCase):
    def _templates(self, skipped: set[str]) -> list[tuple[str, str]]:
        """(имя, текст) всех шаблонов, которые едут в проект с выключенной возможностью."""
        items = [(f"скилл {d.name}", (d / "SKILL.md").read_text(encoding="utf-8"))
                 for d in sorted(SKILLS_TEMPLATES.iterdir())
                 if d.is_dir() and d.name not in skipped]
        items += [(f"команда {f.name}", f.read_text(encoding="utf-8"))
                  for f in sorted(COMMANDS_TEMPLATES.glob("*.md")) if f.stem not in skipped]
        items.append(("rules_section.md",
                      (AGENTIC_TEMPLATES / "rules_section.md").read_text(encoding="utf-8")))
        return items

    def test_disabled_feature_leaves_no_trace(self) -> None:
        """Выключенная возможность исчезает из текстов целиком, а не почти целиком."""
        for spec in OPTIONAL_BLOCKS:
            key = spec["key"]
            words = FEATURE_WORDS.get(key)
            if words is None:
                self.fail(f"нет слов-маркеров для возможности {key}: "
                          f"добавьте их в FEATURE_WORDS, иначе она не проверяется")
            # Возможность выключена, остальные включены: ловим утечку именно её
            features = {s["key"] for s in OPTIONAL_BLOCKS if s["key"] != key}
            skipped = set(feature_skills(key))
            for name, text in self._templates(skipped):
                stripped = strip_optional_blocks(text, features)
                leaks = [f"{i}: {line.strip()}"
                         for i, line in enumerate(stripped.splitlines(), 1)
                         if words.search(line)]
                self.assertEqual(
                    leaks, [],
                    f"{name}: упоминание «{key}» вне блока <!-- {spec['marker']} --> — "
                    f"проект без этой возможности получит текст про то, чего у него нет")

    def test_enabled_feature_keeps_its_text(self) -> None:
        """Инвариант в обратную сторону: включённая возможность из текстов не пропадает."""
        features = {spec["key"] for spec in OPTIONAL_BLOCKS}
        joined = "\n".join(strip_optional_blocks(text, features)
                           for _name, text in self._templates(set()))
        self.assertRegex(joined, FEATURE_WORDS["vault"])


if __name__ == "__main__":
    unittest.main()
