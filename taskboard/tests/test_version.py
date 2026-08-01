"""Версия поставки: разбор, сравнение, чтение файла VERSION.

Модуль version.py — единственное место, где решается «новее или нет», поэтому
здесь проверяется именно то, на чём такие сравнения обычно ломаются:
разная длина («1.2» против «1.2.0»), переход через десяток («0.9» против «0.10»)
и ведущая «v» из имени тега.
"""

import unittest

from backend import version


class TestParse(unittest.TestCase):

    def test_обычная_версия(self):
        self.assertEqual(version.parse("1.4.0"), (1, 4, 0))

    def test_ведущая_v_отбрасывается(self):
        # В теге версия пишется как v1.4.0, в файле VERSION — как 1.4.0,
        # а сравнивать приходится одно с другим
        self.assertEqual(version.parse("v1.4.0"), version.parse("1.4.0"))

    def test_пробелы_и_перевод_строки(self):
        self.assertEqual(version.parse("  1.4.0\n"), (1, 4, 0))

    def test_мусор_это_ошибка(self):
        for bad in ("", "   ", "abc", "1.x.0", "1..0", "1.4.0-beta"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    version.parse(bad)

    def test_none_тоже_ошибка(self):
        # Приходит из разобранного json манифеста, где поля может не оказаться
        with self.assertRaises((ValueError, TypeError)):
            version.parse(None)  # type: ignore[arg-type]

    def test_is_valid(self):
        self.assertTrue(version.is_valid("1.0.0"))
        self.assertFalse(version.is_valid("не версия"))


class TestCompare(unittest.TestCase):

    def test_равные(self):
        self.assertEqual(version.compare("1.4.0", "1.4.0"), 0)

    def test_разная_длина_это_та_же_версия(self):
        self.assertEqual(version.compare("1.2", "1.2.0"), 0)

    def test_переход_через_десяток(self):
        # Ровно то, на чём ломается сравнение строк: "0.9.0" > "0.10.0" как текст
        self.assertEqual(version.compare("0.9.0", "0.10.0"), -1)
        self.assertEqual(version.compare("0.10.0", "0.9.0"), 1)

    def test_старше_и_новее(self):
        self.assertEqual(version.compare("1.4.0", "1.4.1"), -1)
        self.assertEqual(version.compare("2.0.0", "1.9.9"), 1)

    def test_тег_против_файла(self):
        self.assertEqual(version.compare("v1.4.0", "1.4.0"), 0)


class TestCurrent(unittest.TestCase):

    def test_файл_версии_существует_и_разбирается(self):
        self.assertTrue(version.VERSION_FILE.is_file(),
                        f"нет файла версии: {version.VERSION_FILE}")
        self.assertTrue(version.is_valid(version.current()))

    def test_версия_не_нулевая(self):
        # UNKNOWN отдаётся только при испорченном или отсутствующем файле
        self.assertNotEqual(version.current(), version.UNKNOWN)

    def test_лежит_рядом_с_пакетом(self):
        # Лаунчер читает этот же файл до создания venv — путь не должен уехать
        self.assertEqual(version.VERSION_FILE.name, "VERSION")
        self.assertEqual(version.VERSION_FILE.parent.name, "taskboard")


class TestHealthExposesVersion(unittest.TestCase):
    """Версию отдают ВСЕ ветки /api/health, а не только та, что попалась на глаза.

    По версии из health лаунчер понимает, что запущенный сервер старее кода.
    У `api_health` два выхода — с активным проектом и без, — и пропустить один
    легко: ветка «без проекта» срабатывает редко и глазами не проверяется.
    Разбираем исходник, потому что поднимать сервер с настоящим реестром ради
    этого дороже, чем проверить факт.
    """

    def setUp(self):
        import ast
        source = (version.VERSION_FILE.parent / "backend" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "api_health"), None)
        self.assertIsNotNone(self.func, "в app.py не нашлась функция api_health")
        self.ast = ast

    def test_каждый_return_отдаёт_версию(self):
        returns = [n for n in self.ast.walk(self.func) if isinstance(n, self.ast.Return)]
        self.assertGreaterEqual(len(returns), 2, "у api_health ожидалось несколько выходов")
        for index, node in enumerate(returns):
            with self.subTest(выход=index):
                self.assertIsInstance(node.value, self.ast.Dict)
                keys = [k.value for k in node.value.keys
                        if isinstance(k, self.ast.Constant)]
                self.assertIn("version", keys,
                              "ветка /api/health не отдаёт version — лаунчер сочтёт "
                              "такой сервер устаревшим")


if __name__ == "__main__":
    unittest.main()
