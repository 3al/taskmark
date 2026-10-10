"""Документация API на `/docs`: имя, версия, группы и описание каждого эндпоинта.

Схему отдаёт сам FastAPI (`/openapi.json`, по ней рисуются `/docs` и `/redoc`).
Тест читает её тем же вызовом `app.openapi()`, которым её строит сервер, — без
HTTP-клиента, которого в зависимостях нет.

Полнота проверяется по всем маршрутам: новый эндпоинт без описания или вне
группы роняет прогон и называется в сообщении.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import app, version  # noqa: E402

METHODS = {"get", "post", "put", "patch", "delete"}


def operations(schema: dict) -> list[tuple[str, dict]]:
    """Все операции схемы: («GET /api/…», описание операции)."""
    return [(f"{m.upper()} {path}", op)
            for path, ops in schema["paths"].items()
            for m, op in ops.items() if m in METHODS]


class ApiDocsTest(unittest.TestCase):

    def setUp(self) -> None:
        self.schema = app.app.openapi()

    def test_docs_served_at_standard_urls(self) -> None:
        paths = {r.path for r in app.app.routes}
        self.assertIn("/docs", paths)
        self.assertEqual(app.app.redoc_url, "/redoc")
        self.assertEqual(app.app.openapi_url, "/openapi.json")

    def test_docs_page_is_dark(self) -> None:
        page = self.docs_page()
        self.assertIn("swagger-ui", page)
        self.assertIn("/openapi.json", page)
        # Фон страницы — zinc-950 доски, а не белый Swagger UI
        self.assertRegex(page, r"body\s*\{[^}]*background:\s*#09090b")

    def docs_page(self) -> str:
        route = next(r for r in app.app.routes if r.path == "/docs")
        return route.endpoint().body.decode("utf-8")

    def test_names_product_and_its_version(self) -> None:
        info = self.schema["info"]
        self.assertEqual(info["title"], "Taskmark")
        self.assertEqual(info["version"], version.current())
        self.assertTrue(info.get("description", "").strip())

    def test_every_operation_is_in_described_group(self) -> None:
        described = {t["name"] for t in self.schema.get("tags", [])
                     if t.get("description", "").strip()}
        self.assertGreater(len(described), 1)
        stray = [name for name, op in operations(self.schema)
                 if len(op.get("tags", [])) != 1 or op["tags"][0] not in described]
        self.assertEqual(stray, [], "эндпоинты вне описанной группы")

    def test_every_operation_has_human_summary(self) -> None:
        bare = []
        for name, op in operations(self.schema):
            summary = op.get("summary", "")
            # Заголовок по умолчанию FastAPI собирает из имени функции: «Api Projects»
            if not summary.strip() or summary.startswith("Api "):
                bare.append(name)
        self.assertEqual(bare, [], "эндпоинты без заголовка")

    def test_unused_groups_are_not_listed(self) -> None:
        used = {op["tags"][0] for _, op in operations(self.schema) if op.get("tags")}
        listed = [t["name"] for t in self.schema.get("tags", [])]
        self.assertEqual([t for t in listed if t not in used], [])


if __name__ == "__main__":
    unittest.main()
