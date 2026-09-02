"""Базовый слепок поставки: из чего разворачивали конкретный проект.

Без слепка расхождение развёрнутого файла с шаблоном необъяснимо. Причин у
него две — обновился шаблон в инструменте либо файл правили в проекте, — а по
двум текстам они неразличимы: отсюда обтекаемое «отличается от шаблона» в UI
и вечный баннер у осознанно кастомизированного скилла.

Слепок — третий текст: копия того, что инструмент сам записал в проект. Он
даёт точный ответ о причине расхождения и служит общим предком для
трёхстороннего merge, без которого слияние правок невозможно в принципе.

Живёт слепок в `<tasks>/.taskboard/`: папка задач и так целиком в .gitignore
проекта, рядом лежит его конфиг, и служебные копии не засоряют репозиторий
пользователя.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from backend import version
from backend.proc import no_window_flags

# Служебная папка проекта и её разделы
STORE_DIR = ".taskboard"
BASELINE_DIR = "baseline"
BACKUP_DIR = "backup"
META_FILE = "env.json"

# Состояния элемента поставки. Пять вместо прежних двух: причина расхождения
# определяет и текст в UI, и набор доступных действий
SAME = "same"                # развёрнутое совпадает с шаблоном
CUSTOMIZED = "customized"    # правки в проекте, шаблон не менялся — это актуально
OUTDATED = "outdated"        # правок нет, шаблон ушёл вперёд — обновление безопасно
CONFLICT = "conflict"        # правки в проекте и новый шаблон — нужен merge
UNKNOWN = "unknown"          # слепка нет (развёрнуто до его появления)
MISSING = "missing"          # файла нет вовсе


def same_text(current: str | None, expected: str | None) -> bool:
    """Совпадает ли текст — построчно, как в diff.

    Хвостовой перевод строки, съеденный редактором, расхождением не считается:
    в diff его не видно, объяснить им баннер нечем (TASK-107).
    """
    if current is None or expected is None:
        return False
    return current.splitlines() == expected.splitlines()


def state(current: str | None, expected: str, base: str | None) -> str:
    """Состояние элемента по трём текстам: развёрнутый, эталон, слепок.

    Порядок проверок — от бесспорного к выводимому: совпадение с шаблоном
    ничего доказывать не нужно, а «кастомизирован» и «отстал» различает
    только слепок.
    """
    if current is None:
        return MISSING
    if same_text(current, expected):
        return SAME
    if base is None:
        return UNKNOWN
    if same_text(base, expected):
        # Шаблон не двигался — расхождение целиком принадлежит пользователю,
        # и беспокоить его нечем: файл актуален, просто он его правил
        return CUSTOMIZED
    if same_text(current, base):
        return OUTDATED
    return CONFLICT


# --- Расположение служебных копий ---

def store_dir(project_root: Path, cfg: dict | None = None) -> Path:
    """Служебная папка проекта: `<tasks>/.taskboard/`."""
    return project_root / (cfg or {}).get("tasks_dir", "tasks") / STORE_DIR


def _safe_name(name: str) -> PurePosixPath:
    """Имя элемента как относительный путь без выхода за пределы папки.

    Имена приходят из реестров поставки, но путь собирается из данных запроса —
    подниматься по `..` из служебной папки он не должен.
    """
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".", "..")]
    return PurePosixPath(*parts) if parts else PurePosixPath("_")


def _element_path(section: Path, part: str, name: str) -> Path:
    return section / part / _safe_name(name)


def read(project_root: Path, part: str, name: str, cfg: dict | None = None) -> str | None:
    """Слепок элемента или None, если его не сохраняли."""
    path = _element_path(store_dir(project_root, cfg) / BASELINE_DIR, part, name)
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return None


def write(project_root: Path, part: str, name: str, text: str,
          cfg: dict | None = None) -> None:
    """Запомнить, что именно развёрнуто в проекте (вызывается после записи файла).

    Отказ записи не должен ронять само развёртывание: слепок — служебные
    данные, а файл пользователю уже доставлен. Потеря слепка вернёт элемент
    в состояние «происхождение неизвестно», не более того.
    """
    path = _element_path(store_dir(project_root, cfg) / BASELINE_DIR, part, name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return
    _stamp(project_root, cfg)


def backup(project_root: Path, part: str, name: str, text: str,
           cfg: dict | None = None) -> str | None:
    """Сохранить прежнее содержимое перед перезаписью. Вернуть путь для UI.

    Одна копия на элемент: цель — снять необратимость («обновил и потерял»),
    а не вести историю. Путь относительный от корня проекта — его показывают
    пользователю, и абсолютный здесь только мешает.
    """
    path = _element_path(store_dir(project_root, cfg) / BACKUP_DIR, part, name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return str(path.relative_to(project_root)).replace("\\", "/")


def _stamp(project_root: Path, cfg: dict | None = None) -> None:
    """Отметить версию инструмента и время последней записи окружения.

    Из какой версии развёрнут проект, по самим файлам не узнать, а вопрос
    возникает при каждом разборе «почему у меня не так».
    """
    path = store_dir(project_root, cfg) / META_FILE
    try:
        path.write_text(json.dumps(
            {"version": version.current(), "updated_at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def meta(project_root: Path, cfg: dict | None = None) -> dict:
    """Версия и время последней записи окружения ({} — записей не было)."""
    try:
        return json.loads((store_dir(project_root, cfg) / META_FILE)
                          .read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


# --- Трёхсторонний merge ---

def git_available() -> bool:
    """Есть ли git: слияние выполняет `git merge-file`."""
    try:
        return subprocess.run(["git", "--version"], capture_output=True,
                              timeout=10,
                              creationflags=no_window_flags()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def merge(base: str, ours: str, theirs: str) -> tuple[str, int] | None:
    """Слить правки проекта с новым шаблоном. Вернуть (текст, число конфликтов).

    None — слияние не выполнено (git недоступен или отказал): вызывающий
    оставляет пользователю выбор «взять шаблон / оставить своё», но молча
    ничего не переписывает.

    Свой алгоритм не пишем: `git merge-file` работает вне репозитория, обкатан
    и уже есть на машине — инструмент обновляет сам себя через git.
    Конфликтующие куски остаются в файле маркерами, с базой внутри (`--diff3`):
    разрешать их всё равно человеку, и без общего предка кусок не прочитать.
    """
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        files = {}
        for key, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            files[key] = folder / key
            files[key].write_text(text, encoding="utf-8", newline="\n")
        try:
            proc = subprocess.run(
                ["git", "merge-file", "-p", "--diff3",
                 "-L", "в проекте", "-L", "из чего разворачивали", "-L", "шаблон",
                 str(files["ours"]), str(files["base"]), str(files["theirs"])],
                capture_output=True, timeout=30,
                creationflags=no_window_flags())
        except (OSError, subprocess.SubprocessError):
            return None
    # Код возврата git merge-file — число конфликтов; отрицательный (в оболочке
    # 255) означает ошибку самой операции, и её результату доверять нельзя
    if proc.returncode < 0 or proc.returncode > 127:
        return None
    return proc.stdout.decode("utf-8", errors="replace"), proc.returncode
