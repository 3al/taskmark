"""Контраст мелкого текста: у него одна шкала, а не подбор на месте.

Мелкий вспомогательный текст (подписи полей, пояснения под настройками,
мета-строки карточек) набирался 10–11 пикселями и при этом самым тёмным
оттенком — читать его приходилось всматриваясь. Разнобой и был причиной: часть
надписей читалась, часть нет.

Тест держит правило: **мелкий текст не бывает темнее `zinc-400`**. Оттенки
темнее остаются для крупного текста, где размер компенсирует контраст.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"

SMALL = ("text-[10px]", "text-[11px]")
TOO_DARK = re.compile(r"text-zinc-([5-9]\d\d)")


def sources() -> list[Path]:
    return sorted(FRONTEND.rglob("*.jsx")) + sorted(FRONTEND.rglob("*.js"))


class SmallTextContrastTest(unittest.TestCase):

    def test_мелкий_текст_не_темнее_zinc_400(self):
        bad = []
        for path in sources():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if any(size in line for size in SMALL) and TOO_DARK.search(line):
                    bad.append(f"{path.name}:{number}")
        self.assertEqual(bad, [], "мелкий текст снова набран тёмным оттенком: "
                                  + ", ".join(bad))

    def test_самый_тёмный_оттенок_больше_не_используется(self):
        """zinc-600 на тёмном фоне читается только вплотную к экрану."""
        bad = [path.name for path in sources()
               if "text-zinc-600" in path.read_text(encoding="utf-8")]
        self.assertEqual(bad, [], "вернулся text-zinc-600: " + ", ".join(bad))


if __name__ == "__main__":
    unittest.main()
