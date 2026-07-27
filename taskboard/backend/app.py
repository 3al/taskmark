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

from backend import lifecycle, registry
from backend.board_parser import parse_board
from backend.config import (PROJECT_KEYS, load_global_config, load_project_config,
                            save_global_config, save_project_config)
from backend.create_task_runner import create_task
from backend.epics import list_epics, register_epic
from backend.migrations import apply_config_migrations, pipeline_removals
from backend.queue_ops import ensure_section, move_task
from backend.scaffold import agentic_diff, agentic_stale_details, scaffold_project
from backend.statuses import CATALOG, load_pipeline
from backend.task_parser import parse_task
from backend.validator import validate_project
from backend.watcher import TasksWatcher

DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"

# Возможности API: лаунчер сверяет их при подключении к уже запущенному
# серверу, чтобы предупредить об устаревшем процессе
CAPABILITIES = {"move_after_task_id": True, "server_lifecycle": True,
                "move_group": True, "scaffold": True, "agentic_diff": True}

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


class ConfigIn(BaseModel):
    updates: dict
    # Куда переносить задачи выключаемых статусов: {статус: новый статус}
    moves: dict | None = None


class ScaffoldIn(BaseModel):
    skills: bool = True
    commands: bool = True
    rules_agents: bool = True
    rules_claude: bool = True
    vault: bool = False
    # Точечное восстановление: создать только перечисленные части
    # (board | create_script | status_script | epics | gitignore | logs |
    #  skills | commands | rules)
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

@app.get("/api/config")
def api_get_config() -> dict:
    """Действующий конфиг: для активного проекта — с его переопределениями."""
    proj = registry.get_active()
    if proj:
        return load_project_config(Path(proj["tasks_dir"]))
    return load_global_config()


@app.post("/api/config")
def api_save_config(body: ConfigIn) -> dict:
    # Защита от мусора: разрешаем только известные ключи
    allowed = {"port", "theme", "dnd_full_board", "tasks_dir", "board_file",
               "create_script", "status_script", "logs_dir", "queue_section",
               "queued_status", "statuses", "pipeline", "actions"}
    updates = {k: v for k, v in body.updates.items() if k in allowed}

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
                "capabilities": CAPABILITIES, "tool_dir": tool_dir}
    tasks_dir = Path(proj["tasks_dir"])
    cfg = load_project_config(tasks_dir)
    report = validate_project(tasks_dir, cfg)
    report["ok"] = report["ok"] and True
    return {"ok": report["ok"], "project": proj, "config": cfg, "report": report,
            "capabilities": CAPABILITIES, "tool_dir": tool_dir}


@app.get("/api/board")
def api_board() -> dict:
    tasks_dir, cfg, report = _validate_or_400()
    pipeline = load_pipeline(cfg)
    board = parse_board(tasks_dir / cfg.get("board_file", "board.md"), pipeline)
    board["report"] = report
    board["config"] = {
        # Фронт рисует колонки, порядок, цвета и правила DnD по пайплайну;
        # queue_section/queued_status оставлены для старых сборок фронта
        "pipeline": pipeline.statuses(),
        "actions": pipeline.actions(),
        "queue_section": pipeline.section_of(pipeline.action("pick") or "") or "Queue",
        "queued_status": pipeline.action("pick"),
        "dnd_full_board": cfg.get("dnd_full_board", False),
    }
    return board


@app.get("/api/epics")
def api_epics() -> dict:
    """Реестр эпиков проекта — подсказки при создании задачи."""
    tasks_dir, _cfg = _ctx()
    return {"items": list_epics(tasks_dir)}


@app.get("/api/task/{task_id}")
def api_task(task_id: str) -> dict:
    tasks_dir, _cfg = _ctx()
    task = parse_task(tasks_dir, task_id)
    if not task:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    return task


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


@app.post("/api/tasks/{task_id}/move")
def api_move(task_id: str, body: MoveIn) -> dict:
    tasks_dir, cfg, _report = _validate_or_400()
    result = move_task(tasks_dir, cfg, task_id, body.to_section, body.position,
                       body.after_task_id, body.group)
    if not result.get("ok"):
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


@app.get("/api/agentic/stale")
def api_agentic_stale() -> dict:
    """Подробности по устаревшему агентскому окружению активного проекта."""
    tasks_dir, cfg = _ctx()
    return {"items": agentic_stale_details(tasks_dir.parent, cfg)}


@app.get("/api/agentic/diff")
def api_agentic_diff(part: str, name: str) -> dict:
    """Unified diff «развёрнутое → эталон» для скилла, команды или правил."""
    tasks_dir, cfg = _ctx()
    if part not in ("skills", "commands", "rules"):
        raise HTTPException(400, f"Неизвестная часть: {part}")
    result = agentic_diff(tasks_dir.parent, part, name, cfg)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Элемент не найден"))
    return result


@app.post("/api/scaffold")
def api_scaffold(body: ScaffoldIn | None = None) -> dict:
    """Развернуть структуру tasks/ и агентское окружение в активном проекте."""
    tasks_dir, cfg = _ctx()
    options = body.model_dump() if body else {}
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


@app.on_event("shutdown")
def _shutdown() -> None:
    # Разорвать SSE-подписки: иначе открытый EventSource браузера
    # не даёт uvicorn завершить процесс при reload
    watcher.shutdown()
