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

from backend import (autostart, baseline, changelog, help_docs, lifecycle, registry,
                     telegram_intake, telegram_source, updater, version)
from backend.board_parser import annotate_age, annotate_fresh, parse_board
from backend.board_repair import apply_repair, plan_repair, visible_columns
from backend.config import (CARD_FLAGS, CARD_LIMITS, DEFAULT_TASK_TYPE, TELEGRAM_KEYS,
                            PROJECT_KEYS,
                            add_assignee, add_criteria_preset, assignees,
                            card_style, criteria_presets,
                            custom_criteria_presets, load_global_config,
                            load_project_config, remove_criteria_preset,
                            save_global_config, save_project_config,
                            validate_card_style)
from backend.create_task_runner import create_task
from backend.fs_browse import browse_dir
from backend.epics import (annotate_epics, epic_name, epic_tasks, list_epics,
                           register_epic, set_task_epic)
from backend.migrations import (apply_config_migrations, migrate_global_config,
                                pipeline_removals, record_vault_choice,
                                rename_notes_section, retire_artifact_names)
from backend.pipeline_sources import list_sources
from backend.requirements import (KNOWN_TYPES_FIELD, PREDICATES, annotate_debt,
                                  apply_preset_exceptions, confirm_requirements,
                                  gate_impact, is_terminal, move_debt, requirement_text,
                                  task_debt, task_waivers, unreviewed_task_types)
from backend.queue_ops import (ensure_pipeline_sections, ensure_section, move_task,
                               relink_entry, retitle_entry)
from backend.scaffold import (HARNESSES, SINGLE_FILE_PARTS, agentic_diff,
                              agentic_stale_details, remove_element, resolve_element,
                              scaffold_project, uses_vault)
from backend.search import search_tasks
from backend.stall import (annotate_stall, blocker_candidates, can_stall,
                           move_confirmation, set_blocked_by, set_paused,
                           stall_details, stalled_tasks)
from backend.statuses import CATALOG, accepts_assignee, load_pipeline
from backend.tasks_delete import delete_plan, delete_task
from backend.notes import append_note
from backend.task_parser import (EDITABLE_SECTIONS, annotate_marks,
                                 find_task_file, list_all_tasks, parse_task,
                                 set_task_assignee, set_task_section,
                                 set_task_size, set_task_title, set_task_type)
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
                "board_repair": True, "stall": True, "update": True,
                "epic_tasks": True, "agentic_merge": True, "task_copy": True,
                "board_sections": True, "agentic_remove": True,
                "telegram": True, "autostart": True}

app = FastAPI(title="taskboard")
watcher = TasksWatcher()

# Остановка фонового цикла проверки обновлений (ставится при старте)
_stop_update_loop = None
_stop_telegram_loop = None


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
    # Тип задачи. Рубрику бэклога он же и задаёт — отдельного поля «раздел»
    # у формы нет: два способа сказать одно и то же расходились (TASK-124)
    task_type: str = DEFAULT_TASK_TYPE
    # Пауза новой задачи: у копии она наследуется от оригинала (у обычной
    # задачи её нет — форма создания паузу не спрашивает)
    paused: str = ""
    # Куда добавить: в раздел приёма или сразу в живую очередь
    target: str = "backlog"
    # Позиция в очереди при target=queue: start | end
    queue_position: str = "end"


class TaskUpdateIn(BaseModel):
    title: str | None = None
    # Тип задачи: метка «что это за работа». Статус и доску не трогает
    type: str | None = None
    # Размер задачи (S…XL): оценка объёма. Пустая строка снимает оценку —
    # поэтому None («поле не прислали») и "" значат здесь разное
    size: str | None = None
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
    # Эпик задачи: ключ реестра. Пустая строка снимает эпик, поэтому None
    # («поле не прислали») и "" значат здесь разное. Имя нужно, только когда
    # ключ ещё не в реестре, — как и в форме создания
    epic: str | None = None
    epic_name: str = ""
    # Исполнитель: кто занимается задачей на этапе проверки. Список открытый —
    # имя приходит текстом, а известные копятся в глобальном конфиге. Пустая
    # строка снимает назначение, поэтому None и "" здесь тоже значат разное
    assignee: str | None = None


class ConfigIn(BaseModel):
    updates: dict
    # Куда переносить задачи выключаемых статусов: {статус: новый статус}
    moves: dict | None = None


class TelegramCheckIn(BaseModel):
    # Токен приходит из формы, а не из конфига: человек проверяет то, что
    # только что вставил, ещё до сохранения
    token: str


class AutostartIn(BaseModel):
    enabled: bool


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


@app.get("/api/fs/dirs")
def api_fs_dirs(path: str = "") -> dict:
    """Подпапки каталога — для выбора корня проекта мышью.

    Абсолютного пути браузер не отдаёт, поэтому файловую систему показывает
    сервер. Отказ (нет папки, нет доступа) приходит в теле с `ok: false`:
    обход чужих каталогов упирается в них постоянно, и для окна это обычный
    ответ, а не сбой.
    """
    return browse_dir(path)


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
        return {**load_global_config(), "card_limits": CARD_LIMITS,
                "predicates": PREDICATES}
    tasks_dir = Path(proj["tasks_dir"])
    cfg = load_project_config(tasks_dir)
    cfg["card_limits"] = CARD_LIMITS
    # Словарь предикатов: без него редактор требований знал бы список проверок
    # только из зашитого в JS перечня, и тот разошёлся бы с движком молча
    cfg["predicates"] = PREDICATES
    # Волт мог быть развёрнут до появления ключа в конфиге — тогда его режим
    # виден только по файлам. Отдаём эффективное значение, иначе форма настроек
    # покажет «выключен» и первым же сохранением это в конфиг и запишет
    cfg.setdefault("vault", uses_vault(tasks_dir.parent, cfg))
    return cfg


@app.post("/api/config")
def api_save_config(body: ConfigIn) -> dict:
    # Защита от мусора: разрешаем только известные ключи.
    # Проектные ключи берём реестром (PROJECT_KEYS), а не списком: перечисленные
    # руками расходились с ним молча — новая настройка проекта сохранялась в
    # форме, но до конфига не доезжала (TASK-043).
    # Имён системных артефактов здесь нет: они перестали быть настройкой
    # (TASK-053) — переименование не доезжало до текстов скиллов и правил
    allowed = {"port", "theme", "tasks_dir", "update_check",
               "release_manifest_url", "hide_empty_columns",
               *PROJECT_KEYS, *CARD_LIMITS, *CARD_FLAGS, *TELEGRAM_KEYS}
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

    # Включённая возможность должна заработать по кнопке «Сохранить», а не
    # после перезапуска сервера: поллер стартует с конфигом, снятым при старте
    if updates.keys() & TELEGRAM_KEYS:
        restart_telegram_poller()

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

    Второе такое же решение — включение требования этапа: оно действует задним
    числом, и живые задачи, прошедшие этап раньше, упрутся на следующем шаге
    вперёд. Цена показывается до сохранения (`gated`), а не выясняется потом.
    """
    proj = registry.get_active()
    if not proj:
        return {"removals": [], "gated": []}
    tasks_dir = Path(proj["tasks_dir"])
    old_cfg = load_project_config(tasks_dir)
    new_cfg = {**old_cfg, **body.updates}
    return {"removals": pipeline_removals(tasks_dir, old_cfg, new_cfg),
            "gated": gate_impact(tasks_dir, old_cfg, new_cfg)}


# --- Доска и задачи ---

@app.post("/api/telegram/check")
def api_telegram_check(body: TelegramCheckIn) -> dict:
    """Живой ли токен. Имя бота человек должен увидеть своими глазами."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(400, "Токен не введён")
    try:
        me = telegram_source.get_me(token)
    except Exception as exc:  # noqa: BLE001 — сеть или отказ API: скажем как есть
        raise HTTPException(400, f"Бот не отозвался: {exc}")
    return {"ok": True, "username": me.get("username", "")}


@app.get("/api/telegram/chats")
def api_telegram_chats() -> dict:
    """Чаты, которые бот видел с момента запуска.

    Так человек выбирает чат по имени: у групп id — отрицательное число вида
    -1001234567890, и искать его руками мучительно.

    Уже привязанные чаты в списке есть всегда, даже если бот их в этой сессии
    не встречал: иначе настроенная привязка невидима и нередактируема. Имя
    такого чата спрашивается у Bot API один раз и запоминается.
    """
    cfg = load_global_config()
    chats = telegram_source.seen_chats()
    known = {str(chat["id"]) for chat in chats}
    for chat_id in (cfg.get("telegram_chats") or {}):
        if str(chat_id) in known:
            continue
        try:
            chats.append({"id": int(chat_id),
                          "title": telegram_source.chat_title(cfg, chat_id)})
        except (TypeError, ValueError):
            continue  # мусор в привязке — показывать нечего
    return {"ok": True, "chats": chats}


@app.get("/api/autostart")
def api_autostart_status() -> dict:
    """Запускается ли Taskmark при входе в систему."""
    return {"ok": True, **autostart.status(ROOT_DIR)}


@app.post("/api/autostart")
def api_autostart_set(body: AutostartIn) -> dict:
    """Включить или выключить автозапуск.

    Отказ платформы — не ошибка сервера, а сведения для человека: на macOS и
    Linux автозагрузка заводится руками, и текст с командами возвращается ему.
    """
    result = autostart.enable(ROOT_DIR) if body.enabled else autostart.disable()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Не удалось изменить автозапуск"))
    return result


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
    # Метки из файла задачи: тип («что это за работа» — кружок с буквой),
    # размер («сколько тут работы» — буквы S…XL) и прогресс плана работы.
    # В строке board.md их нет, как и эпика; читаются одним проходом
    annotate_marks(tasks_dir, board, cfg, pipeline)
    # Причина простоя есть только во frontmatter — карточке она нужна, чтобы
    # маркер «стоит» рисовался без открытия задачи
    annotate_stall(tasks_dir, board, pipeline)
    # Долг этапа тоже виден только из файла задачи — и только он объясняет,
    # почему агент упрётся при следующем движении вперёд
    annotate_debt(tasks_dir, board, cfg, pipeline)
    # Возраст в статусе берётся из самой строки доски, а вот залежалость —
    # ещё и из времени правки файла: порог считает бэкенд, превью получает
    # уже готовый ответ
    annotate_age(tasks_dir, board, cfg, pipeline)
    # Свежесть — та же величина в другом масштабе: «правят прямо сейчас»
    # меряется минутами, а не днями, и живёт своим полем
    annotate_fresh(tasks_dir, board, cfg, pipeline)
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


@app.get("/api/assignees")
def api_assignees() -> dict:
    """Известные исполнители — подсказки поля в окне задачи.

    Список глобальный, а не проектный: человек ведёт несколько проектов одной
    машины, и заводить одни и те же имена в каждом он не станет.
    """
    return {"items": assignees()}


@app.get("/api/epics")
def api_epics() -> dict:
    """Реестр эпиков проекта — подсказки при создании задачи."""
    tasks_dir, _cfg = _ctx()
    return {"items": list_epics(tasks_dir)}


@app.get("/api/epics/{key}/tasks")
def api_epic_tasks(key: str) -> dict:
    """Состав эпика: его задачи в порядке маршрута проекта.

    Имя эпика отдаём тем же ответом: оно живёт только в реестре, и окну иначе
    пришлось бы ходить за ним вторым запросом. Неизвестный ключ — не ошибка:
    пустой состав и пустое имя, окно покажет это словами.
    """
    tasks_dir, cfg = _ctx()
    pipeline = load_pipeline(cfg)
    return {"key": key, "name": epic_name(tasks_dir, key),
            "tasks": epic_tasks(tasks_dir, key, pipeline)}


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


def _debt_item(req: dict) -> dict:
    """Требование для интерфейса: что это и может ли человек закрыть его сам.

    `confirm` означает «человек сказал» — его закрывает нажатие человека.
    Остальные предикаты закрываются работой, и кнопки для них нет
    """
    return {"id": req.get("id"), "text": requirement_text(req),
            "check": req.get("check"), "confirmable": req.get("check") == "confirm",
            # Требование входа закрывает человек и **сейчас**: обещать «агент
            # закроет позже» про имя исполнителя нельзя — агент его не знает.
            # `todo` — та же суть указанием, а не утверждением о выполненном:
            # в списке дел «исполнитель назначен» читается как «уже есть»
            "entry": bool(req.get("entry")),
            "todo": req.get("todo") or "",
            "stage": req.get("stage_label") or req.get("stage")}


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
    # Спрашивает ли этап исполнителя: правило одно, и считает его бэкенд —
    # окно рисует поле по этому признаку, а не по списку статусов у себя
    task["can_assign"] = accepts_assignee(pipeline, task["meta"].get("status", ""))
    # Долг этапа: поля `confirmed`/`waived` окно рисует поимённо, а долг из них
    # не выводится — он считается по положению задачи и требованиям конфига
    debt = task_debt(tasks_dir, task_id, cfg, pipeline)
    task["debt"] = [_debt_item(r) for r in (debt.get("debt") or [])]
    # Списанные требования: в открытой задаче показываем всегда, в том числе у
    # закрытой — там смотрят историю решений, а не работают
    task["waived"] = task_waivers(tasks_dir, task_id, cfg, pipeline)
    return task


@app.get("/api/tasks/{task_id}/move-debt")
def api_move_debt(task_id: str, section: str) -> dict:
    """С каким долгом задача окажется в целевом разделе.

    Спрашивается доской **до** переноса: рука человека не гейтится, но цену
    движения он должен видеть заранее, а не узнавать её от агента через два
    этапа. Ничего не пишет — это вопрос, а не действие.

    `terminal` — цель в конце маршрута. Список требований там тот же, но
    называть его долгом нельзя: в терминальном статусе долг не считается
    (`crossed`), и обещание «агент закроет позже» неисполнимо — задача
    закрыта, закрывать требование некому. Это, наоборот, последний момент,
    когда его ещё можно выполнить, и окно должно сказать именно так.
    """
    tasks_dir, cfg = _ctx()
    pipeline = load_pipeline(cfg)
    target = pipeline.status_for_section(section)
    if not target:
        return {"ok": True, "task": task_id, "debt": []}
    debt = move_debt(tasks_dir, task_id, cfg, target, pipeline)
    return {"ok": True, "task": task_id, "target": target,
            "terminal": is_terminal(pipeline, target),
            "debt": [_debt_item(r) for r in debt]}


class ConfirmIn(BaseModel):
    ids: list[str]
    section: str | None = None


class CommentIn(BaseModel):
    text: str


@app.post("/api/tasks/{task_id}/comment")
def api_add_comment(task_id: str, body: CommentIn) -> dict:
    """Дописать комментарий человека в хронологию задачи.

    Отдельный эндпоинт, а не правка секции: «Комментарии» — хронология, и
    строка в ней складывается из системного времени, подписи источника и сути.
    Отдай эту секцию текстовому редактору — и время можно будет выдумать, а
    прежние строки переписать; ровно от этого её и защищают.
    """
    tasks_dir, _cfg = _ctx()
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    note = append_note(path, body.text)
    if note is None:
        raise HTTPException(400, "Пустой комментарий не записывается")
    return {"ok": True, "note": note}


@app.post("/api/tasks/{task_id}/confirm")
def api_confirm(task_id: str, body: ConfirmIn) -> dict:
    """Подтвердить требования этапа от имени человека.

    Только предикат `confirm`: он и означает «человек сказал», а решать за
    человека агент не вправе. Требования, которые закрываются работой (чеклист,
    секции, поля), отсюда не закрываются — их подтверждать нечем.
    """
    tasks_dir, cfg = _ctx()
    result = confirm_requirements(tasks_dir, task_id, body.ids, body.section or "",
                                  cfg, load_pipeline(cfg))
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Задача не найдена"))
    return result


@app.post("/api/requires/exceptions")
def api_apply_requires_exceptions() -> dict:
    """Дописать в требования проекта исключения, появившиеся в поставке.

    Правит **только** `except_types` и только дописыванием: `requires` —
    настройки пользователя, он мог переписать формулировку или снять требование
    ещё с каких-то типов. Приведение к эталону затёрло бы его работу, поэтому
    остального конфига эта кнопка не касается вовсе.
    """
    tasks_dir, cfg = _ctx()
    updated, applied = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())
    if not applied:
        return {"ok": True, "applied": []}
    save_project_config(tasks_dir, {"requires": updated["requires"]})
    return {"ok": True, "applied": applied}


@app.post("/api/requires/types-reviewed")
def api_mark_types_reviewed() -> dict:
    """Отметить типы задач просмотренными: спрашиваем про них один раз.

    Ничего не настраивает — требования человек правит сам, и решить за него,
    относится ли его требование к новому типу, нельзя. Задача этой отметки одна:
    не спрашивать второй раз. Вечная строка в баннере обесценивает соседние.
    """
    tasks_dir, cfg = _ctx()
    types = unreviewed_task_types(cfg, load_pipeline(cfg).statuses())
    if not types:
        return {"ok": True, "marked": []}
    known = list(cfg.get(KNOWN_TYPES_FIELD) or [])
    save_project_config(tasks_dir, {KNOWN_TYPES_FIELD: known + types})
    return {"ok": True, "marked": types}


@app.get("/api/tasks/stalled")
def api_stalled() -> dict:
    """Что сейчас стоит и почему — срез простоя по всем задачам проекта."""
    tasks_dir, cfg = _ctx()
    return stalled_tasks(tasks_dir, load_pipeline(cfg))


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

    # Пауза скрипту создания неизвестна: он знает только блокировки. Ставим её
    # отдельным шагом — копия задачи наследует простой оригинала целиком, а
    # половина простоя врала бы о том, чего задача ждёт
    if body.paused.strip() and result.get("id"):
        set_paused(tasks_dir, result["id"], body.paused)

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

    if body.type is not None:
        typed = set_task_type(tasks_dir, task_id, body.type)
        if not typed.get("ok"):
            raise HTTPException(400, typed.get("error", "Ошибка смены типа"))
        result["type"] = typed["type"]

    if body.size is not None:
        sized = set_task_size(tasks_dir, task_id, body.size)
        if not sized.get("ok"):
            raise HTTPException(400, sized.get("error", "Ошибка смены размера"))
        result["size"] = sized["size"]

    if body.assignee is not None:
        # Исполнителя спрашивают не на каждом этапе: на своих задачу делает
        # тот, кто её взял. Правило спрашиваем у пайплайна — окно поле прячет,
        # а API обязано отказать: иначе прятание превращается в украшение.
        # Снятие имени разрешено всюду: задачу могли перенести с этапа
        # проверки, и застрявшее имя надо чем-то убирать
        name = " ".join(body.assignee.split())
        if name:
            task = parse_task(tasks_dir, task_id)
            status = (task or {}).get("meta", {}).get("status", "")
            pipeline = load_pipeline(cfg)
            if not accepts_assignee(pipeline, status):
                label = pipeline.label_of(status) if status else "—"
                raise HTTPException(
                    400, f"Этап «{label}» исполнителя не спрашивает — "
                         f"включить можно в настройках жизненного цикла")
        assigned = set_task_assignee(tasks_dir, task_id, name)
        if not assigned.get("ok"):
            raise HTTPException(400, assigned.get("error", "Ошибка назначения"))
        # Имя запоминается после записи, а не до: список подсказок не должен
        # пополняться тем, что в задачу не доехало
        if assigned["assignee"]:
            add_assignee(assigned["assignee"])
        result["assignee"] = assigned["assignee"]

    if body.epic is not None:
        # Сначала пишем в задачу: она же проверяет форму ключа. Регистрация
        # идёт следом — реестр не должен пополняться ключом, который отклонён
        epiced = set_task_epic(tasks_dir, task_id, body.epic)
        if not epiced.get("ok"):
            raise HTTPException(400, epiced.get("error", "Ошибка смены эпика"))
        if epiced["epic"]:
            register_epic(tasks_dir, epiced["epic"], body.epic_name)
        result["epic"] = epiced["epic"]
        result["epic_name"] = epic_name(tasks_dir, epiced["epic"])

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


@app.get("/api/tasks/{task_id}/delete-plan")
def api_delete_plan(task_id: str) -> dict:
    """Что заденет удаление: название, статус и кого задача держит.

    Диалог подтверждения называет последствия до удаления, а не после.
    """
    tasks_dir, _cfg = _ctx()
    plan = delete_plan(tasks_dir, task_id)
    if not plan.get("ok"):
        raise HTTPException(404, plan.get("error", "Задача не найдена"))
    return plan


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str) -> dict:
    """Удалить задачу: файл и строку доски за одно действие.

    Возможность выключена по умолчанию — проверку делает сам модуль удаления,
    чтобы правило было одним для доски и для любого другого клиента.
    """
    tasks_dir, cfg = _ctx()
    result = delete_task(tasks_dir, cfg, task_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Ошибка удаления задачи"))
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


@app.post("/api/board/sections/ensure")
def api_ensure_sections() -> dict:
    """Создать разделы под статусы пайплайна, которых нет на доске."""
    tasks_dir, cfg, _report = _validate_or_400()
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    pipeline = load_pipeline(cfg)
    return {"ok": True, "created": ensure_pipeline_sections(board_path, pipeline)}


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


# Части поставки, расхождения которых разбираются в окне: многофайловые и
# одиночные файлы в tasks/ — разрешаются они одинаково
_DIFFABLE_PARTS = ("skills", "commands", "rules", "vault", *SINGLE_FILE_PARTS)


@app.get("/api/agentic/stale")
def api_agentic_stale() -> dict:
    """Расхождения окружения активного проекта и чем их можно разрешить.

    `can_merge` — есть ли git: слияние выполняет `git merge-file`, и без него
    окно не должно предлагать кнопку, которая гарантированно откажет.
    """
    tasks_dir, cfg = _ctx()
    return {"items": agentic_stale_details(tasks_dir.parent, cfg),
            "can_merge": baseline.git_available()}


@app.get("/api/agentic/diff")
def api_agentic_diff(part: str, name: str) -> dict:
    """Diff элемента: сводный, «что нового в шаблоне» и «что своего в проекте»."""
    tasks_dir, cfg = _ctx()
    if part not in _DIFFABLE_PARTS:
        raise HTTPException(400, f"Неизвестная часть: {part}")
    result = agentic_diff(tasks_dir.parent, part, name, cfg)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Элемент не найден"))
    return result


class ResolveIn(BaseModel):
    part: str
    name: str
    action: str  # merge | template | keep


@app.post("/api/agentic/resolve")
def api_agentic_resolve(body: ResolveIn) -> dict:
    """Разрешить расхождение элемента: слить, взять шаблон или оставить своё."""
    tasks_dir, cfg = _ctx()
    if body.part not in _DIFFABLE_PARTS:
        raise HTTPException(400, f"Неизвестная часть: {body.part}")
    result = resolve_element(tasks_dir.parent, body.part, body.name, body.action, cfg)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Не удалось разрешить расхождение"))
    watcher.send("changed")
    return result


class RemoveIn(BaseModel):
    part: str
    name: str


@app.post("/api/agentic/remove")
def api_agentic_remove(body: RemoveIn) -> dict:
    """Удалить лишний элемент: скилл выключенной возможности и его обёртку.

    Удаляется только то, что при текущих настройках не поставляется, и только
    по кнопке: молча снести файл, который пользователь мог править, нельзя.
    Прежнее содержимое уходит в бэкап, как при обновлении из шаблона.
    """
    tasks_dir, cfg = _ctx()
    result = remove_element(tasks_dir.parent, body.part, body.name, cfg)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Не удалось удалить элемент"))
    watcher.send("changed")
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
    # Скрипт проекта только что обновился — теперь он знает про «Комментарии»,
    # и файлы задач можно переименовать вслед за ним, не дожидаясь рестарта
    rename_notes_section(tasks_dir, cfg)
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


def restart_telegram_poller() -> None:
    """Поднять поллер заново по текущему конфигу.

    Зовётся и при старте, и при сохранении настроек: поллер снимает конфиг
    один раз, поэтому включение возможности иначе ждало бы перезапуска сервера.
    Выключенная возможность потока не создаёт — «перезапуск» её просто гасит.
    """
    global _stop_telegram_loop
    if _stop_telegram_loop is not None:
        _stop_telegram_loop()
    _stop_telegram_loop = telegram_source.start_polling(
        load_global_config(), handle=lambda message: telegram_intake.handle(message))


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
    # …и там же секция «Заметки агента» переезжает в «Комментарии» (TASK-131):
    # имя описывало автора, а пишет туда и человек с доски. Проход идёт только
    # по проектам, где развёрнутый set_status.py про новое имя уже знает
    for proj in registry.list_projects().get("projects", []):
        tasks_dir = Path(proj["tasks_dir"])
        try:
            retire_artifact_names(tasks_dir)
            rename_notes_section(tasks_dir, load_project_config(tasks_dir))
            # …и ключ волта переезжает из эвристики по файлам в конфиг
            # проекта: автономный set_status.py читает только его
            record_vault_choice(tasks_dir)
        except Exception:
            pass
    # Проверка обновлений — фоном и только при согласии (update_check: auto).
    # В путь запроса доски сеть не попадает никогда
    cfg = load_global_config()
    updater.check_in_background(cfg)
    # …и дальше по таймеру: инструмент локальный, его держат запущенным днями,
    # а проверка «при старте» у такого пользователя не случается вовсе (TASK-125).
    # Находку доводим до открытой доски событием: точка в шапке читается из
    # кэша при загрузке страницы и сама бы не зажглась (TASK-126)
    global _stop_update_loop
    _stop_update_loop = updater.start_periodic_check(
        cfg, check=lambda c: updater.check_and_notify(c, ROOT_DIR, watcher.send))
    # Задачи из чата: поллер живёт тем же способом, что и проверка обновлений —
    # потоком-демоном внутри уже работающего сервера. Конфиг обработчик читает
    # сам на каждом сообщении: привязку чатов и свой ник человек правит в
    # настройках, и ждать перезапуска ради них незачем
    restart_telegram_poller()


@app.on_event("shutdown")
def _shutdown() -> None:
    # Разорвать SSE-подписки: иначе открытый EventSource браузера
    # не даёт uvicorn завершить процесс при reload
    watcher.shutdown()
    if _stop_update_loop is not None:
        _stop_update_loop()
    if _stop_telegram_loop is not None:
        _stop_telegram_loop()
