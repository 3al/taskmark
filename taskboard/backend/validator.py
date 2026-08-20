"""Валидация структуры папки tasks/ проекта."""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import parse_board
from backend.board_repair import _section_for_status, row_matches_file, task_files
from backend.config import is_lost_section
from backend.scaffold import (detect_harnesses, environment_issues, harness_choice,
                              script_capabilities)
from backend.requirements import (declaration_issues, exception_gaps_message,
                                  preset_exception_gaps, unknown_requirement_ids,
                                  unreviewed_task_types, unreviewed_types_message)
from backend.stall import plan_blocks_repair, stall_issues
from backend.statuses import load_pipeline

_TASK_ID_RE = re.compile(r"^(TASK-\d+)")

# Как назвать часть окружения в сообщении о пробеле поставки:
# (нет целиком, не хватает элементов, устарело).
# Формулировки одинаковой формы для всех частей — чтобы отсутствие скиллов
# читалось так же серьёзно, как отсутствие скрипта, а не терялось в молчании.
# «Нет» и «устарело» не смешиваются: назвать несуществующий файл устаревшим
# значит отправить пользователя искать, что же в нём поменялось
_PART_WORDING = {
    "create_script": ("Нет {names} — создание задач отключено", "",
                      "{names} устарел — не поддерживает актуальные возможности"),
    "status_script": ("Нет {names} — инструмент агента для управления задачами", "",
                      "{names} устарел — не поддерживает актуальные возможности"),
    "template": ("Нет {names} — эталона структуры задачи, на него ссылаются скиллы", "",
                 "{names} устарел — новые задачи заводятся по старой структуре"),
    "epics": ("Нет {names} — реестру эпиков негде хранить имена", "", ""),
    "logs": ("Нет папки {names} — просмотр логов отключён", "", ""),
    "skills": ("Скиллы не развёрнуты — агент не знает процесса работы с задачами",
               "Не хватает скиллов: {names}",
               "Скиллы устарели: {names}"),
    "commands": ("Команды opencode не развёрнуты: без них среда не видит скиллы",
                 "Не хватает команд opencode: {names}",
                 "Команды opencode устарели: {names}"),
    "rules": ("Правила для агентов не развёрнуты: {names}", "",
              "Правила для агентов устарели: {names}"),
    "vault": ("Волт знаний не развёрнут — скиллы ссылаются на несуществующий vault/",
              "Волт развёрнут не полностью: {names}",
              "Правила и шаблоны волта устарели: {names}"),
}

_STATE_INDEX = {"missing": 0, "partial": 1, "outdated": 2}


def _issue_message(issue: dict) -> str:
    """Сообщение о пробеле поставки по реестру частей."""
    wording = _PART_WORDING[issue["part"]]
    template = wording[_STATE_INDEX[issue["state"]]]
    return template.format(names=", ".join(issue["names"]))


def validate_project(tasks_dir: Path, cfg: dict) -> dict:
    """
    Проверить структуру tasks/.

    Возвращает отчёт:
      ok           — можно работать полноценно
      critical     — список критичных проблем (read-only режим)
      degraded     — деградация функционала (off кнопки/вкладки)
      warnings     — мягкие предупреждения (панель "Проблемы данных")
      features     — доступные возможности: create_task, logs, queue_section
    """
    critical: list[str] = []
    degraded: list[dict] = []
    warnings: list[str] = []
    features = {"create_task": False, "logs": False, "queue_section": False}

    board_file = cfg.get("board_file", "board.md")
    create_script = cfg.get("create_script", "create_task.py")
    logs_dir = cfg.get("logs_dir", "logs")

    # Критичные проверки
    if not tasks_dir.is_dir():
        critical.append(f"Папка задач не найдена: {tasks_dir}")
        report = _report(critical, degraded, warnings, features)
        # Отдельный маркер: структуры нет совсем — UI предложит scaffold
        report["structure"] = "missing"
        return report

    board_path = tasks_dir / board_file
    if not board_path.is_file():
        critical.append(f"Файл доски не найден: {board_path}")
        report = _report(critical, degraded, warnings, features)
        # Папка есть, доски нет — UI предложит scaffold (досоздаст недостающее)
        report["structure"] = "no_board"
        return report

    pipeline = load_pipeline(cfg)
    try:
        board = parse_board(board_path, pipeline)
    except Exception as exc:
        critical.append(f"Не удалось распарсить {board_file}: {exc}")
        return _report(critical, degraded, warnings, features)

    known = {t.lower() for t in board["known_sections"]}

    # Раздел создания задач критичен: без него новую задачу некуда положить
    create_status = pipeline.action("create")
    create_section = pipeline.section_of(create_status) if create_status else None
    if not create_section:
        critical.append("Пайплайн статусов пуст: нечего показывать на доске")
    elif create_section.lower() not in known:
        critical.append(f"В {board_file} нет раздела {create_section}")

    # Раздел, откуда берут работу (живая очередь)
    pick_section = pipeline.section_of(pipeline.action("pick") or "")
    features["queue_section"] = bool(pick_section) and pick_section.lower() in known

    # Разделы пайплайна, которых на доске нет: колонки не будет, пока не создадим.
    # Это деградация, а не мягкое предупреждение: раздел создаётся одной
    # кнопкой, а без кода строка оставалась жалобой без выхода (TASK-129)
    missing = [s["section"] for s in pipeline.statuses()
               if s["section"].lower() not in known]
    if missing:
        degraded.append({
            "code": "no_board_sections",
            "message": ("Нет разделов доски для статусов пайплайна: " + ", ".join(missing)),
            "names": missing})

    # Разделы вне пайплайна не трогаем молча — показываем как есть и предупреждаем.
    # Кроме технического раздела: его завела сама починка, жаловаться на него —
    # значит выдавать новый баннер за устранение старых
    extra = [t for t in board["known_sections"]
             if pipeline.status_for_section(t) is None and not is_lost_section(t, cfg)]
    if extra:
        warnings.append("Разделы вне пайплайна статусов: " + ", ".join(extra))

    # Деградация функционала (code — для точечного восстановления из UI).
    # Возможности зависят от наличия инструментов, сами проблемы полноты
    # поставки перечисляет реестр частей окружения
    features["create_task"] = (tasks_dir / create_script).is_file()
    features["set_status"] = (tasks_dir / cfg.get("status_script", "set_status.py")).is_file()
    features["logs"] = (tasks_dir / logs_dir).is_dir()

    # Пока среды не выбраны, про их части молчим: спрашиваем выбор один раз
    if harness_choice(cfg) is None:
        degraded.append({"code": "no_harness_choice",
                         "message": "Не выбраны среды агентов — окружение проверяется не полностью"})

    for issue in environment_issues(tasks_dir, cfg):
        degraded.append({"code": issue["code"],
                         "message": _issue_message(issue),
                         "names": issue["names"]})

    # Настройка, опередившая развёрнутый скрипт: требования этапов объявлены, а
    # исполняет их он, и обновляется он отдельно — кнопкой. Это деградация, а не
    # мягкое расхождение данных: объявленная проверка не работает, и починка у
    # неё та же самая кнопка. Молчать нельзя — человек считал бы этап
    # защищённым, а гейта не было бы вовсе.
    # Файла нет вовсе — про это уже сказано своей строкой с «Создать»: вторая
    # предлагала бы обновить то, чего не существует
    status_script = cfg.get("status_script", "set_status.py")
    if (cfg.get("requires") and (tasks_dir / status_script).is_file()
            and "requires" not in script_capabilities(tasks_dir, cfg)):
        degraded.append({
            "code": "requires_unsupported",
            "message": (f"Этапы требуют проверок (ключ requires в "
                        f"tasks/.taskboard.json), но развёрнутый {status_script} "
                        f"про них не знает: агент проходит этапы, а проверка молчит"),
            "names": [status_script]})

    # Снимок требований, отставший от поставки: исключение по типу задачи там
    # появилось, а в настройках проекта осталось прежним. Молчать нельзя —
    # задача нового типа упирается в отказ, который сам себя не объясняет
    # («сделать и повторить», хотя делать нечего). Чинится дописыванием, а не
    # приведением к эталону: requires принадлежат человеку
    gaps = preset_exception_gaps(cfg, pipeline.statuses())
    if gaps:
        degraded.append({"code": "requires_exceptions_stale",
                         "message": exception_gaps_message(gaps),
                         "names": [g["id"] for g in gaps]})

    # Требование, придуманное человеком, дописать за него нельзя: применимо оно
    # к новому типу или нет, знает только он. Поэтому не чиним, а спрашиваем —
    # и спрашиваем один раз, нажатие записывает типы как просмотренные
    unreviewed = unreviewed_task_types(cfg, pipeline.statuses())
    if unreviewed:
        degraded.append({"code": "requires_types_unreviewed",
                         "message": unreviewed_types_message(unreviewed),
                         "names": unreviewed})

    # Битая декларация требований: движок на ней молчит и пропускает этап
    # (fail-open — отказ из-за опечатки в конфиге хуже неработающей проверки),
    # поэтому сказать обязан валидатор. Иначе человек считает этап защищённым
    warnings.extend(declaration_issues(cfg, pipeline.statuses()))

    # Мягкие предупреждения: битые ссылки, чужие записи, расхождение раздела
    # со статусом файла и файлы вне доски. Всё это чинится одной кнопкой —
    # см. backend/board_repair.py; по флагу repairable UI решает, показывать
    # ли её на баннере
    repairable = 0
    on_board: set[str] = set()
    files = task_files(tasks_dir)
    for column in board["columns"]:
        # Записи в техническом разделе уже разобраны починкой: файла у них нет
        # по определению, и повторять про них — держать баннер вечно
        if is_lost_section(column["title"], cfg):
            continue
        for group in column["groups"]:
            for task in group["tasks"]:
                info = files.get(task["id"])
                if info is None:
                    warnings.append(f"{task['id']}: файл {task['file']} не найден")
                    repairable += 1
                    continue
                if not row_matches_file(task, info):
                    warnings.append(
                        f"{task['id']}: запись «{task['title']}» не похожа на файл "
                        f"{info['path'].name} — возможно, строка с чужой доски")
                    repairable += 1
                    continue
                on_board.add(task["id"])
                if task["file"] != info["path"].name:
                    warnings.append(
                        f"{task['id']}: ссылка ведёт на {task['file']}, "
                        f"файл переименован в {info['path'].name}")
                    repairable += 1
                elif info["title"] and task["title"].strip() != info["title"]:
                    warnings.append(
                        f"{task['id']}: заголовок в строке «{task['title'].strip()}» "
                        f"не совпадает с заголовком файла «{info['title']}»")
                    repairable += 1
                expected = _section_for_status(pipeline, info["status"])
                if expected and expected.strip().lower() != column["title"].strip().lower():
                    warnings.append(
                        f"{task['id']}: строка в разделе «{column['title']}», "
                        f"а статус в файле ({info['status'] or 'пусто'}) "
                        f"ведёт в «{expected}»")
                    repairable += 1

    for f in sorted(tasks_dir.glob("TASK-*.md")):
        m = _TASK_ID_RE.match(f.name)
        if m and m.group(1) not in on_board:
            warnings.append(f"{f.name}: файла нет на доске")
            repairable += 1
        # Запись о требовании, которого нет в маршруте: этап она не закрывает, а
        # поле выглядит заполненным. Кнопкой это не чинится — запись может быть
        # опечаткой в конфиге, и стёрся бы факт, — поэтому без repairable
        if m and cfg.get("requires"):
            unknown = unknown_requirement_ids(cfg, pipeline.statuses(), f)
            if unknown:
                warnings.append(
                    f"{m.group(1)}: в подтверждениях и списаниях не опознано — "
                    f"{', '.join(unknown)}: таких требований в маршруте нет, "
                    f"и этап они не закрывают")

    # Настройка, опередившая развёрнутый скрипт: требования этапа объявлены,
    # а исполняет их он, и обновляется он отдельно — кнопкой. Молчать тут
    # нельзя: доска показывала бы долг, которого скрипт не проверяет, то есть
    # человек считал бы этап защищённым, а гейта не было бы вовсе
    # Причины простоя (blocked_by / blocks / paused) живут во frontmatter, и
    # починка доски их не касается: битую ссылку и разъехавшиеся концы правит
    # set_status.py, а не перенос строк. Поэтому в repairable они не идут
    warnings.extend(stall_issues(tasks_dir, cfg))
    # Односторонние связи — то же расхождение двух концов, только оба во
    # frontmatter: чинятся той же кнопкой, значит и считаются в repairable
    repairable += len(plan_blocks_repair(tasks_dir))

    report = _report(critical, degraded, warnings, features)
    report["repairable"] = repairable
    # Выбор сред и предзаполнение диалога: по нему UI решает, спрашивать ли
    report["harnesses"] = {"choice": harness_choice(cfg),
                           "detected": detect_harnesses(tasks_dir.parent)}
    return report


def _report(critical: list, degraded: list, warnings: list, features: dict) -> dict:
    return {
        "ok": not critical,
        "structure": "present",
        "critical": critical,
        "degraded": degraded,
        "warnings": warnings,
        "features": features,
    }
