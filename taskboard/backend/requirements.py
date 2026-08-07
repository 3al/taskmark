"""Требования этапа: зеркало движка из `templates/tasks/set_status.py` (TASK-110).

Правила живут в двух местах не по недосмотру: скрипт автономен — он работает без
сервера, в том числе в проектах, где taskboard не установлен, — и импортировать
бэкенд не может. Бэкенду те же вердикты нужны, чтобы рисовать долг на карточках
доски.

**Расхождение зеркал — худшее, что здесь может случиться**: доска покажет один
долг, а агент упрётся в другой, и доверие теряется к обоим. Поэтому здесь лежит
**копия** блока, обособленного в скрипте маркерами
`# --- НАЧАЛО БЛОКА: требования этапа ---`, а не самостоятельная реализация: правя
одну сторону, правь и вторую. Равенство вердиктов проверяется тестом
`tests/test_requirements_mirror.py` — он прогоняет один набор задач через обе.

Отличия от оригинала только в хелперах: там свои разборщики файла задачи, здесь —
`backend.task_parser` и `backend.statuses`. Сами правила совпадают дословно.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.notes import append_note
from backend.statuses import load_pipeline
from backend.task_parser import find_task_file, parse_frontmatter, set_meta_fields

CONFIRMED_FIELD = "confirmed"
WAIVED_FIELD = "waived"

EMPTY = "~"

# Словарь предикатов: имя → что предикат просит и как он читается человеком.
# Нужен редактору требований (иначе список предикатов знает только код движка) и
# валидатору, чтобы отличить опечатку от рабочей декларации. `param` — имя ключа,
# без которого предикат проверяет несуществующее; None — параметра нет вовсе.
# Состав закрытый и сверяется со скриптом тестом: разойдись он, редактор предложит
# то, чего движок не умеет
# `ask_label` — нужна ли требованию своя формулировка и как подписать поле.
# Нужна там, где по самому требованию не видно, о чём речь: у подтверждения
# (что именно подтверждают) и у поля frontmatter (`mr` — техническое имя).
# У секции наоборот: её заголовок и есть человеческий текст, и выдумывать к нему
# псевдоним значит писать одно и то же дважды
# `phrase` — как проверка читается вместе со своим параметром. Склеивать её в UI
# из подписи и значения нельзя: выходит «секция есть в файле задачи: «Изменение
# для пользователя»» вместо «секция «Изменение для пользователя» есть в файле
# задачи». Фраза — такая же часть словаря, как и подпись
PREDICATES: dict[str, dict] = {
    "confirm": {"label": "подтверждение человека", "param": None,
                "ask_label": "что подтверждает человек",
                "hint": "закрывается кнопкой на доске или командой агента"},
    "checklist_done": {"label": "чеклист задачи закрыт", "param": None,
                       "hint": "в разделе «Чеклист» нет незакрытых пунктов"},
    "section_present": {"label": "секция есть в файле задачи", "param": "name",
                        "param_label": "заголовок секции",
                        "phrase": "секция «{}» есть в файле задачи",
                        "hint": "важен лишь факт наличия секции"},
    "section_filled": {"label": "секция непуста", "param": "name",
                       "param_label": "заголовок секции",
                       "phrase": "секция «{}» непуста",
                       "hint": "в секции есть хотя бы одна строка"},
    "field": {"label": "поле задачи заполнено", "param": "name",
              "param_label": "имя поля frontmatter",
              "phrase": "поле «{}» заполнено",
              "ask_label": "формулировка для человека",
              "hint": "например epic или ссылка на MR"},
}


def gate_impact(tasks_dir, old_cfg: dict, new_cfg: dict) -> list[dict]:
    """Живые задачи, у которых от нового состава требований появится долг.

    Требование действует **задним числом**: задача, прошедшая этап раньше, упрётся
    на следующем движении вперёд. Это то, ради чего механизм и заводился (иначе он
    не ловит ровно тот случай, что его породил), но цену человек должен видеть до
    нажатия, а не узнавать от агента через день.

    Считаем разницей долгов: что было при старом конфиге и что станет при новом.
    Так снятие требования никого не «задевает», а порог `since:` не нужен —
    достаточно показать список.
    """
    from backend.board_repair import task_files

    directory = Path(tasks_dir)
    out: list[dict] = []
    for task_id, info in task_files(directory).items():
        was = {r["id"] for r in task_debt(directory, task_id, old_cfg).get("debt", [])}
        now = task_debt(directory, task_id, new_cfg).get("debt", [])
        added = [r for r in now if r["id"] not in was]
        if added:
            out.append({"id": task_id, "title": info.get("title", ""),
                        "status": info.get("status", ""),
                        "requirements": [requirement_text(r) for r in added]})
    return out


def declaration_issues(cfg: dict, pipeline: list[dict] | None = None) -> list[str]:
    """Что в объявленных требованиях не сработает — человеческими словами.

    Движок на непонятной декларации **молчит и пропускает** (fail-open): отказ
    посреди работы из-за опечатки в конфиге хуже, чем неработающая проверка. Цена
    в том, что человек считает этап защищённым, — поэтому сказать обязан кто-то
    другой, и это валидатор.
    """
    requires = cfg.get("requires") or {}
    if not isinstance(requires, dict):
        return ["Требования этапов заданы не объектом: ключ requires должен быть "
                "словарём «статус → список требований»"]

    known = {s["key"] for s in (pipeline or load_pipeline(cfg).statuses())}
    out: list[str] = []
    for status, reqs in requires.items():
        if status not in known:
            out.append(f"Требования объявлены для статуса «{status}», которого нет "
                       f"в маршруте проекта: они не сработают никогда")
            continue
        seen: set[str] = set()
        # Перебираем сырой список, а не через `_req_list`: тот отбрасывает записи
        # без `id` — для движка их не существует, и именно об этом надо сказать
        if not isinstance(reqs, (list, tuple)):
            out.append(f"Требования этапа «{status}» заданы не списком")
            continue
        for req in reqs:
            if not isinstance(req, dict):
                out.append(f"Требование этапа «{status}» задано не объектом: "
                           f"нужны ключи id и check")
                continue
            rid = _one_line(req.get("id"))
            check = _one_line(req.get("check"))
            where = f"«{status}»"
            if not rid:
                out.append(f"Требование этапа {where} без идентификатора: его нечем "
                           f"отметить выполненным")
                continue
            if rid.lower() in seen:
                out.append(f"Требование «{rid}» на этапе {where} объявлено дважды: "
                           f"отметка закроет оба сразу")
            seen.add(rid.lower())
            spec = PREDICATES.get(check)
            if spec is None:
                out.append(f"Требование «{rid}» на этапе {where}: проверка "
                           f"«{check or '—'}» неизвестна, этап пропустят молча")
                continue
            param = spec.get("param")
            if param and not _one_line(req.get(param)):
                out.append(f"Требование «{rid}» на этапе {where}: для проверки "
                           f"«{spec['label']}» нужен {spec.get('param_label', param)}")
    return out


def _one_line(text) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return "" if text == EMPTY else text


def parse_req_ids(value) -> list[str]:
    """Идентификаторы требований из поля frontmatter — как их написал человек."""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    out: list[str] = []
    for part in re.split(r"[,\s]+", str(value or "")):
        part = part.strip()
        if not part or part == EMPTY or part.lower() in [o.lower() for o in out]:
            continue
        out.append(part)
    return out


def _rows(pipeline) -> list[dict]:
    """Пайплайн списком статусов.

    Бэкенд носит его объектом `Pipeline`, автономный скрипт — списком словарей.
    Правила зеркал должны читаться одинаково, поэтому приводим на входе, а не
    ветвимся внутри.
    """
    return pipeline.statuses() if hasattr(pipeline, "statuses") else list(pipeline)


def _req_list(value) -> list[dict]:
    """Объявления требований из конфига: без `id` требование не существует."""
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(r) for r in value if isinstance(r, dict) and _one_line(r.get("id"))]


def requirement_text(req: dict) -> str:
    """Голая формулировка требования, как её написал человек в конфиге.

    По смыслу это утверждение о выполненном («проверку подтвердил человек»,
    «тексты релиза утверждены»), поэтому в интерфейсе она читается придаточным:
    «этап считается пройденным, если: …». Без кавычек и идентификатора —
    идентификатор нужен тому, кто зовёт скрипт, а не тому, кто читает диалог.
    """
    return _one_line(req.get("ask") or req.get("name") or req.get("id"))


def requirement_wording(req: dict) -> str:
    """Как требование называется агенту: формулировка плюс идентификатор.

    Идентификатор здесь обязателен — им гасят требование (`--confirm <id>`),
    и отказ, не назвавший его, заставляет лезть в конфиг.
    """
    return f"«{requirement_text(req)}» ({_one_line(req.get('id'))})"


def _requirement_kind(req: dict) -> tuple[str, str]:
    """Отпечаток требования: что проверяется и у чего. Зеркало функции скрипта.

    По `id` декларации не сопоставляются: идентификатор человек придумывает сам,
    и вытеснение по имени заставляло угадывать идентификатор рекомендации из
    каталога (TASK-135). Смысл задают предикат и его параметр.
    """
    return (_one_line(req.get("check")).lower(), _one_line(req.get("name")).lower())


def stage_requirements(cfg: dict, pipeline: list[dict], status: str) -> list[dict]:
    """Что этап просит на выходе: объявленное проектом и рекомендованное каталогом.

    `mandatory` — объявлено в `requires` и потому даёт отказ у скрипта; иначе
    рекомендация. Объявленное вытесняет рекомендацию **того же смысла**.
    """
    meta = next((s for s in _rows(pipeline) if s["key"] == status), {})
    # `stage` — чьё это требование. Долг копится с разных этапов, и назвать
    # чужой (тот, откуда задачу двигают) значит соврать в тексте отказа
    stage = {"stage": status, "stage_label": meta.get("label", status)}
    declared = [dict(r, mandatory=True, **stage)
                for r in _req_list((cfg.get("requires") or {}).get(status))]
    seen = {_requirement_kind(r) for r in declared}
    seen |= {_one_line(r.get("id")).lower() for r in declared}
    recommended = [dict(r, mandatory=False, **stage)
                   for r in _req_list(meta.get("recommends"))
                   if _requirement_kind(r) not in seen
                   and _one_line(r.get("id")).lower() not in seen]
    return declared + recommended


# Секцию ищем сами, а не через task_parser.section_bounds: тот заточен под
# точные заголовки редактируемых секций, а имя сюда пишет человек в конфиге —
# и «история коммитов» с маленькой буквы должна работать так же, как «История
# коммитов». Зеркало `_section_bounds` скрипта: он сравнивает так же, и
# расхождение давало доске один вердикт, а агенту другой
_HEADING_RE = re.compile(r"^##\s+(.*)$")


def _section_body(content: str, name: str) -> str | None:
    """Тело секции `## name` (регистр и отступы не важны) либо None."""
    lines = content.splitlines()
    needle = (name or "").strip().lower()
    start = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        if start is None and m.group(1).strip().lower() == needle:
            start = i
        elif start is not None:
            return "\n".join(lines[start + 1:i])
    return "\n".join(lines[start + 1:]) if start is not None else None


def _unchecked_boxes(content: str) -> list[str]:
    body = _section_body(content, "Чеклист")
    if body is None:
        return []
    return re.findall(r"^\s*-\s*\[\s\]\s*(.+?)\s*$", body, flags=re.M)


def _section_filled(content: str, name: str) -> bool:
    body = _section_body(content, name)
    return bool(body and body.strip())


def requirement_met(req: dict, task_path) -> bool:
    """Выполнено ли требование — по одному лишь файлу задачи.

    Неизвестный предикат истинен (fail-open): непонятная декларация не должна
    останавливать работу — про неё скажет валидатор, а не отказ посреди дела.
    """
    try:
        content = Path(task_path).read_text(encoding="utf-8-sig")
    except OSError:
        return True
    meta, _body = parse_frontmatter(content)

    check = _one_line(req.get("check"))
    name = _one_line(req.get("name"))
    if check == "checklist_done":
        return not _unchecked_boxes(content)
    if check == "section_present":
        return bool(name) and _section_body(content, name) is not None
    if check == "section_filled":
        return bool(name) and _section_filled(content, name)
    if check == "field":
        return bool(name) and bool(_one_line(meta.get(name)))
    if check == "confirm":
        done = [i.lower() for i in parse_req_ids(meta.get(CONFIRMED_FIELD))]
        return _one_line(req.get("id")).lower() in done
    return True


def _applies_to_type(req: dict, task_type: str) -> bool:
    """Относится ли требование к задаче этого типа. Зеркало функции скрипта.

    Часть требований бессмысленна по типу работы: «История коммитов» у
    задачи-обсуждения пуста не по недосмотру — коммитов там не будет никогда.
    Хранится исключение (`except_types`), а не белый список: новый тип в поставке
    белый список молча перестал бы покрывать, а исключение молча включит.
    """
    skip = req.get("except_types") or req.get("except_type")
    if isinstance(skip, str):
        skip = [skip]
    skip = [_one_line(t).lower() for t in (skip or [])]
    return not skip or _one_line(task_type).lower() not in skip


def unmet(reqs: list[dict], task_path) -> list[dict]:
    """Требования, ложные сейчас, не списанные и относящиеся к этой задаче."""
    try:
        content = Path(task_path).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    meta, _body = parse_frontmatter(content)
    waived = [i.lower() for i in parse_req_ids(meta.get(WAIVED_FIELD))]
    task_type = meta.get("type") or ""
    return [r for r in reqs
            if _one_line(r.get("id")).lower() not in waived
            and _applies_to_type(r, task_type)
            and not requirement_met(r, task_path)]


def is_terminal(pipeline, status: str) -> bool:
    """Конец маршрута: нет ожидаемого следующего шага."""
    rows = _rows(pipeline)
    keys = [s["key"] for s in rows]
    if not status or status not in keys:
        return False
    idx = keys.index(status)
    if rows[idx].get("offramp"):
        return True
    return not any(not s.get("offramp") for s in rows[idx + 1:])


def crossed(pipeline, status: str) -> list[str]:
    """Этапы, которые задача уже прошла: строго левее текущего, без съездов.

    В съезде и в терминальном статусе пересечения нет вовсе — иначе долг
    навешивается на всю историю проекта задним числом.
    """
    rows = _rows(pipeline)
    keys = [s["key"] for s in rows]
    if status not in keys:
        return []
    if rows[keys.index(status)].get("offramp") or is_terminal(rows, status):
        return []
    return [s["key"] for s in rows[:keys.index(status)] if not s.get("offramp")]


def move_requirements(cfg: dict, pipeline,
                      current: str, target: str) -> list[dict]:
    """Что должно быть закрыто, чтобы задача оказалась в целевом статусе.

    Переход вперёд разрешён, если в целевой позиции долг пуст: смотрим на все
    пройденные этапы, а не на пересекаемый отрезок. Назад и в съезд — никогда.
    """
    rows = _rows(pipeline)
    keys = [s["key"] for s in rows]
    if current not in keys or target not in keys:
        return []
    if rows[keys.index(target)].get("offramp"):
        return []
    if keys.index(target) <= keys.index(current):
        return []
    passed = [s["key"] for s in rows[:keys.index(target)] if not s.get("offramp")]
    return [r for key in passed for r in stage_requirements(cfg, rows, key)]


def task_debt(tasks_dir, task_id: str, cfg: dict, pipeline=None) -> dict:
    """Долг задачи в её нынешнем положении. Долг вычисляется, а не хранится."""
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    rows = _rows(load_pipeline(cfg) if pipeline is None else pipeline)
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    status = _one_line(meta.get("status"))
    reqs = [r for key in crossed(rows, status)
            for r in stage_requirements(cfg, rows, key)]
    pending = unmet(reqs, path)
    return {"ok": True, "task": task_id, "status": status,
            "debt": [r for r in pending if r.get("mandatory")],
            "recommended": [r for r in pending if not r.get("mandatory")]}


def move_debt(tasks_dir, task_id: str, cfg: dict, target: str,
              pipeline=None) -> list[dict]:
    """Долг, с которым задача окажется в целевом статусе.

    Нужен доске **до** переноса: рука человека не гейтится, но цену переноса он
    должен видеть заранее, а не узнавать её от агента через два этапа.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return []
    rows = _rows(load_pipeline(cfg) if pipeline is None else pipeline)
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    current = _one_line(meta.get("status"))
    reqs = move_requirements(cfg, rows, current, target)
    return [r for r in unmet(reqs, path) if r.get("mandatory")]


def reset_confirmations(task_path, cfg: dict, pipeline, target: str) -> list[str]:
    """Снять подтверждения этапов, которые задача пройдёт заново. Вернуть снятые.

    Зеркало `reset_confirmations` из скрипта. Возврат назад — признание, что этап
    не закрыт: подтверждение прошлой итерации к новой не относится. Подтверждения
    этапов **левее** цели остаются, `waived` не трогается (списание — решение о
    самом требовании, а не о степени готовности).
    """
    path = Path(task_path)
    rows = _rows(pipeline)
    keys = [s["key"] for s in rows]
    if target not in keys:
        return []
    again = [s["key"] for s in rows[keys.index(target):] if not s.get("offramp")]
    ids = {_one_line(r.get("id")).lower()
           for key in again for r in stage_requirements(cfg, rows, key)}
    if not ids:
        return []

    try:
        meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return []
    confirmed = parse_req_ids(meta.get(CONFIRMED_FIELD))
    dropped = [i for i in confirmed if i.lower() in ids]
    if dropped:
        kept = [i for i in confirmed if i.lower() not in ids]
        set_meta_fields(path, {CONFIRMED_FIELD: ", ".join(kept) if kept else EMPTY})
    return dropped


def requirement_names(cfg: dict, pipeline, ids: list[str]) -> list[str]:
    """Формулировки требований по идентификаторам — для строк, которые читает человек.

    Нет объявления — остаётся идентификатор: врать нечем.
    """
    out = []
    for req_id in ids:
        req = requirement_by_id(cfg, pipeline, req_id)
        out.append(requirement_text(req) if req else req_id)
    return out


def requirement_by_id(cfg: dict, pipeline, req_id: str) -> dict | None:
    """Найти объявление требования по идентификатору — где бы оно ни стояло.

    Нужно там, где на входе только `id` (подтверждение с доски), а показать
    человеку надо формулировку: идентификатор — служебное имя, и в тексте,
    который читают, ему не место.
    """
    needle = _one_line(req_id).lower()
    for status in [s["key"] for s in _rows(pipeline)]:
        for req in stage_requirements(cfg, pipeline, status):
            if _one_line(req.get("id")).lower() == needle:
                return req
    return None


def confirm_requirements(tasks_dir, task_id: str, ids: list[str],
                         where: str = "", cfg: dict | None = None,
                         pipeline=None) -> dict:
    """Отметить требования подтверждёнными — от имени человека с доски.

    Предикат `confirm` проверяет не «работа сделана», а «**человек сказал**».
    Для агента это гейт: решать за человека он не вправе. Но когда задачу двигает
    сам человек, он и есть тот, чьего подтверждения требуют, — иначе требование
    адресовано ему и невыполнимо им же (TASK-110).

    Остальные предикаты закрываются работой, а не решением, и отсюда не
    закрываются вовсе.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    wanted = [i for i in (parse_req_ids(ids) or []) if i]
    if not wanted:
        return {"ok": True, "task": task_id, "confirmed": []}

    content = path.read_text(encoding="utf-8-sig")
    meta, _body = parse_frontmatter(content)
    current = parse_req_ids(meta.get(CONFIRMED_FIELD))
    have = [i.lower() for i in current]
    added = [i for i in wanted if i.lower() not in have]
    if not added:
        return {"ok": True, "task": task_id, "confirmed": []}

    set_meta_fields(path, {CONFIRMED_FIELD: ", ".join(current + added)})
    # След обязателен: подтверждение без строки в хронологии неотличимо от
    # этапа, пройденного молча.
    # В строке — формулировка требования, а не его идентификатор: заметки читает
    # человек, и служебное имя ему ничего не говорит. Формулировку берём из
    # конфига по id; нет объявления — остаётся id, врать нечем.
    # «на доске» не пишем — это уже сказано подписью строки (`· доска ·`)
    # Без слова «подтверждено»: формулировка требования сама им является
    # («проверку подтвердил человек», «тексты релиза утверждены»), и префикс
    # давал тавтологию
    rows = _rows(load_pipeline(cfg or {}) if pipeline is None else pipeline)
    tail = f" — перенос в «{where}»" if where else ""
    for req_id in added:
        req = requirement_by_id(cfg or {}, rows, req_id)
        what = requirement_text(req) if req else req_id
        append_note(path, f"{what}{tail}")
    return {"ok": True, "task": task_id, "confirmed": added}


def task_waivers(tasks_dir, task_id: str, cfg: dict, pipeline=None) -> list[dict]:
    """Списанные требования задачи: [{id, text}].

    Списание — единственный легальный обход гейта, и оно обязано быть заметным:
    молчаливое неотличимо от честно закрытого этапа. Но заметным **в задаче**, а
    не строкой в проблемах данных: списание — принятое решение, а не расхождение,
    которое чинят, и вечная строка в баннере обесценивала соседние.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return []
    rows = _rows(load_pipeline(cfg) if pipeline is None else pipeline)
    try:
        meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return []
    ids = parse_req_ids(meta.get(WAIVED_FIELD))
    return [{"id": i, "text": t}
            for i, t in zip(ids, requirement_names(cfg, rows, ids))]


def waived_tasks(tasks_dir) -> list[dict]:
    """Задачи, где требование этапа списано: [{id, waived}]. Срез по проекту."""
    tasks_dir = Path(tasks_dir)
    out: list[dict] = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        try:
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        ids = parse_req_ids(meta.get(WAIVED_FIELD))
        if ids:
            out.append({"id": path.name.split("-")[0] + "-" + path.name.split("-")[1],
                        "waived": ids})
    return out


def annotate_debt(tasks_dir, board: dict, cfg: dict, pipeline) -> dict:
    """Проставить карточкам доски их долг.

    В строке board.md его нет — как эпик и простой, он берётся из файлов задач.
    Задачи без долга полей не получают: бейдж нужен только тем, у кого он есть.
    """
    tasks_dir = Path(tasks_dir)
    rows = _rows(pipeline)
    if not (cfg.get("requires") or any(s.get("recommends") for s in rows)):
        return board
    for column in board.get("columns", []):
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                path = tasks_dir / task.get("file", "")
                if not path.is_file():
                    continue
                # У закрытой задачи ни долга, ни списаний не показываем:
                # исторические решения не стоят визуального шума на доске
                meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
                if is_terminal(rows, _one_line(meta.get("status"))):
                    continue
                waived = task_waivers(tasks_dir, task["id"], cfg, rows)
                if waived:
                    task["waived"] = waived
                result = task_debt(tasks_dir, task["id"], cfg, rows)
                debt = result.get("debt") or []
                if debt:
                    task["debt"] = [{"id": r.get("id"), "text": requirement_text(r),
                                     "confirmable": r.get("check") == "confirm"}
                                    for r in debt]
    return board
