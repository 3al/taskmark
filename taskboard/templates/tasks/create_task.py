#!/usr/bin/env python3
"""
Скрипт для создания новой задачи в систему Task Management.

Использование (интерактивный режим):
  py tasks/create_task.py          (Windows)
  python3 tasks/create_task.py     (macOS/Linux)

Использование (с аргументами):
  py tasks/create_task.py -t "Название" -d "Описание" -c "Критерии"
  py tasks/create_task.py -t "Название" --type refactor --section refactor -e "E056-18500"

Параметры:
  -t, --title TEXT        Название задачи (обязательно)
  -d, --description TEXT  Описание
  -c, --criteria TEXT     Критерии приёмки
  -b, --blocked-by TASK-NNN  Задача-блокер (опционально)
  -e, --epic TEXT         Эпик — Jira-ключ (опционально). Имя эпика хранится в tasks/epics.md
  --type TYPE             Тип задачи: bug | refactor | feature | cleanup
                          (влияет на чеклист; default: feature)
  --section SECTION       Подраздел Backlog: refactor | feature
                          (влияет на позицию в board.md; default: feature)
"""

import sys
import re
import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path


def _read_json(path: Path) -> dict:
    """Прочитать json, при любой ошибке — пустой словарь."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_config(tasks_dir: Path) -> dict:
    """Дефолты → глобальный конфиг → per-project переопределения.

    Конфиг проекта живёт в tasks/.taskboard.json; прежнее расположение
    (<корень>/taskboard/config.json) читаем ради совместимости.
    """
    cfg = {"board_file": "board.md", "status_script": "set_status.py"}
    cfg.update(_read_json(Path.home() / ".taskboard" / "config.json"))
    cfg.update(_read_json(tasks_dir.parent / "taskboard" / "config.json"))
    cfg.update(_read_json(tasks_dir / ".taskboard.json"))
    return cfg


def intake_status(tasks_dir: Path, cfg: dict) -> tuple[str, str]:
    """Статус и раздел доски для новой задачи — по пайплайну проекта.

    Разбор пайплайна живёт в соседнем set_status.py: это тот же жизненный цикл,
    и второй копии каталога статусов в tasks/ быть не должно. Скрипта нет или
    он старый — работаем как раньше, по бэклогу.
    """
    script = tasks_dir / cfg.get("status_script", "set_status.py")
    try:
        spec = importlib.util.spec_from_file_location("_set_status", script)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        pipeline = module.pipeline_of(cfg)
        key = module.actions_of(cfg, pipeline).get("create")
        for meta in pipeline:
            if meta["key"] == key:
                return key, meta["section"]
    except Exception:
        pass
    return "backlog", "Backlog"

# UTF-8 encoding для консоли
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _is_interactive(title_from_args: bool) -> bool:
    """
    Определить режим запуска.
    Если --title передан явно → программный вызов, без интерактива.
    Иначе → интерактивный режим.
    """
    return not title_from_args


def get_next_task_number() -> int:
    """Найти следующий номер задачи."""
    tasks_dir = Path(__file__).parent
    if not tasks_dir.exists():
        return 1

    task_files = list(tasks_dir.glob("TASK-*.md"))
    if not task_files:
        return 1

    numbers = []
    for f in task_files:
        match = re.match(r"TASK-(\d+)", f.name)
        if match:
            numbers.append(int(match.group(1)))

    return max(numbers) + 1 if numbers else 1


def slugify(text: str) -> str:
    """Преобразовать текст в slug для имени файла."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


def ask_input(prompt: str, default: str = "") -> str:
    """Получить ввод от пользователя (только в интерактивном режиме)."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "

    result = input(prompt).strip()
    return result or default


def build_checklist(task_type: str) -> str:
    """Сформировать чеклист по типу задачи."""
    checklists = {
        "bug": (
            "- [ ] Воспроизвести баг и написать failing тест (RED)\n"
            "- [ ] Исправить минимальным изменением\n"
            "- [ ] Все тесты проходят\n"
            "- [ ] Локальная проверка подтверждена пользователем"
        ),
        "refactor": (
            "- [ ] Убедиться что поведение покрыто тестами (или написать)\n"
            "- [ ] Провести рефакторинг\n"
            "- [ ] Все тесты проходят (поведение не изменилось)\n"
            "- [ ] Локальная проверка подтверждена пользователем"
        ),
        "feature": (
            "- [ ] Написать тест (RED)\n"
            "- [ ] Реализовать минимальный код\n"
            "- [ ] Все тесты проходят\n"
            "- [ ] Локальная проверка подтверждена пользователем"
        ),
        "cleanup": (
            "- [ ] Определить что удалить/упростить\n"
            "- [ ] Применить изменения\n"
            "- [ ] Все тесты проходят (регрессий нет)\n"
            "- [ ] Локальная проверка подтверждена пользователем"
        ),
    }
    return checklists.get(task_type, checklists["feature"])


TEMPLATE_FILE = "_TEMPLATE.md"

# Заголовок секции → чем заполнить её тело при создании задачи.
# Остальные секции шаблона (заметки агента, история коммитов) переезжают
# в новую задачу как есть
_FILLED_SECTIONS = ("## Описание", "### Критерии приёмки", "## Чеклист")


# Строки, которые markdown и так разбирает построчно: список, заголовок,
# цитата, таблица, граница блока кода. Их разделять пустой строкой нельзя
_STRUCTURAL_LINE = re.compile(r"\s*([-*+>#|]|\d+[.)]\s|```)")


def as_paragraphs(text: str) -> str:
    """Одиночные переносы автора → абзацы markdown.

    В поле формы и в аргументе `-d` человек жмёт Enter и ждёт новую строку,
    а markdown склеивает такие переводы в один абзац — отсюда «простыни»
    в описаниях. Разделяем пустой строкой на входе: файл остаётся валидным
    markdown, а рендер не приходится учить нестандартной семантике.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    for i, line in enumerate(lines):
        out.append(line)
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if in_code or i + 1 >= len(lines):
            continue
        nxt = lines[i + 1]
        if not line.strip() or not nxt.strip():
            continue  # пустая строка уже разделяет
        if _STRUCTURAL_LINE.match(line) or _STRUCTURAL_LINE.match(nxt):
            continue  # список и заголовки markdown разбирает сам
        out.append("")
    return "\n".join(out)


def _replace_section(text: str, heading: str, body: str) -> str:
    """Заменить тело секции heading, не трогая остальные.

    Границей считается следующий заголовок любого уровня — так секция
    «### Критерии приёмки» внутри «## Описание» остаётся на месте.
    """
    start = text.find(heading + "\n")
    if start < 0:
        return text
    body_start = start + len(heading) + 1
    next_heading = re.search(r"^#{1,6} ", text[body_start:], flags=re.M)
    body_end = body_start + next_heading.start() if next_heading else len(text)
    return text[:body_start] + f"\n{body.strip()}\n\n" + text[body_end:]


def render_from_template(tasks_dir: Path, frontmatter: str, description: str,
                         criteria: str, checklist: str) -> str | None:
    """Собрать файл задачи из `_TEMPLATE.md`, если он есть в проекте.

    Шаблон — видимый эталон структуры: его правят руками, и созданные задачи
    должны следовать за ним, а не за копией структуры внутри скрипта.
    Нет шаблона (старый проект) — вернуть None, вызывающий возьмёт встроенную.
    """
    template = tasks_dir / TEMPLATE_FILE
    try:
        text = template.read_text(encoding="utf-8")
    except OSError:
        return None

    end = text.find("\n---", 3)
    if not text.startswith("---") or end < 0:
        return None  # шаблон без frontmatter — считаем его сломанным
    text = frontmatter + text[end + 4:]

    for heading, body in zip(_FILLED_SECTIONS, (description, criteria, checklist)):
        text = _replace_section(text, heading, body)
    return text


def _append_to_block(board_content: str, heading: str, new_entry: str) -> str | None:
    """
    Дописать запись в конец блока с заголовком heading.

    Границей блока считается ближайший следующий заголовок любого уровня, а не
    конкретная секция: раньше feature-задачи вставлялись перед «## Development»,
    то есть в самый конец Backlog, и после появления подраздела «Отложено»
    начали падать в него.

    Возвращает None, если заголовок в доске не найден.
    """
    start = board_content.find(heading)
    if start < 0:
        return None

    after = start + len(heading)
    boundaries = [
        pos
        for pos in (board_content.find("\n### ", after), board_content.find("\n## ", after))
        if pos >= 0
    ]
    if not boundaries:
        return board_content.rstrip() + f"\n{new_entry}\n"

    end = min(boundaries)
    return f"{board_content[:end]}\n{new_entry}{board_content[end:]}"


def insert_into_board(board_content: str, new_entry: str, section: str,
                      intake_section: str = "Backlog") -> str:
    """
    Вставить новую задачу в нужный подраздел раздела приёма задач.

    section — заголовок подраздела ### (полный текст) либо легаси-алиас:
    "refactor" → ### Рефакторинг, "feature" → ### Новый функционал.
    Fallback → в конец раздела приёма (его имя задаёт пайплайн проекта).
    """
    aliases = {
        "refactor": "Рефакторинг",
        "feature": "Новый функционал",
    }
    heading = f"### {aliases.get(section, section)}"
    updated = _append_to_block(board_content, heading, new_entry)
    if updated is not None:
        return updated

    # Fallback: в конец раздела приёма — перед следующим разделом ##
    marker = re.search(rf"^##\s+{re.escape(intake_section)}\s*$", board_content,
                       flags=re.MULTILINE)
    if marker:
        rest = board_content[marker.end():]
        nxt = re.search(r"^##\s+", rest, flags=re.MULTILINE)
        at = marker.end() + (nxt.start() if nxt else len(rest))
        return board_content[:at].rstrip() + f"\n{new_entry}\n\n" + board_content[at:]

    # Последний fallback: в конец файла
    return board_content.rstrip() + f"\n{new_entry}\n"


def create_task(
    title: str | None = None,
    description: str | None = None,
    criteria: str | None = None,
    blocked_by: str | None = None,
    epic: str | None = None,
    task_type: str = "feature",
    section: str = "feature",
) -> None:
    """Главная функция создания задачи."""
    print("\n=== Создание новой задачи ===\n")

    interactive = _is_interactive(title is not None)

    # Получить информацию (из аргументов или интерактивно)
    if title is None:
        if not interactive:
            print("[ERROR] Флаг -t/--title обязателен в не-интерактивном режиме")
            sys.exit(1)
        title = ask_input("Название задачи (обязательно)")
    if not title:
        print("[ERROR] Название задачи не может быть пустым")
        sys.exit(1)

    if description is None:
        description = ask_input("Описание (что нужно сделать)") if interactive else ""

    if criteria is None:
        default_criteria = "TDD: RED -> GREEN -> ALL TESTS PASS"
        criteria = ask_input("Критерии приёмки (опционально)", default=default_criteria) if interactive else default_criteria

    # blocked_by: спрашиваем ТОЛЬКО в интерактивном режиме
    if blocked_by is None:
        blocked_by = ask_input("Заблокировано задачей (опционально, формат: TASK-NNN)", default="") if interactive else ""

    # epic: спрашиваем ТОЛЬКО в интерактивном режиме
    if epic is None:
        epic = ask_input("Эпик (Jira-ключ, опционально — пусто = нет)", default="") if interactive else ""

    # Генерировать номер и имя файла
    task_num = get_next_task_number()
    slug = slugify(title)
    filename = f"TASK-{task_num:03d}-{slug}.md"
    created_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    blocked_by_line = f"\nblocked_by: {blocked_by}" if blocked_by else ""
    epic_value = epic if epic else "~"
    checklist = build_checklist(task_type)

    tasks_dir = Path(__file__).parent
    cfg = load_config(tasks_dir)
    status_key, intake_section = intake_status(tasks_dir, cfg)

    frontmatter = f"""---
id: TASK-{task_num:03d}
title: {title}
epic: {epic_value}
status: {status_key}
created: {created_date}{blocked_by_line}
---"""

    # Структуру задаёт `_TEMPLATE.md` проекта; встроенная копия — запасной
    # вариант для проектов, развёрнутых до его появления
    # Текст автора приводим к абзацам до записи: в файле остаётся обычный
    # markdown, и он одинаково читается и в окне доски, и в редакторе
    description = as_paragraphs(description)
    criteria = as_paragraphs(criteria)

    content = render_from_template(tasks_dir, frontmatter, description, criteria, checklist)
    if content is None:
        content = f"""{frontmatter}

## Описание

{description}

### Критерии приёмки

{criteria}

## Чеклист

{checklist}

## Заметки агента

## История коммитов
"""

    # Создать файл
    tasks_dir.mkdir(exist_ok=True)
    task_file = tasks_dir / filename

    task_file.write_text(content, encoding="utf-8")
    print(f"[OK] Создана задача: {filename}")

    # Обновить доску
    board_file = tasks_dir / cfg.get("board_file", "board.md")
    board_content = board_file.read_text(encoding="utf-8")

    new_entry = f"- TASK-{task_num:03d} · [{title}]({filename})"

    # Случай: раздел приёма пуст — заменяем маркер заглушки
    empty_intake = f"## {intake_section}\n\n_(нет)_"
    if empty_intake in board_content:
        updated = board_content.replace(
            empty_intake, f"## {intake_section}\n\n{new_entry}"
        )
    else:
        updated = insert_into_board(board_content, new_entry, section, intake_section)

    if updated == board_content:
        print("[WARN] Не удалось вставить в нужный раздел — запись добавлена в конец файла")
        updated = board_content.rstrip() + f"\n{new_entry}\n"

    board_file.write_text(updated, encoding="utf-8")
    print(f"[OK] Обновлен board.md (секция: {section})")

    print(f"\n[OK] Задача успешно создана!\n")
    print(f"  ID: TASK-{task_num:03d}")
    print(f"  Файл: tasks/{filename}")
    print(f"  Тип: {task_type} | Секция: {section}")
    if epic:
        print(f"  Эпик: {epic}")
    print(f"  Статус: {status_key}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Создать новую задачу")
    parser.add_argument("-t", "--title", help="Название задачи")
    parser.add_argument("-d", "--description", help="Описание")
    parser.add_argument("-c", "--criteria", help="Критерии приёмки")
    parser.add_argument("-b", "--blocked-by", help="Задача-блокер (TASK-NNN)")
    parser.add_argument("-e", "--epic", help="Эпик (Jira-ключ, опционально)")
    parser.add_argument(
        "--type",
        dest="task_type",
        choices=["bug", "refactor", "feature", "cleanup"],
        default="feature",
        help="Тип задачи (влияет на чеклист)",
    )
    parser.add_argument(
        "--section",
        default="feature",
        help="Подраздел Backlog: полный заголовок ### или алиас refactor|feature",
    )
    args = parser.parse_args()

    try:
        create_task(
            title=args.title,
            description=args.description,
            criteria=args.criteria,
            blocked_by=args.blocked_by,
            epic=args.epic,
            task_type=args.task_type,
            section=args.section,
        )
    except KeyboardInterrupt:
        print("\n[CANCEL] Отменено пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Ошибка: {e}")
        sys.exit(1)
