#!/usr/bin/env python3
"""
Скрипт для создания новой задачи в систему Task Management.

Использование (интерактивный режим):
  py tasks/create_task.py          (Windows)
  python tasks/create_task.py      (Windows без лаунчера py — Microsoft Store)
  python3 tasks/create_task.py     (macOS/Linux)

Использование (с аргументами):
  py tasks/create_task.py -t "Название" -d "Описание" -c "Критерии"
  py tasks/create_task.py -t "Название" --type refactor -e "E056-18500"

Параметры:
  -t, --title TEXT        Название задачи (обязательно)
  -d, --description TEXT  Описание
  -c, --criteria TEXT     Критерии приёмки
  -b, --blocked-by TASK-NNN  Задача-блокер (опционально)
  -e, --epic TEXT         Эпик — Jira-ключ (опционально). Имя эпика хранится в tasks/epics.md
  --type TYPE             Тип задачи: feature | bug | refactor | cleanup |
                          discussion | design (остаётся в поле `type` задачи
                          и рисует метку на доске; default: feature)
  --section SECTION       Подраздел Backlog. По умолчанию — рубрика типа
                          задачи: «Баги» для bug, «Обсуждения» для discussion
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
    module = status_module(tasks_dir, cfg)
    try:
        pipeline = module.pipeline_of(cfg)  # type: ignore[union-attr]
        key = module.actions_of(cfg, pipeline).get("create")  # type: ignore[union-attr]
        for meta in pipeline:
            if meta["key"] == key:
                return key, meta["section"]
    except Exception:
        pass
    return "backlog", "Backlog"


def status_module(tasks_dir: Path, cfg: dict):
    """Соседний set_status.py как модуль (None, если его нет или он старый).

    В нём живут и пайплайн, и каталог типов задач: второй копии этих справочников
    в tasks/ быть не должно.
    """
    script = tasks_dir / cfg.get("status_script", "set_status.py")
    try:
        spec = importlib.util.spec_from_file_location("_set_status", script)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception:
        return None


def section_of_type(task_type: str) -> str | None:
    """Рубрика бэклога для типа задачи — из каталога соседнего set_status.py.

    Рубрика и тип — одно понятие: «Баги» это раздел для `bug`. Скрипта нет или
    он старый — вернём None, и задача ляжет в конец раздела приёма.
    """
    tasks_dir = Path(__file__).parent
    module = status_module(tasks_dir, load_config(tasks_dir))
    meta = getattr(module, "TASK_TYPES", {}).get(task_type) if module else None
    return meta.get("section") if meta else None

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


# Типы задач — константа поставки, а не настройка проекта: «что это за работа»
# не зависит от жизненного цикла. Подписи, буквы и цвета живут в соседнем
# set_status.py (`--types`), сюда им ехать незачем — здесь нужен только перечень
# допустимых значений `--type`.
#
# Чеклиста по типу больше нет. Он ставился шаблоном и почти никогда не описывал
# предстоящую работу: у обсуждения требовал тестов, которых не будет, а
# закрывался всё равно — галочками в конце. Чеклист заводит агент под конкретную
# работу, когда она многошаговая; правило — в секции правил проекта.
TASK_TYPES = ("feature", "bug", "refactor", "cleanup", "discussion", "design",
              "review")
DEFAULT_TASK_TYPE = "feature"


TEMPLATE_FILE = "_TEMPLATE.md"

# Заголовок секции → чем заполнить её тело при создании задачи.
# Остальные секции шаблона (комментарии, история коммитов) переезжают
# в новую задачу как есть
_FILLED_SECTIONS = ("## Описание", "### Критерии приёмки")


# Заголовок секции критериев: автор приносит её прямо в описании, когда
# оформляет текст по правилам проекта
_CRITERIA_HEADING = "### Критерии приёмки"

# Забор блока кода: ``` или ~~~ с отступом не больше трёх пробелов
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def normalize_text(text: str) -> str:
    """Текст автора — это markdown, и портить его нельзя.

    Единственное, что здесь делается, — приведение переводов строк. Скрипт
    **ничего не вставляет**: перенос внутри абзаца остаётся мягким, абзацы
    разделяет пустая строка, список остаётся списком.

    Прежняя версия вставляла пустую строку между соседними непустыми строками,
    чтобы одиночный перенос автора стал абзацем. На размеченном тексте это
    ломало ровно то, ради чего его размечали: строка продолжения пункта
    начинается не с маркера, и пункт разваливался на пункт и оторванные абзацы,
    а абзац, перенесённый по ширине строки, — на лесенку обрывков. Отличить
    «Enter вместо абзаца» от «перенос ради читаемости» по одной строке нельзя,
    поэтому правило одно: как написано, так и записано.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_criteria(description: str) -> tuple[str, str]:
    """Отделить от описания секцию «### Критерии приёмки», если автор её принёс.

    Иначе в файле оказываются три блока подряд: авторский, подставленный из
    `-c` и незаполненная заглушка шаблона.

    Заголовок ищется **построчно и вне блоков кода**: описания задач сплошь и
    рядом цитируют куски других файлов, где строки начинаются с `#`.
    """
    lines = normalize_text(description).split("\n")
    fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            fence = not fence
            continue
        if not fence and line.strip() == _CRITERIA_HEADING:
            head = "\n".join(lines[:i]).strip()
            body = "\n".join(lines[i + 1:]).strip()
            return head, body
    return description.strip(), ""


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
                         criteria: str) -> str | None:
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

    for heading, body in zip(_FILLED_SECTIONS, (description, criteria)):
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


def _add_rubric(board_content: str, heading: str, new_entry: str,
                intake_section: str, first: bool = False) -> str | None:
    """Завести рубрику в разделе приёма и положить в неё запись.

    `first` отличает рубрику **источника** (задача из чата) от рубрики типа, и
    отличие двойное:

    - источник встаёт перед остальными рубриками, а не в конец: его смысл в
      том, чтобы попасться на глаза, а в конец длинного бэклога никто не
      смотрит. Место рубрики типа ничего не значит, она идёт в конец;
    - **на доске без подразделов рубрику типа не наводим, а источника —
      заводим.** Плоский бэклог это выбор человека, и навязывать ему таксономию
      незачем; у явной рубрики цель обратная, и без неё задача из чата
      теряется в конце раздела приёма.

    Заголовок забирает себе всё, что стоит под ним, поэтому на плоской доске
    рубрика встаёт **в конце** раздела: прежние записи остаются без рубрики над
    ней, а не оказываются задним числом пришедшими из чата.

    None — если раздела приёма нет вовсе или рубрику типа наводить не на чем.
    """
    marker = re.search(rf"^##\s+{re.escape(intake_section)}\s*$", board_content,
                       flags=re.MULTILINE)
    if not marker:
        return None
    rest = board_content[marker.end():]
    nxt = re.search(r"^##\s+", rest, flags=re.MULTILINE)
    intake = rest[:nxt.start()] if nxt else rest
    head = re.search(r"^### ", intake, flags=re.MULTILINE)
    if not head and not first:
        return None

    if first and head:
        # Перед первой рубрикой, а не в самом верху раздела: записи, лежащие
        # без рубрики, должны такими и остаться, а не въехать в нашу
        at = marker.end() + head.start()
        return (board_content[:at]
                + f"{heading}\n\n{new_entry}\n\n"
                + board_content[at:])

    at = marker.end() + (nxt.start() if nxt else len(rest))
    return (board_content[:at].rstrip()
            + f"\n\n{heading}\n\n{new_entry}\n\n"
            + board_content[at:])


def insert_into_board(board_content: str, new_entry: str, section: str,
                      intake_section: str = "Backlog") -> str:
    """
    Вставить новую задачу в нужный подраздел раздела приёма задач.

    section — заголовок подраздела ### (полный текст) либо ключ типа задачи:
    рубрика бэклога и есть тип, и берётся она из каталога (`--types`).

    Рубрики типа на доске нет — она **заводится**: у досок, развёрнутых до
    появления типов, её нет по построению, а «просто в конец раздела приёма»
    означает «внутрь последнего подраздела» — обсуждения так падали в «Дизайн».
    Доска без подразделов вовсе — решение человека: там рубрики не наводим.
    Fallback → в конец раздела приёма (его имя задаёт пайплайн проекта).

    Пустой `section` — задача без типа: рубрики у неё нет по построению, и
    заводить её не из чего. Такая задача сразу идёт в конец раздела приёма.
    """
    rubric = section_of_type(section) if section else None
    if section:
        heading = f"### {rubric or section}"
        updated = _append_to_block(board_content, heading, new_entry)
        if updated is not None:
            return updated

        # Рубрику заводим и для явного `--section`, а не только для рубрики
        # типа: источник без типа (задача из чата) называет её сам, и без этого
        # запись уезжала в общий fallback — то есть внутрь последней рубрики
        # Такая рубрика встаёт первой: её задача — попасться на глаза
        created = _add_rubric(board_content, heading, new_entry, intake_section,
                              first=rubric is None)
        if created is not None:
            return created

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
    task_type: str = DEFAULT_TASK_TYPE,
    section: str | None = None,
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
    # Рубрика бэклога — это тип задачи: отдельного «куда положить» у неё нет,
    # но нестандартную доску можно указать явным --section
    section = section or task_type

    task_num = get_next_task_number()
    slug = slugify(title)
    filename = f"TASK-{task_num:03d}-{slug}.md"
    created_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    blocked_by_line = f"\nblocked_by: {blocked_by}" if blocked_by else ""
    epic_value = epic if epic else "~"

    tasks_dir = Path(__file__).parent
    cfg = load_config(tasks_dir)
    status_key, intake_section = intake_status(tasks_dir, cfg)

    frontmatter = f"""---
id: TASK-{task_num:03d}
title: {title}
epic: {epic_value}
type: {task_type or "~"}
size: ~
status: {status_key}
created: {created_date}{blocked_by_line}
---"""

    # Структуру задаёт `_TEMPLATE.md` проекта; встроенная копия — запасной
    # вариант для проектов, развёрнутых до его появления
    # Текст автора пишется дословно: он уже markdown (см. normalize_text).
    # Секцию критериев, принесённую в описании, отделяем — иначе она задвоится
    # с подставленной из `-c` и с заглушкой шаблона
    description = normalize_text(description)
    criteria = normalize_text(criteria)
    description, own_criteria = split_criteria(description)
    # Автор оформил критерии сам — его текст и главный: он писал их вместе
    # с описанием и видел целиком
    if own_criteria:
        criteria = own_criteria

    content = render_from_template(tasks_dir, frontmatter, description, criteria)
    if content is None:
        content = f"""{frontmatter}

## Описание

{description}

### Критерии приёмки

{criteria}

## Комментарии

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

    # У зависимости два конца: раз новая задача ждёт другую, у той должна
    # появиться обратная ссылка blocks. Правит их set_status.py — он лежит
    # рядом; нет его (старая поставка) — остаётся одна сторона, и о ней
    # скажет валидатор
    if blocked_by:
        try:
            sys.path.insert(0, str(tasks_dir))
            from set_status import set_blocked_by

            result = set_blocked_by(tasks_dir, f"TASK-{task_num:03d}", blocked_by)
            for missing in result.get("missing", []):
                print(f"[WARN] Блокер не найден в проекте: {missing}")
        except Exception as exc:
            print(f"[WARN] Обратная ссылка blocks не проставлена: {exc}")

    print(f"\n[OK] Задача успешно создана!\n")
    print(f"  ID: TASK-{task_num:03d}")
    print(f"  Файл: tasks/{filename}")
    print(f"  Тип: {task_type or 'не указан'} | Секция: {section or intake_section}")
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
        choices=[*TASK_TYPES, ""],
        default=DEFAULT_TASK_TYPE,
        help="Тип задачи; пустое значение — тип не указан",
    )
    # Рубрику задаёт тип задачи; флаг остаётся ради нестандартных досок,
    # где подразделы названы по-своему
    parser.add_argument(
        "--section",
        default=None,
        help="Подраздел Backlog (по умолчанию — рубрика типа задачи)",
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
