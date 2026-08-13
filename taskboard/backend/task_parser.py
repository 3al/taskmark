"""Парсинг файлов задач: frontmatter + markdown-тело."""

from __future__ import annotations

import re
from pathlib import Path

from backend.config import TASK_SIZES, TASK_TYPES, card_style
from backend.notes import (BOARD_AUTHOR, SIZE_TEXT, TITLE_TEXT, TYPE_TEXT,
                           append_note)
from backend.statuses import is_terminal

_TASK_FILE_RE = re.compile(r"^TASK-\d+.*\.md$")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Разобрать YAML-lite frontmatter (key: value между --- строками).

    Возвращает (meta, body). При отсутствии frontmatter — ({}, весь текст).
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end < 0:
        return {}, content

    meta: dict[str, str] = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = content[end + 4:].lstrip("\n")
    return meta, body


def find_task_file(tasks_dir: Path, task_id: str) -> Path | None:
    """Найти файл задачи по id (TASK-NNN)."""
    for f in tasks_dir.glob(f"{task_id}*.md"):
        if _TASK_FILE_RE.match(f.name):
            return f
    return None


# Секции файла задачи, которые правятся из окна доски. Имена — те же, что
# подставляет `create_task.py` (`_FILLED_SECTIONS`): структуру задаёт
# `_TEMPLATE.md`, и второго источника правды у неё нет.
#
# Заголовок отдаётся окну вместе с текстом: по нему оно разрезает тело задачи
# на блоки и понимает, над каким рисовать карандаш. Секции нет в файле —
# правки нет, а не ошибка: задачи бывают старые и урезанные.
EDITABLE_SECTIONS = (
    ("description", "## Описание"),
    ("criteria", "### Критерии приёмки"),
    # Текст изменения для changelog: пишет скилл выпуска, вычитывает человек.
    # В шаблоне новой задачи секции нет — она появляется при отборе в релиз
    ("release_notes", "## Изменение для пользователя"),
)


# Забор блока кода: ``` или ~~~ с отступом не больше трёх пробелов (дальше
# markdown считает строку уже частью списка или кодом-по-отступу)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# План работы: секция необязательная, её заводит агент. Заголовок — константа
# поставки, как и остальные имена секций файла задачи (зеркало в set_status.py)
CHECKLIST_SECTION = "## Чеклист"

# Пункт плана. Регистр галочки не важен: файл правят руками, и `- [X]` —
# та же отметка. Вложенные пункты считаются наравне: это тоже работа
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+\S")


def mask_code_fences(text: str) -> str:
    """Копия текста, где содержимое блоков кода заменено пробелами.

    Длина и переносы сохраняются, поэтому найденный по маске индекс годится
    для исходного текста. Нужна там, где ищут заголовки: строка `## Release
    Notes` внутри примера — часть примера, а не граница секции (TASK-120).
    Незакрытый забор маскирует всё до конца текста: так же его понимает и
    markdown, который рисует остаток блоком кода.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        match = _FENCE_RE.match(stripped)
        if fence is None:
            # Открывающая строка сама заголовком быть не может — маскируем и её
            out.append(" " * len(stripped) + line[len(stripped):] if match else line)
            if match:
                fence = match.group(1)[0] * 3
            continue
        out.append(" " * len(stripped) + line[len(stripped):])
        # Закрывает забор того же вида; хвост info-строки у закрывающей запрещён
        if match and match.group(1).startswith(fence) and not stripped.strip(match.group(1)[0]).strip():
            fence = None
    return "".join(out)


def section_bounds(content: str, heading: str) -> tuple[int, int] | None:
    """Границы тела секции `heading` в тексте: (начало, конец) или None.

    Секция кончается на заголовке **своего или более высокого уровня** — иначе
    «## Описание» обрывалось бы на первом же `### Что делаем`, а правила проекта
    прямо советуют разбивать длинное описание подзаголовками. Соседняя
    редактируемая секция обрывает тоже: «### Критерии приёмки» лежат внутри
    описания, но правятся отдельным полем.
    """
    # Заголовки ищем по маске: внутри блока кода их нет, что бы там ни было
    # написано, — иначе пример с фрагментом доски рвал бы секцию пополам
    masked = mask_code_fences(content)
    start = masked.find(heading + "\n")
    if start < 0:
        return None
    level = len(heading) - len(heading.lstrip("#"))
    body_start = start + len(heading) + 1
    rest = masked[body_start:]

    stops = []
    higher = re.search(rf"^#{{1,{level}}} ", rest, flags=re.M)
    if higher:
        stops.append(higher.start())
    for _key, other in EDITABLE_SECTIONS:
        if other == heading:
            continue
        at = rest.find(other + "\n")
        if at >= 0:
            stops.append(at)
    end = body_start + min(stops) if stops else len(content)
    return body_start, end


def section_body(content: str, heading: str) -> str | None:
    """Тело секции `heading` (без самого заголовка) либо None, если её нет."""
    bounds = section_bounds(content, heading)
    if bounds is None:
        return None
    start, end = bounds
    return content[start:end].strip("\n")


def replace_section(content: str, heading: str, body: str) -> str | None:
    """Заменить тело секции, не трогая остальной файл. None — секции нет."""
    bounds = section_bounds(content, heading)
    if bounds is None:
        return None
    body_start, end = bounds

    body = body.strip("\n")
    # Текст автора пишется дословно: «переносы → абзацы» (`as_paragraphs`)
    # уместны на вводе сырого текста в форме создания, а здесь правят уже
    # оформленный markdown, где перенос внутри абзаца — часть оформления
    filling = f"\n{body}\n\n" if body else "\n"
    return content[:body_start] + filling + content[end:]


def task_sections(content: str) -> list[dict]:
    """Редактируемые секции задачи: [{key, heading, text}] в порядке файла."""
    out: list[dict] = []
    for key, heading in EDITABLE_SECTIONS:
        text = section_body(content, heading)
        if text is not None:
            out.append({"key": key, "heading": heading, "text": text})
    return out


def set_task_section(tasks_dir: Path, task_id: str, key: str, text: str) -> dict:
    """Записать новое тело секции задачи.

    Правка точечная: пока карточка открыта, в тот же файл пишет агент
    (комментарии, история коммитов) — переписывать файл целиком нельзя.
    """
    heading = dict(EDITABLE_SECTIONS).get(key)
    if not heading:
        return {"ok": False, "error": f"Секция {key} не редактируется"}

    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    content = path.read_text(encoding="utf-8")
    updated = replace_section(content, heading, text.replace("\r\n", "\n"))
    if updated is None:
        return {"ok": False, "error": f"В задаче нет секции «{heading.lstrip('# ')}»"}

    # newline="" — файл пишется ровно теми переводами строк, что в тексте:
    # у пользователей встречается CRLF, и удваивать \r нельзя
    path.write_text(updated, encoding="utf-8", newline="")
    return {"ok": True, "task": task_id, "key": key,
            "text": section_body(updated, heading) or ""}


def parse_task(tasks_dir: Path, task_id: str) -> dict | None:
    """Прочитать задачу: meta + markdown-тело + редактируемые секции."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return None
    content = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    return {"id": task_id, "file": path.name, "meta": meta, "body": body,
            "sections": task_sections(content)}


def checklist_progress(body: str) -> dict | None:
    """Сколько пунктов плана закрыто: `{done, total}` либо None.

    Секция «Чеклист» необязательна — её заводит агент под конкретную работу.
    Нет её или пунктов в ней нет — возвращаем None: `0/0` на превью означало бы
    «работа не начата» там, где плана просто не вели.

    **Ни одного закрытого пункта — тоже None.** Полоска отвечает на вопрос «где
    работа идёт»; пустая она говорит ровно то же, что её отсутствие, но занимает
    строку на каждой карточке с планом. Отсюда же и практическая цена: у задач,
    заведённых по старому шаблону, чеклист стоит нетронутым.

    Считаются пункты **этой** секции, а не любые галочки файла: в «Истории
    доработок» живут замечания ревью, и они не про ход работы.
    """
    lines = section_body(body, CHECKLIST_SECTION)
    if lines is None:
        return None
    done = total = 0
    for line in lines.splitlines():
        m = _CHECKBOX_RE.match(line)
        if not m:
            continue
        total += 1
        done += m.group(1).lower() == "x"
    return {"done": done, "total": total} if done else None


def annotate_marks(tasks_dir: Path, board: dict, cfg: dict | None = None,
                   pipeline=None) -> dict:
    """Проставить карточкам доски метки из файла задачи: тип, размер, прогресс.

    В строке board.md их нет — как эпика и простоя, берём из файла. Все три
    читаются **одним проходом**: файл задачи и так открывается на каждой
    отрисовке доски, и второй обход ради соседнего признака — цена, которую
    платит каждое действие агента (доску перерисовывает SSE на любую правку
    в tasks/).

    Задача без поля (заведена до его появления, размер ещё не оценивали, плана
    не вели) и задача с чужим значением метки не получают: пустой кружок на
    превью хуже отсутствующего.

    Показ прогресса выключен настройкой — поля нет вовсе, как у возраста:
    решение «что показывать» принимает бэкенд, а не превью. Там же и второе
    правило возраста: в конце маршрута (терминальный статус, съезд) работа
    окончена, и ход её выполнения на превью — шум того же класса, что долг
    у закрытой задачи.
    """
    tasks_dir = Path(tasks_dir)
    want_progress = card_style(cfg or {})["card_show_progress"]
    for column in board.get("columns", []):
        column_progress = want_progress and not is_terminal(
            pipeline, column.get("status", ""))
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                path = tasks_dir / task.get("file", "")
                if not path.is_file():
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                meta, _body = parse_frontmatter(content)
                key = meta.get("type", "")
                if key in TASK_TYPES:
                    task["type"] = key
                # Регистр приводим здесь: файл правят руками, а `size: l` —
                # тот же размер, а не повод промолчать о нём на доске
                size = str(meta.get("size", "") or "").strip().upper()
                if size in TASK_SIZES:
                    task["size"] = size
                progress = checklist_progress(content) if column_progress else None
                if progress:
                    task["progress"] = progress
    return board


def list_all_tasks(tasks_dir: Path) -> list[dict]:
    """Все задачи проекта: [{id, title}] — для подсказок blocked_by.

    Сортировка по номеру задачи (TASK-001 < TASK-002 < TASK-010).
    """
    if not tasks_dir or not tasks_dir.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        if not _TASK_FILE_RE.match(path.name):
            continue
        match_id = re.match(r"^(TASK-\d+)", path.name)
        if not match_id:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = parse_frontmatter(content)
        results.append({"id": match_id.group(1), "title": meta.get("title", "")})
    return results


def set_meta_fields(path: Path, updates: dict[str, str]) -> bool:
    """Записать поля frontmatter файла задачи. Возвращает успех.

    Поля, которых в шапке нет, дописываются в конец: задачи, заведённые до
    появления поля, не должны требовать ручной правки. Тело файла не трогаем.
    """
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end < 0:
        return False

    header = content[:end]
    for key, value in updates.items():
        line = f"{key}: {value}"
        pattern = rf"^{re.escape(key)}:.*$"
        if re.search(pattern, header, flags=re.MULTILINE):
            # Замена функцией: в значении может быть \1 или обратный слэш —
            # как шаблон подстановки re такую строку либо испортит, либо уронит
            header = re.sub(pattern, lambda _m, ln=line: ln, header, flags=re.MULTILINE)
        else:
            header = header.rstrip("\n") + f"\n{line}"

    path.write_text(header + content[end:], encoding="utf-8")
    return True


def set_task_status(tasks_dir: Path, task_id: str, status: str) -> bool:
    """Обновить status в frontmatter задачи. Возвращает успех."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return False
    return set_meta_fields(path, {"status": status})


def set_task_type(tasks_dir: Path, task_id: str, value: str,
                  author: str = BOARD_AUTHOR) -> dict:
    """Сменить тип задачи — то же, что `set_status.py --type`, но из окна доски.

    Список закрыт: чужое значение молча превратилось бы в задачу без метки.
    Поля может не быть вовсе (задача заведена до его появления) — тогда оно
    дописывается, а не требует правки файла руками.

    Тип задаёт исключения в требованиях этапа, поэтому его смена идёт в
    хронологию: ею объясняется, почему переход прошёл или не прошёл. Повтор
    того же значения событием не считается.
    """
    value = (value or "").strip().lower()
    if value not in TASK_TYPES:
        return {"ok": False,
                "error": f"Неизвестный тип задачи: {value or '(пусто)'} "
                         f"(допустимо: {', '.join(TASK_TYPES)})"}
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    was = str(meta.get("type", "") or "").strip().lower()
    if not set_meta_fields(path, {"type": value}):
        return {"ok": False, "error": f"Не удалось записать тип в {path.name}"}
    if value != was:
        append_note(path, TYPE_TEXT.format(now=value, was=was or "не указан"), author)
    return {"ok": True, "type": value, "label": TASK_TYPES[value]["label"]}


def set_task_size(tasks_dir: Path, task_id: str, value: str,
                  author: str = BOARD_AUTHOR) -> dict:
    """Проставить или снять размер задачи — то же, что `set_status.py --size`.

    Пустое значение **снимает оценку** (`size: ~`): оценка, поставленная
    наугад, хуже её отсутствия, и способ передумать обязан быть. Список
    закрыт — чужое значение молча превратилось бы в задачу без метки.

    Оценка объёма объясняет ход работы («взял, оказалось XL»), поэтому её
    смена идёт в хронологию. Повтор того же значения событием не считается.
    """
    value = (value or "").strip().upper()
    if value and value not in TASK_SIZES:
        return {"ok": False,
                "error": f"Неизвестный размер задачи: {value} "
                         f"(допустимо: {', '.join(TASK_SIZES)})"}
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    was = str(meta.get("size", "") or "").strip().upper()
    was = was if was in TASK_SIZES else ""
    if not set_meta_fields(path, {"size": value or "~"}):
        return {"ok": False, "error": f"Не удалось записать размер в {path.name}"}
    if value != was:
        append_note(path, SIZE_TEXT.format(now=value or "не указан",
                                           was=was or "не указан"), author)
    return {"ok": True, "size": value,
            "label": TASK_SIZES[value]["label"] if value else ""}


def slugify(text: str) -> str:
    """Преобразовать текст в slug для имени файла (как в create_task.py)."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


def normalize_title(text: str) -> str:
    """Название в одну строку: любые пробелы и переносы схлопываются в пробел.

    Заголовок живёт во frontmatter (одна строка `title:`) и в строке доски —
    перевод строки внутри разорвал бы и то, и другое.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def set_task_title(tasks_dir: Path, task_id: str, new_title: str,
                   author: str = BOARD_AUTHOR) -> dict:
    """Обновить title во frontmatter и переименовать файл задачи.

    Возвращает {"ok": True, "title": "...", "file": "TASK-NNN-новый-slug.md"}
    или {"ok": False, "error": "..."}.

    Прежнее название остаётся только в хронологии: файл переименован, строка
    доски переписана, и без записи от старого имени не остаётся следа.
    """
    new_title = normalize_title(new_title)
    if not new_title:
        return {"ok": False, "error": "Название не может быть пустым"}
    # Строка доски — `[Заголовок](файл)`, и `](` внутри заголовка обрывает
    # ссылку раньше времени: одиночные скобки безобидны, эта пара — нет
    if "](" in new_title:
        return {"ok": False, "error": "Название не может содержать «](»"}
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {"ok": False, "error": "Нет frontmatter"}
    end = content.find("\n---", 3)
    if end < 0:
        return {"ok": False, "error": "Нет закрывающего ---"}

    header = content[:end]
    # Замена — функцией: в названии может быть \1 или обратный слэш, и как
    # шаблон подстановки re такую строку либо испортит, либо уронит
    if re.search(r"^title:.*$", header, flags=re.MULTILINE):
        header = re.sub(r"^title:.*$", lambda _m: f"title: {new_title}", header, flags=re.MULTILINE)
    else:
        header += f"\ntitle: {new_title}"

    # Имя файла собирается как в create_task.py; название из одних знаков
    # препинания даёт пустой slug — тогда остаётся голый номер задачи
    slug = slugify(new_title).strip("-")
    new_name = f"{task_id}-{slug}.md" if slug else f"{task_id}.md"
    new_path = path.with_name(new_name)

    if path != new_path and new_path.exists():
        return {"ok": False, "error": f"Файл уже существует: {new_name}"}

    was = str(parse_frontmatter(content)[0].get("title", "") or "").strip()
    path.write_text(header + content[end:], encoding="utf-8")

    if path != new_path:
        path.rename(new_path)

    if new_title != was:
        append_note(new_path, TITLE_TEXT.format(now=new_title, was=was or "не указано"),
                    author)
    return {"ok": True, "title": new_title, "file": new_name}
