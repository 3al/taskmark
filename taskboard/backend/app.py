"""FastAPI-приложение taskboard: API доски, очереди, проектов, логов."""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import changelog, help_docs, lifecycle, registry, updater, version
from backend.board_parser import parse_board
from backend.board_repair import apply_repair, plan_repair, visible_columns
from backend.config import (CARD_LIMITS, PROJECT_KEYS, add_criteria_preset,
                            card_style, criteria_presets,
                            custom_criteria_presets, load_global_config,
                            load_project_config, remove_criteria_preset,
                            save_global_config, save_project_config,
                            validate_card_style)
from backend.create_task_runner import create_task
from backend.epics import annotate_epics, epic_name, list_epics, register_epic
from backend.migrations import (apply_config_migrations, migrate_global_config,
                                pipeline_removals, retire_artifact_names)
from backend.pipeline_sources import list_sources
from backend.queue_ops import ensure_section, move_task, relink_entry, retitle_entry
from backend.scaffold import (HARNESSES, agentic_diff, agentic_stale_details,
                              scaffold_project, uses_vault)
from backend.search import search_tasks
from backend.stall import (annotate_stall, blocker_candidates, can_stall,
                           move_confirmation, set_blocked_by, set_paused,
                           stall_details, stalled_tasks)
from backend.statuses import CATALOG, load_pipeline
from backend.task_parser import (EDITABLE_SECTIONS, list_all_tasks, parse_task,
                                 set_task_section, set_task_title)
from backend.validator import validate_project
from backend.watcher import TasksWatcher

DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"

# Корень установки инструмента: по нему определяется способ обновления
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# Возможности API: лаунчер сверяет их при подключении к уже запущенному
# серверу, чтобы предупредить об устаревшем процессе
CAPABILITIES = {"move_after_task_id": True, "server_lifecycle": True,
                "move_group": True, "scaffold": True, "agentic_diff": True,
                "harnesses": True, "pipeline_sources": True, "help": True,
                "board_repair": True, "stall": True, "update": True}

app = FastAPI(title="taskboard")
watcher = TasksWatcher()


# --- Модели запросов ---

class ProjectIn(BaseModel):
    tasks_dir: str
    name: str | None = None
    activate: bool = True


class ActivateIn(BaseModel):
    name: str


class MoveIn(BaseModel):
    to_section: str
    position: int | None = None
    after_task_id: str | None = None
    group: str | None = None
    # Перенос стоящей задачи в работу подтверждается явно: без признака API
    # отказывает и называет причину — правило одно для всех клиентов
    confirm: bool = False
    # Причина съезда с маршрута (отмены): без неё перенос не выполняется
    reason: str | None = None


class TaskIn(BaseModel):
    title: str
    description: str = ""
    criteria: str = ""
    blocked_by: str = ""
    # Ключ эпика и его имя: имя нужно, только когда ключ ещё не в реестре
    epic: str = ""
    epic_name: str = ""
    task_type: str = "feature"
    section: str = "feature"
    # Куда добавить: backlog (в подраздел section) или сразу в живую очередь
    target: str = "backlog"
    # Позиция в очереди при target=queue: start | end
    queue_position: str = "end"


class TaskUpdateIn(BaseModel):
    title: str | None = None
    # Простой задачи: список блокеров целиком (строкой или списком) и причина
    # паузы. Пустое значение снимает: [] — все блокировки, "" — паузу
    blocked_by: list[str] | str | None = None
    paused: str | None = None
    # Тела редактируемых секций файла задачи, как их набрал человек: пишутся
    # дословно, без «переносы → абзацы» (см. task_parser.replace_section)
    description: str | None = None
    criteria: str | None = None
    # Текст изменения для changelog: черновик пишет скилл выпуска, человек правит
    release_notes: str | None = None


class ConfigIn(BaseModel):
    updates: dict
    # Куда переносить задачи выключаемых статусов: {статус: новый статус}
    moves: dict | None = None


class CriteriaPresetIn(BaseModel):
    text: str


class ScaffoldIn(BaseModel):
    # Среды агентов: {"claude": bool, "opencode": bool}. Передан — выбор
    # пользователя запоминается в конфиге проекта и задаёт состав поставки
    harnesses: dict | None = None
    skills: bool | None = None
    commands: bool | None = None
    rules_agents: bool = True
    rules_claude: bool = True
    vault: bool = False
    # Точечное восстановление: создать только перечисленные части
    # (board | create_script | status_script | epics | gitignore | logs |
    #  skills | commands | rules | vault)
    parts: list[str] | None = None
    # Точечное обновление агентского окружения: только эти скиллы/команды/файлы правил
    names: list[str] | None = None


# --- Вспомогательные ---

def _active_project() -> dict:
    """Активный проект из реестра или 404."""
    proj = registry.get_active()
    if not proj:
        raise HTTPException(404, "Нет активного проекта")
    return proj


def _ctx() -> tuple[Path, dict]:
    """(tasks_dir, config) активного проекта."""
    proj = _active_project()
    tasks_dir = Path(proj["tasks_dir"])
    return tasks_dir, load_project_config(tasks_dir)


def _validate_or_400() -> tuple[Path, dict, dict]:
    tasks_dir, cfg = _ctx()
    report = validate_project(tasks_dir, cfg)
    if not report["ok"]:
        raise HTTPException(400, {"message": "Критические проблемы структуры", "report": report})
    return tasks_dir, cfg, report


# --- Проекты ---

def _resolve_tasks_dir(path: Path) -> Path:
    """Путь при добавлении — корень проекта: tasks/ берётся внутри него.

    Обратная совместимость: путь, сам называющийся tasks или уже
    содержащий board.md, используется как есть.
    """
    if path.name.lower() == "tasks" or (path / "board.md").is_file():
        return path
    return path / "tasks"


@app.get("/api/projects")
def api_projects() -> dict:
    return registry.list_projects()


@app.post("/api/projects")
def api_register(body: ProjectIn) -> dict:
    tasks_dir = _resolve_tasks_dir(Path(body.tasks_dir))
    # Несуществующая папка допустима: структуру можно развернуть из UI (scaffold)
    proj = registry.register_project(tasks_dir, name=body.name, activate=body.activate)
    if body.activate and tasks_dir.is_dir():
        watcher.watch(Path(proj["tasks_dir"]))
    return {"ok": True, "project": proj,
            "structure": "present" if tasks_dir.is_dir() else "missing"}


@app.post("/api/projects/activate")
def api_activate(body: ActivateIn) -> dict:
    proj = registry.activate_project(body.name)
    if not proj:
        raise HTTPException(404, f"Проект не найден: {body.name}")
    watcher.watch(Path(proj["tasks_dir"]))
    return {"ok": True, "project": proj}


@app.delete("/api/projects/{name}")
def api_remove_project(name: str) -> dict:
    if not registry.remove_project(name):
        raise HTTPException(404, f"Проект не найден: {name}")
    # Если удалили активный — перенацелить watcher на новый активный проект
    proj = registry.get_active()
    if proj:
        watcher.watch(Path(proj["tasks_dir"]))
    else:
        watcher.stop()
    return {"ok": True}


# --- Конфиг ---

@app.get("/api/update/status")
def api_update_status() -> dict:
    """Что известно о новой версии. Только из кэша — в сеть тут не ходим."""
    return updater.status(load_global_config(), ROOT_DIR)


@app.post("/api/update/check")
def api_update_check() -> dict:
    """Проверить сейчас. Нажатие кнопки и есть согласие на сетевой запрос."""
    cfg = load_global_config()
    updater.check_remote(cfg, force=True)
    return updater.status(cfg, ROOT_DIR)


@app.get("/api/update/plan")
def api_update_plan() -> dict:
    """Можно ли обновиться по кнопке — и если нет, то по каким причинам."""
    return updater.plan(load_global_config(), ROOT_DIR)


@app.post("/api/update/apply")
def api_update_apply() -> dict:
    """Применить обновление: записать запрос и выйти, передав работу лаунчеру.

    Сам сервер git не трогает: он раздаёт `frontend/dist` из папки, которую
    перезаписывает обновление. Проверки повторяются здесь, а не берутся
    с фронта: кнопку могли нажать, когда преграда уже появилась.
    """
    cfg = load_global_config()
    plan = updater.plan(cfg, ROOT_DIR)
    if not plan["ok"]:
        raise HTTPException(400, {"message": "Обновление сейчас невозможно",
                                  "blockers": plan["blockers"]})

    updater.clear_result()
    updater.request_apply(plan)
    proj = registry.get_active()
    lifecycle.apply_update(int(os.environ.get("TASKBOARD_PORT", 8765)),
                           proj["tasks_dir"] if proj else None)
    return {"ok": True, "version": plan["version"], "tag": plan["tag"]}


@app.post("/api/update/seen")
def api_update_seen() -> dict:
    """Плашку «что нового» показали — больше не показывать."""
    updater.clear_result()
    return {"ok": True}


@app.get("/api/changelog")
def api_changelog(since_version: str = "", limit: int = 0) -> dict:
    """Локальный CHANGELOG.md — «что нового» в уже установленной версии.

    С `since_version` отдаёт **отрезок**: секции строго новее указанной.
    Обновление может перепрыгнуть несколько выпусков, и человеку нужно
    увидеть их все, а не только последний (TASK-099).
    """
    path = ROOT_DIR / "CHANGELOG.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "text": "", "sections": [], "total": 0}
    found = changelog.since(text, since_version)
    # Пропустивший двадцать выпусков получит стену текста, поэтому отдаём
    # свежие, а сколько их всего — числом: окно скажет об остальных словами
    shown = found[:limit] if limit > 0 else found
    return {"ok": True, "text": text, "sections": shown, "total": len(found)}


@app.get("/api/config")
def api_get_config() -> dict:
    """Действующий конфиг: для активного проекта — с его переопределениями.

    Вместе со значениями отдаём `card_limits` — допустимые границы размеров
    превью. Форма ограничивает поля по ним, а не по числам, вписанным в JS:
    иначе границы разъедутся с проверкой бэкенда при первой же правке.
    """
    proj = registry.get_active()
    if not proj:
        return {**load_global_config(), "card_limits": CARD_LIMITS}
    tasks_dir = Path(proj["tasks_dir"])
    cfg = load_project_config(tasks_dir)
    cfg["card_limits"] = CARD_LIMITS
    # Волт мог быть развёрнут до появления ключа в конфиге — тогда его режим
    # виден только по файлам. Отдаём эффективное значение, иначе форма настроек
    # покажет «выключен» и первым же сохранением это в конфиг и запишет
    cfg.setdefault("vault", uses_vault(tasks_dir.parent, cfg))
    return cfg


@app.post("/api/config")
def api_save_config(body: ConfigIn) -> dict:
    # Защита от мусора: разрешаем только известные ключи
    # Имён системных артефактов здесь нет: они перестали быть настройкой
    # (TASK-053) — переименование не доезжало до текстов скиллов и правил
    allowed = {"port", "theme", "dnd_full_board", "tasks_dir", "release_script",
               "statuses", "pipeline", "actions", "harnesses",
               "vault", "update_check", "release_manifest_url",
               *CARD_LIMITS}
    updates = {k: v for k, v in body.updates.items() if k in allowed}

    # Размеры превью проверяет бэкенд, а не только форма: за границами диапазона
    # карточка разваливается, и «настройка» превращается в способ сломать доску
    updates, invalid = validate_card_style(updates)
    if invalid:
        raise HTTPException(400, {"message": "Недопустимые размеры превью",
                                  "errors": invalid})

    proj = registry.get_active()
    tasks_dir = Path(proj["tasks_dir"]) if proj else None
    old_cfg = load_project_config(tasks_dir) if tasks_dir else load_global_config()

    # Настройки проекта (жизненный цикл, имена артефактов) пишем в сам проект,
    # свойства инструмента (порт, тема) — в глобальный конфиг
    project_updates = {k: v for k, v in updates.items() if k in PROJECT_KEYS}
    global_updates = {k: v for k, v in updates.items() if k not in PROJECT_KEYS}

    if global_updates or not tasks_dir:
        save_global_config(global_updates or updates)
    cfg = (save_project_config(tasks_dir, project_updates) if tasks_dir and project_updates
           else (load_project_config(tasks_dir) if tasks_dir else load_global_config()))

    # Переименования мигрируют данные активного проекта вслед за конфигом
    migrations: list[str] = []
    if tasks_dir:
        migrations = apply_config_migrations(tasks_dir, old_cfg, cfg, body.moves)
    return {"config": cfg, "migrations": migrations}


@app.post("/api/config/preview")
def api_preview_config(body: ConfigIn) -> dict:
    """Что произойдёт с задачами при таком изменении конфига.

    Выключение статуса с непустым разделом — не то, что делают молча: UI
    спрашивает, куда переносить задачи, и предлагает предыдущий по порядку.
    """
    proj = registry.get_active()
    if not proj:
        return {"removals": []}
    tasks_dir = Path(proj["tasks_dir"])
    old_cfg = load_project_config(tasks_dir)
    new_cfg = {**old_cfg, **body.updates}
    return {"removals": pipeline_removals(tasks_dir, old_cfg, new_cfg)}


# --- Доска и задачи ---

@app.get("/api/health")
def api_health() -> dict:
    proj = registry.get_active()
    # Расположение инструмента: лаунчер сверяет, чтобы не подключиться
    # к серверу, запущенному из другой (напр. удалённой) папки
    tool_dir = str(Path(__file__).parent.parent.parent.resolve())
    if not proj:
        return {"ok": False, "project": None, "report": None,
                "capabilities": CAPABILITIES, "tool_dir": tool_dir,
                "version": version.current()}
    tasks_dir = Path(proj["tasks_dir"])
    cfg = load_project_config(tasks_dir)
    report = validate_project(tasks_dir, cfg)
    report["ok"] = report["ok"] and True
    return {"ok": report["ok"], "project": proj, "config": cfg, "report": report,
            "capabilities": CAPABILITIES, "tool_dir": tool_dir,
            "version": version.current()}


@app.get("/api/board")
def api_board() -> dict:
    tasks_dir, cfg, report = _validate_or_400()
    pipeline = load_pipeline(cfg)
    board = parse_board(tasks_dir / cfg.get("board_file", "board.md"), pipeline)
    # Технический раздел починки колонкой не показываем: он для записей,
    # у которых не осталось файла, а не для работы
    board["columns"] = visible_columns(board, cfg)
    annotate_epics(tasks_dir, board)
    # Причина простоя есть только во frontmatter — карточке она нужна, чтобы
    # маркер «стоит» рисовался без открытия задачи
    annotate_stall(tasks_dir, board, pipeline)
    board["report"] = report
    board["config"] = {
        # Фронт рисует колонки, порядок, цвета и правила DnD по пайплайну;
        # queue_section/queued_status оставлены для старых сборок фронта
        "pipeline": pipeline.statuses(),
        "actions": pipeline.actions(),
        "queue_section": pipeline.section_of(pipeline.action("pick") or "") or "Queue",
        "queued_status": pipeline.action("pick"),
        "dnd_full_board": cfg.get("dnd_full_board", True),
        # Размеры превью — доска рисует карточки по ним
        "card_style": card_style(cfg),
    }
    return board


@app.get("/api/tasks/list")
def api_tasks_list(blocker_for: str = "") -> dict:
    """Список задач проекта — подсказки для blocked_by.

    С `blocker_for=TASK-NNN` отдаёт только тех, кем эту задачу вообще можно
    заблокировать: без себя, завершённых, отменённых, уже проставленных и
    всех, кто (пусть и через цепочку) ждёт её саму. Считает бэкенд — фронт
    не знает ни графа зависимостей, ни статусов чужих задач.
    """
    tasks_dir, cfg = _ctx()
    if blocker_for:
        return {"items": blocker_candidates(tasks_dir, cfg, blocker_for)}
    return {"items": list_all_tasks(tasks_dir)}


@app.get("/api/search")
def api_search(q: str = "") -> dict:
    """Живой фильтр доски: задачи, в которых встречается запрос."""
    tasks_dir, _cfg = _ctx()
    return {"query": q, "items": search_tasks(tasks_dir, q)}


@app.get("/api/epics")
def api_epics() -> dict:
    """Реестр эпиков проекта — подсказки при создании задачи."""
    tasks_dir, _cfg = _ctx()
    return {"items": list_epics(tasks_dir)}


@app.get("/api/criteria-presets")
def api_criteria_presets() -> dict:
    """Пресеты критериев приёмки: встроенные + сохранённые пользователем.

    custom — подмножество пользовательских: только они удаляемы.
    """
    return {"presets": criteria_presets(), "custom": custom_criteria_presets()}


@app.post("/api/criteria-presets")
def api_add_criteria_preset(body: CriteriaPresetIn) -> dict:
    """Сохранить новый пресет глобально — он доступен из всех проектов."""
    if not body.text.strip():
        raise HTTPException(400, "Пустой пресет")
    return {"presets": add_criteria_preset(body.text),
            "custom": custom_criteria_presets()}


@app.delete("/api/criteria-presets")
def api_remove_criteria_preset(body: CriteriaPresetIn) -> dict:
    """Удалить пользовательский пресет (встроенные остаются)."""
    remove_criteria_preset(body.text)
    return {"presets": criteria_presets(), "custom": custom_criteria_presets()}


@app.get("/api/task/{task_id}")
def api_task(task_id: str) -> dict:
    tasks_dir, cfg = _ctx()
    task = parse_task(tasks_dir, task_id)
    if not task:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    # Во frontmatter лежит ключ, а имя эпика — только в реестре
    task["epic_name"] = epic_name(tasks_dir, task["meta"].get("epic", ""))
    # Состояние простоя — производное от полей, считаем его на бэкенде, чтобы
    # разбор «~», списков и пустых значений жил в одном месте. Блокеры идут
    # с заголовком и статусом: по одному номеру не понять, далеко ли до
    # разблокировки, а фронт файлов задач не читает
    pipeline = load_pipeline(cfg)
    task["stall"] = stall_details(tasks_dir, task["meta"], pipeline)
    # Можно ли вообще ставить простой в этом статусе: в терминальном UI просто
    # не показывает кнопки — подпись про недоступное действие только шумит
    task["stall"]["can_set"] = can_stall(pipeline, task["meta"].get("status", ""))["ok"]
    return task


@app.get("/api/tasks/stalled")
def api_stalled() -> dict:
    """Что сейчас стоит и почему — срез простоя по всем задачам проекта."""
    tasks_dir, _cfg = _ctx()
    return stalled_tasks(tasks_dir)


@app.post("/api/tasks")
def api_create_task(body: TaskIn) -> dict:
    tasks_dir, cfg, report = _validate_or_400()
    if not report["features"]["create_task"]:
        raise HTTPException(400, "Создание задач отключено: нет create_task.py")
    # Новый ключ эпика пополняет реестр: имя эпика хранится только там, и
    # задача не должна ссылаться на эпик, которого никто не знает по имени
    if body.epic.strip():
        register_epic(tasks_dir, body.epic, body.epic_name)

    result = create_task(tasks_dir, cfg, body.model_dump())
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Ошибка создания задачи"))

    # Сразу в живую очередь: задача создаётся в бэклог и переносится
    if body.target == "queue" and result.get("id"):
        position = 0 if body.queue_position == "start" else None
        pipeline = load_pipeline(cfg)
        queue_section = pipeline.section_of(pipeline.action("pick") or "")
        if not queue_section:
            raise HTTPException(400, "В пайплайне нет статуса очереди")
        move = move_task(tasks_dir, cfg, result["id"], queue_section, position)
        if not move.get("ok"):
            raise HTTPException(400, f"Задача создана, но не попала в очередь: {move.get('error')}")
        result["status"] = move.get("status")
    return result


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: str, body: TaskUpdateIn) -> dict:
    """Обновить поля задачи.

    title — переименовывает файл и правит доску; blocked_by и paused — причины
    простоя во frontmatter (статус и раздел доски при этом не меняются).
    """
    tasks_dir, cfg, _report = _validate_or_400()
    board_path = tasks_dir / cfg.get("board_file", "board.md")

    result: dict = {"ok": True}

    if body.title is not None:
        renamed = set_task_title(tasks_dir, task_id, body.title)
        if not renamed.get("ok"):
            raise HTTPException(400, renamed.get("error", "Ошибка переименования"))

        # Файл — источник правды: его переименовали, значит правка состоялась.
        # Строки на доске может не быть (задачу с доски убрали) — сообщаем это
        # флагом, а не ошибкой
        title, new_file = renamed["title"], renamed["file"]
        linked = relink_entry(board_path, task_id, new_file)
        titled = retitle_entry(board_path, task_id, title)
        result.update(
            title=title,
            file=new_file,
            board=bool(linked.get("ok") and titled.get("ok")),
        )

    # Правка текста задачи: каждая секция пишется отдельно и точечно — пока
    # карточка открыта, в тот же файл пишет агент.
    # Список берём из реестра, а не перечисляем здесь: второй источник правды
    # уже приводил к тому, что новая секция до правки не доезжала
    for key, _heading in EDITABLE_SECTIONS:
        text = getattr(body, key, None)
        if text is None:
            continue
        saved = set_task_section(tasks_dir, task_id, key, text)
        if not saved.get("ok"):
            raise HTTPException(400, saved.get("error", "Ошибка правки задачи"))
        result.setdefault("sections", {})[key] = saved["text"]

    if body.blocked_by is not None or body.paused is not None:
        # Простой ставится не в любом статусе: у завершённой или отменённой
        # задачи «ждёт» — мусор в данных. Правило спрашиваем у бэкенда, чтобы
        # UI, API и set_status.py не разъехались в трактовках
        task = parse_task(tasks_dir, task_id)
        status = (task or {}).get("meta", {}).get("status", "")
        verdict = can_stall(load_pipeline(cfg), status)
        # Снятие простоя разрешено всегда: убрать мусор нужно и там, где
        # поставить его уже нельзя
        setting = bool(body.blocked_by) or bool((body.paused or "").strip())
        if setting and not verdict["ok"]:
            raise HTTPException(400, verdict["reason"])

    if body.blocked_by is not None:
        # Список задаётся целиком: у блокеров синхронно правится `blocks`,
        # снятые блокировки со второго конца убираются
        blocked = set_blocked_by(tasks_dir, task_id, body.blocked_by)
        if not blocked.get("ok"):
            raise HTTPException(400, blocked.get("error", "Ошибка простановки блокировки"))
        result["blocked_by"] = blocked["blocked_by"]
        if blocked["missing"]:
            result["missing"] = blocked["missing"]

    if body.paused is not None:
        paused = set_paused(tasks_dir, task_id, body.paused)
        if not paused.get("ok"):
            raise HTTPException(400, paused.get("error", "Ошибка простановки паузы"))
        result["paused"] = paused["paused"]

    return result


@app.post("/api/tasks/{task_id}/move")
def api_move(task_id: str, body: MoveIn) -> dict:
    tasks_dir, cfg, _report = _validate_or_400()
    pipeline = load_pipeline(cfg)

    # Стоящую задачу берут в работу — это и есть то, от чего защищает
    # блокировка. Отказываем, пока клиент не подтвердил явно: тогда правило
    # соблюдает любой клиент, а не только доска (она спрашивает у пользователя
    # и повторяет вызов с confirm)
    target = pipeline.status_for_section(body.to_section) or ""
    if not body.confirm:
        verdict = move_confirmation(tasks_dir, pipeline, task_id, target)
        if verdict["confirm"]:
            raise HTTPException(400, {"code": "stall_confirm",
                                      "message": verdict["reason"]})

    result = move_task(tasks_dir, cfg, task_id, body.to_section, body.position,
                       body.after_task_id, body.group, reason=body.reason)
    if not result.get("ok"):
        # Код отличает «нужна причина отмены» от прочих ошибок: по нему доска
        # открывает поле ввода, а не показывает красную строку
        if result.get("code"):
            raise HTTPException(400, {"code": result["code"], "message": result["error"]})
        raise HTTPException(400, result.get("error", "Ошибка перемещения"))
    return result


@app.post("/api/queue/ensure")
def api_ensure_queue() -> dict:
    """Создать недостающий раздел очереди (кнопка на баннере доски)."""
    tasks_dir, cfg, _report = _validate_or_400()
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    pipeline = load_pipeline(cfg)
    pick = pipeline.action("pick")
    if not pick:
        raise HTTPException(400, "В пайплайне нет статуса очереди")
    ensure_section(board_path, pipeline, pick)
    return {"ok": True}


@app.get("/api/board/repair")
def api_board_repair_plan() -> dict:
    """Что разошлось между доской и файлами задач (предпросмотр починки)."""
    tasks_dir, cfg, _report = _validate_or_400()
    return plan_repair(tasks_dir, cfg)


@app.post("/api/board/repair")
def api_board_repair_apply() -> dict:
    """Применить починку: доска — источник правды, файлы подстраиваются."""
    tasks_dir, cfg, _report = _validate_or_400()
    return apply_repair(tasks_dir, cfg)


@app.get("/api/pipeline")
def api_pipeline() -> dict:
    """Пайплайн активного проекта и каталог доступных статусов (для настроек)."""
    tasks_dir, cfg = _ctx()
    pipeline = load_pipeline(cfg)
    return {
        "pipeline": pipeline.statuses(),
        "actions": pipeline.actions(),
        "catalog": [{"key": key, **meta} for key, meta in CATALOG.items()],
    }


@app.get("/api/pipeline/sources")
def api_pipeline_sources() -> dict:
    """Готовые жизненные циклы: пресеты и пайплайны других проектов реестра."""
    proj = registry.get_active()
    return {"items": list_sources(Path(proj["tasks_dir"]) if proj else None)}


# --- Помощь ---

@app.get("/api/help")
def api_help() -> dict:
    """Разделы помощи: те же файлы docs/help, на которые ссылается README."""
    return {"items": help_docs.list_sections()}


@app.get("/api/help/{section_id}")
def api_help_section(section_id: str) -> dict:
    section = help_docs.get_section(section_id)
    if not section:
        raise HTTPException(404, f"Раздел помощи не найден: {section_id}")
    return section


@app.get("/api/agentic/stale")
def api_agentic_stale() -> dict:
    """Подробности по устаревшему агентскому окружению активного проекта."""
    tasks_dir, cfg = _ctx()
    return {"items": agentic_stale_details(tasks_dir.parent, cfg)}


@app.get("/api/agentic/diff")
def api_agentic_diff(part: str, name: str) -> dict:
    """Unified diff «развёрнутое → эталон» для скилла, команды или правил."""
    tasks_dir, cfg = _ctx()
    if part not in ("skills", "commands", "rules", "vault"):
        raise HTTPException(400, f"Неизвестная часть: {part}")
    result = agentic_diff(tasks_dir.parent, part, name, cfg)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Элемент не найден"))
    return result


@app.post("/api/scaffold")
def api_scaffold(body: ScaffoldIn | None = None) -> dict:
    """Развернуть структуру tasks/ и агентское окружение в активном проекте."""
    tasks_dir, cfg = _ctx()
    # None — «не задано»: пусть решает состав по выбранным средам, а не пустое значение
    options = {k: v for k, v in (body.model_dump() if body else {}).items() if v is not None}
    harnesses = options.get("harnesses")
    if harnesses is not None:
        # Выбор сред и волта делается в диалоге и живёт в конфиге проекта:
        # дальше по нему проверяется полнота поставки и считается эталон
        # скиллов, а сам диалог больше не спрашивают
        cfg = save_project_config(tasks_dir, {
            "harnesses": {h: bool(harnesses.get(h)) for h in HARNESSES},
            "vault": bool(options.get("vault"))})
    result = scaffold_project(tasks_dir, cfg, options)
    watcher.watch(tasks_dir)
    return {"ok": True, **result}


# --- Логи ---

@app.get("/api/logs")
def api_logs() -> dict:
    tasks_dir, cfg = _ctx()
    logs_path = tasks_dir / cfg.get("logs_dir", "logs")
    if not logs_path.is_dir():
        return {"files": []}
    files = [
        {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
        for f in sorted(logs_path.iterdir()) if f.is_file()
    ]
    return {"files": files}


@app.get("/api/logs/{name}")
def api_log(name: str) -> dict:
    tasks_dir, cfg = _ctx()
    path = (tasks_dir / cfg.get("logs_dir", "logs") / name).resolve()
    if not str(path).startswith(str((tasks_dir / cfg.get("logs_dir", "logs")).resolve())):
        raise HTTPException(400, "Недопустимое имя файла")
    if not path.is_file():
        raise HTTPException(404, "Лог не найден")
    return {"name": name, "content": path.read_text(encoding="utf-8", errors="replace")}


# --- Жизненный цикл сервера ---

@app.post("/api/server/stop")
def api_server_stop() -> dict:
    port = int(os.environ.get("TASKBOARD_PORT", "8765"))
    lifecycle.stop_server(port)
    return {"ok": True, "action": "stop"}


@app.post("/api/server/restart")
def api_server_restart() -> dict:
    port = int(os.environ.get("TASKBOARD_PORT", "8765"))
    proj = registry.get_active()
    lifecycle.restart_server(port, proj["tasks_dir"] if proj else None)
    return {"ok": True, "action": "restart"}


# --- SSE ---

@app.get("/api/events")
async def api_events():
    q = watcher.subscribe()

    async def stream():
        try:
            yield "data: connected\n\n"
            while not watcher.stopped.is_set():
                try:
                    msg = await asyncio.to_thread(q.get, timeout=1)
                    if msg == "shutdown":
                        return
                    yield f"data: {msg}\n\n"
                except Exception:
                    yield ": ping\n\n"  # keep-alive
        finally:
            watcher.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


# --- Статика фронтенда ---

if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="frontend")
else:
    @app.get("/")
    def no_frontend() -> dict:
        return {
            "ok": False,
            "message": "Фронтенд не собран: отсутствует taskboard/frontend/dist. "
                       "Запустите taskboard.py — bootstrap предложит сборку (нужен node).",
        }


def start_watcher() -> None:
    """Навесить watcher на активный проект (вызывается при старте сервера)."""
    proj = registry.get_active()
    if proj:
        watcher.watch(Path(proj["tasks_dir"]))


@app.on_event("startup")
def _startup() -> None:
    # В dev-режиме (uvicorn --reload) сервер живёт в подпроцессе,
    # поэтому watcher стартуем через событие, а не извне
    start_watcher()
    # Разовая чистка: прежние версии писали в глобальный конфиг слепок всех
    # дефолтов, и правки поставки переставали доезжать (TASK-088)
    migrate_global_config()
    # Имена системных артефактов перестали быть настройкой (TASK-053):
    # у кого они переименованы, возвращаем к именам поставки. Проходим по
    # всем проектам реестра, а не только по активному, — иначе миграция
    # ждала бы переключения на проект, где скиллы уже сломаны
    for proj in registry.list_projects().get("projects", []):
        try:
            retire_artifact_names(Path(proj["tasks_dir"]))
        except Exception:
            pass
    # Проверка обновлений — фоном и только при согласии (update_check: auto).
    # В путь запроса доски сеть не попадает никогда
    updater.check_in_background(load_global_config())


@app.on_event("shutdown")
def _shutdown() -> None:
    # Разорвать SSE-подписки: иначе открытый EventSource браузера
    # не даёт uvicorn завершить процесс при reload
    watcher.shutdown()
