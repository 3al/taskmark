"""Проверка обновлений: манифест релиза, кэш ответа, тип установки.

Итерация «узнать»: модуль только выясняет, вышла ли новая версия, и умеет
сказать, как обновиться. Ничего не скачивает и `git` не запускает — применение
обновления живёт отдельно и приходит следующей задачей.

Три правила, из которых всё остальное следует:

- **Без согласия в сеть не ходим.** До сих пор инструмент был полностью
  локальным; молча начать стучаться наружу нечестно, поэтому `update_check`
  по умолчанию `ask` и запрос не уходит, пока пользователь не ответил.
- **Провал — это тишина.** Офлайн, недоступный адрес, мусор вместо json:
  доска работает как обычно, а ошибка видна только в окне обновления.
- **Сеть не в пути запроса доски.** Проверка идёт фоновым потоком и кладёт
  результат в кэш; `/api/update/status` отвечает из кэша мгновенно.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import changelog as changelog_text
from . import version
from .config import GLOBAL_DIR, DEFAULTS
from .proc import no_window_flags

# Кэш последней проверки: рядом с остальным глобальным состоянием инструмента
CACHE_FILE = GLOBAL_DIR / "update.json"

# Обмен с лаунчером при обновлении по кнопке. Отдельные файлы, а не кэш
# проверки: его перетирает фоновая проверка, и запрос на обновление исчез бы
# вместе с ней. Имена дублируются в taskboard.py (UPDATE_REQUEST/UPDATE_RESULT)
# и должны оставаться синхронными: лаунчер не может импортировать backend —
# git-операция идёт до импорта
APPLY_FILE = GLOBAL_DIR / "update_apply.json"
RESULT_FILE = GLOBAL_DIR / "update_result.json"

# Как часто ходить в сеть при `auto`. Реже суток нет смысла: релизы редкие
CHECK_INTERVAL = 24 * 60 * 60

# Таймаут сетевого запроса. Короткий: проверка обновлений не та вещь,
# ради которой стоит ждать
TIMEOUT = 5

USER_AGENT = "taskboard-update-check"

# Больше манифеста быть не может — защита от чтения чего-то постороннего
MAX_MANIFEST_BYTES = 256 * 1024


def manifest_url(cfg: dict) -> str:
    """Адрес манифеста релиза из конфига."""
    return (cfg.get("release_manifest_url")
            or DEFAULTS["release_manifest_url"])


# Режимы проверки. `ask` — пользователь ещё не отвечал: ведёт себя как manual
# (сама в сеть не ходит), но интерфейс показывает вопрос. Отдельный `manual`
# нужен именно затем, чтобы «проверяю сам, когда захочу» было ответом, а не
# вечно висящим вопросом
MODES = ("ask", "auto", "manual", "off")


def check_mode(cfg: dict) -> str:
    """Режим проверки обновлений: ask | auto | manual | off."""
    mode = str(cfg.get("update_check") or DEFAULTS["update_check"]).lower()
    return mode if mode in MODES else "ask"


def may_check(cfg: dict) -> bool:
    """Разрешено ли ходить в сеть само, без явного действия пользователя."""
    return check_mode(cfg) == "auto"


def install_kind(root: Path) -> str:
    """Как установлен инструмент: git | plain | nogit.

    `git` — рабочая копия репозитория И найденный бинарник git: только тогда
    обновление одной командой вообще возможно. Наличие папки `.git` без самого
    git в PATH — это `nogit`: показывать команду, которую нечем выполнить,
    бессмысленно.
    """
    has_repo = (root / ".git").exists()
    has_git = shutil.which("git") is not None
    if has_repo and has_git:
        return "git"
    if has_repo:
        return "nogit"
    return "plain"


def update_command(tag: str) -> str:
    """Команда ручного обновления для git-установки.

    Обновляемся на тег, а не на ветку: между релизными коммитами `main` может
    содержать исходники фронтенда без пересобранного `dist`.
    """
    return f"git fetch origin main --tags && git merge --ff-only {tag}"


# --- Кэш -------------------------------------------------------------------


def read_cache() -> dict:
    """Последний удачный ответ манифеста. Нет или испорчен — пустой словарь."""
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(data: dict) -> None:
    """Сохранить кэш. Неудача записи молча игнорируется — это не критично."""
    try:
        GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError:
        pass


def cache_is_fresh(cache: dict, now: float | None = None) -> bool:
    """Проверяли меньше суток назад?"""
    checked = cache.get("checked_at")
    if not isinstance(checked, (int, float)):
        return False
    now = time.time() if now is None else now
    return 0 <= (now - checked) < CHECK_INTERVAL


# --- Сеть ------------------------------------------------------------------


def changelog_url(cfg: dict) -> str:
    """Адрес удалённого CHANGELOG.md — рядом с манифестом.

    Выводится из адреса манифеста, а не заводится второй настройкой: файлы
    лежат в одном репозитории и на одной ветке, а лишний ключ в конфиге
    заморозил бы ещё один дефолт (см. слои конфигурации) и разъехался бы
    с манифестом у того, кто сменил адрес.
    """
    base = manifest_url(cfg).rsplit("/", 1)[0]
    return f"{base}/CHANGELOG.md"


def fetch_changelog(url: str, timeout: int = TIMEOUT) -> str:
    """Скачать удалённый CHANGELOG.md как текст.

    Тот же предел размера, что и у манифеста: ответ приходит по сети, и
    доверия ему не больше.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("changelog подозрительно большой")
    return raw.decode("utf-8", errors="replace")


def fetch_manifest(url: str, timeout: int = TIMEOUT) -> dict:
    """Скачать и разобрать манифест релиза.

    Наружу отдаётся только статичный User-Agent: ни о проекте, ни о пользователе
    не сообщается ничего.

    **Свежесть ответа нам не подвластна.** `raw.githubusercontent.com` отдаёт
    файл через CDN с `Cache-Control: max-age=300`, и первые пять минут после
    публикации узел отвечает старой версией. Пробить это со стороны клиента
    нельзя: query-строка в ключ кэша не входит (проверено — `?t=<мс>` даёт
    `X-Cache: HIT`), клиентский `no-cache` игнорируется. Лечится только сменой
    источника (например, на GitHub API релизов с `max-age=60`), а пять минут
    задержки на редких релизах того не стоят (TASK-127).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("манифест подозрительно большой")
    return parse_manifest(json.loads(raw.decode("utf-8")))


def parse_manifest(data: object) -> dict:
    """Проверить форму манифеста и оставить только известные поля.

    Чужой ответ (заглушка провайдера, страница ошибки, обрезанный json) не
    должен доехать до интерфейса под видом релиза, поэтому версия обязана
    разбираться, а всё лишнее отбрасывается.
    """
    if not isinstance(data, dict):
        raise ValueError("манифест не является объектом")
    raw_version = data.get("version")
    if not isinstance(raw_version, str) or not version.is_valid(raw_version):
        raise ValueError(f"в манифесте нет разбираемой версии: {raw_version!r}")
    tag = data.get("tag")
    notes = data.get("notes")
    date = data.get("date")
    return {
        "version": raw_version.strip(),
        "tag": tag.strip() if isinstance(tag, str) and tag.strip() else "v" + raw_version.strip(),
        "date": date if isinstance(date, str) else "",
        "notes": notes if isinstance(notes, str) else "",
    }


def check_remote(cfg: dict, force: bool = False,
                 fetch=fetch_manifest, fetch_text=fetch_changelog) -> dict:
    """Сходить за манифестом (если можно и пора) и обновить кэш.

    Возвращает кэш — прежний или свежий. `force` — явное действие пользователя
    («Проверить сейчас»), оно обходит и суточный интервал, и режим `ask`:
    нажатие кнопки и есть согласие. Режим `off` не обходит ничто.

    Сетевая ошибка не поднимается наверх: она пишется в кэш полем `error`,
    а прежние сведения о версии остаются на месте.
    """
    cache = read_cache()
    if check_mode(cfg) == "off":
        return cache
    if not force:
        if not may_check(cfg) or cache_is_fresh(cache):
            return cache

    url = manifest_url(cfg)
    try:
        latest = fetch(url)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        cache = dict(cache)
        cache["checked_at"] = time.time()
        cache["error"] = f"{type(exc).__name__}: {exc}"
        write_cache(cache)
        return cache

    # Тексты пропущенных выпусков: манифест описывает только последний, а
    # решение «обновляться ли» человек принимает по всему, что пропустил.
    # Провал этого запроса проверку не отменяет — окно покажет заметки
    # последней версии, как показывало раньше (TASK-237)
    text = ""
    try:
        text = fetch_text(changelog_url(cfg))
        if len(text) > MAX_MANIFEST_BYTES:
            raise ValueError("changelog подозрительно большой")
    except Exception:
        text = str(read_cache().get("changelog") or "")

    fresh = {"checked_at": time.time(), "url": url, "error": None, "latest": latest,
             "changelog": text}
    write_cache(fresh)
    return fresh


def check_in_background(cfg: dict) -> None:
    """Запустить проверку фоновым потоком (демоном, чтобы не держать выход)."""
    if not may_check(cfg):
        return

    def run() -> None:
        try:
            check_remote(cfg)
        except Exception:  # noqa: BLE001 — фон не имеет права уронить сервер
            pass

    threading.Thread(target=run, name="update-check", daemon=True).start()


def check_and_notify(cfg: dict, root: Path, notify, fetch=fetch_manifest) -> dict:
    """Проверить обновления и сказать открытой доске, если версия новая.

    Точка «доступна новая версия» читается из кэша при загрузке страницы, и
    без события фоновая находка оставалась невидимой до перезагрузки (TASK-126).

    Событие поднимается **только на смену версии**: повторная находка той же
    самой ничего не шлёт — иначе доска дёргалась бы каждый час без причины.
    """
    before = read_cache().get("latest") or {}
    fresh = check_remote(cfg, fetch=fetch)
    if fresh.get("error"):
        return fresh

    latest = fresh.get("latest") or {}
    if not latest or latest.get("version") == before.get("version"):
        return fresh
    if status(cfg, root).get("update_available"):
        notify("update")
    return fresh


# Как часто просыпается фоновый цикл. Само хождение в сеть по-прежнему держит
# `CHECK_INTERVAL`: цикл лишь даёт шанс проверить, не дожидаясь перезапуска
WAKE_INTERVAL = 60 * 60


def start_periodic_check(cfg: dict, interval: float = WAKE_INTERVAL,
                         check=None):
    """Проверять обновления по таймеру, пока живёт сервер. Возвращает «стоп».

    Раньше проверка звалась только из `startup`, а `CHECK_INTERVAL` был
    троттлингом, а не расписанием: инструмент локальный и работает днями, так
    что у выбравшего «автоматически» проверка не случалась вовсе — релиз он
    узнавал руками (TASK-125).

    В сеть по-прежнему ходим только при `auto` и не чаще суток: цикл лишь даёт
    проверке шанс, решает всё тот же `check_remote`. Поток — демон: он не
    держит выход процесса и не мешает остановке и перезапуску сервера из UI.
    """
    stop = threading.Event()
    if not may_check(cfg):
        return stop.set

    run_check = check or check_remote

    def loop() -> None:
        while not stop.wait(interval):
            try:
                run_check(cfg)
            except Exception:  # noqa: BLE001 — сеть отвалилась, цикл живёт дальше
                pass

    threading.Thread(target=loop, name="update-check-loop", daemon=True).start()
    return stop.set


# --- Готовность к обновлению -----------------------------------------------
# Здесь кончается «только смотрим»: из манифеста приходит тег, на который
# выполняется git merge. Это исполняемое действие по данным из сети, поэтому
# remote берётся из локального репозитория, тег проверяется как данные,
# а версия обязана быть строго новее установленной.

# Тег релиза и ничего кроме: `v` + семантическая версия. Ни путей, ни ссылок,
# ни опций — строка из сети не должна попадать в командную строку git как есть
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def valid_tag(tag: object) -> bool:
    """Похоже ли значение из манифеста на имя релизного тега."""
    return isinstance(tag, str) and bool(TAG_RE.match(tag))


def _git(root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Запустить git в папке инструмента. Без shell и без склейки строк."""
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=no_window_flags())


def local_remote(root: Path) -> str | None:
    """Имя remote, откуда клонировались. `origin` в приоритете.

    Адрес обновления берётся отсюда, а не из манифеста: манифест лежит в сети
    и мог быть подменён, а remote — это то, откуда пользователь уже получил
    установленный код.
    """
    try:
        out = _git(root, "remote")
    except (OSError, subprocess.SubprocessError):
        return None
    names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if not names:
        return None
    return "origin" if "origin" in names else names[0]


def worktree_dirty(root: Path) -> bool | None:
    """Есть ли незакоммиченные правки отслеживаемых файлов (None — не узнать).

    Untracked не считаются намеренно: у части установок папка задач лежит
    в дереве инструмента незаигноренной, и она обновлению не мешает.
    """
    try:
        out = _git(root, "status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return bool(out.stdout.strip())


def local_commits(root: Path) -> int | None:
    """Сколько своих коммитов лежит поверх апстрима (None — сравнить не с чем).

    Тег обновления приезжает только с `fetch`, поэтому потомственность до сети
    не проверить. Зато расхождение с апстримом видно локально — и именно оно
    делает fast-forward невозможным. Без этой проверки отказ приходил после
    перезапуска сервера, впустую (TASK-098).
    """
    try:
        out = _git(root, "rev-list", "--count", "@{upstream}..HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None  # апстрим не настроен — прежнее поведение, отказ из лаунчера
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def head_commit(root: Path) -> str:
    """Текущий HEAD — точка отката, если обновление не сложится."""
    try:
        out = _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def in_dev_mode() -> bool:
    """Под dev-супервизором обновляться нельзя: он перезапустит сервер."""
    return bool(os.environ.get("TASKBOARD_SUPERVISED"))


def plan(cfg: dict, root: Path) -> dict:
    """Можно ли обновиться прямо сейчас — и если нет, то почему.

    Преграды собираются **все сразу**: чинить их по одной, каждый раз запуская
    обновление заново, — худший из возможных сценариев. Команда ручного
    обновления возвращается в любом случае: отказ кнопки не повод оставлять
    человека без выхода.
    """
    info = status(cfg, root)
    latest = info.get("latest") or {}
    tag = str(latest.get("tag") or "")
    target = str(latest.get("version") or "")

    blockers: list[str] = []
    if not info.get("update_available"):
        blockers.append(
            f"Устанавливать нечего: у вас {info['version']}, "
            f"в манифесте {target or 'версии нет'}")
    if info.get("install") == "nogit":
        blockers.append("git не найден в PATH — обновиться командой не получится")
    elif info.get("install") != "git":
        blockers.append("Инструмент установлен не из git — обновите распаковкой архива")
    if not valid_tag(tag):
        blockers.append(f"Тег в манифесте не похож на релизный: {tag!r}")
    if in_dev_mode():
        blockers.append("Идёт dev-режим (--dev): остановите его и обновитесь обычным запуском")

    remote = local_remote(root) if info.get("install") == "git" else None
    if info.get("install") == "git" and not remote:
        blockers.append("У репозитория нет remote — неоткуда получать обновление")

    ahead = local_commits(root) if info.get("install") == "git" else None
    if ahead:
        blockers.append(
            f"В вашей копии {ahead} собственных коммитов поверх релиза — "
            f"обновление кнопкой невозможно. Работа не потеряется: "
            f"обновитесь вручную, разобравшись с ними")

    dirty = worktree_dirty(root) if info.get("install") == "git" else None
    if dirty:
        blockers.append("В рабочей копии есть незакоммиченные правки — "
                        "сохраните или отмените их")
    elif dirty is None and info.get("install") == "git":
        blockers.append("Не удалось проверить состояние рабочей копии")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "tag": tag,
        "version": target,
        "remote": remote,
        "head": head_commit(root) if info.get("install") == "git" else "",
        "command": update_command(tag) if valid_tag(tag) else "",
    }


def request_apply(plan_data: dict) -> None:
    """Записать лаунчеру, что применять: тег, версия, точка отката и отсчёта.

    `from` — версия, с которой уходим. Без неё после обновления не построить
    диапазон «что изменилось»: обновление может перепрыгнуть несколько
    выпусков, и показать надо все (TASK-099).
    """
    APPLY_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPLY_FILE.write_text(json.dumps(
        {"tag": plan_data.get("tag", ""), "version": plan_data.get("version", ""),
         "head": plan_data.get("head", ""), "from": version.current(),
         "at": time.time()},
        ensure_ascii=False, indent=2), encoding="utf-8")


def last_result() -> dict:
    """Чем кончилось последнее обновление (пусто — его не было)."""
    try:
        data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_result() -> None:
    """Забыть итог обновления — плашку показываем один раз."""
    try:
        RESULT_FILE.unlink()
    except OSError:
        pass


# --- Сводка для интерфейса -------------------------------------------------


def status(cfg: dict, root: Path) -> dict:
    """Что показать в окне обновления. Только из кэша, без сети."""
    cache = read_cache()
    latest = cache.get("latest") if isinstance(cache.get("latest"), dict) else None
    current = version.current()

    available = False
    if latest and version.is_valid(str(latest.get("version", ""))):
        available = version.compare(str(latest["version"]), current) > 0

    # Пропущенные выпуски режем **при чтении**, а не при загрузке: локальная
    # версия меняется после обновления, и нарезанный заранее список сразу
    # устарел бы (TASK-237)
    missed = []
    if available:
        missed = changelog_text.since(str(cache.get("changelog") or ""), current)

    kind = install_kind(root)
    tag = str(latest.get("tag")) if latest else ""
    return {
        "version": current,
        "mode": check_mode(cfg),
        # Адрес показываем в окне: без него ошибка вроде 404 неотличима от
        # поломки инструмента, а адрес настраиваемый и мог быть изменён
        "url": manifest_url(cfg),
        "install": kind,
        "latest": latest,
        "update_available": available,
        # Все выпуски новее установленного, а не только последний: манифест
        # описывает один релиз, и по нему не видно, что человек пропустил
        "missed": missed,
        "missed_total": len(missed),
        "checked_at": cache.get("checked_at"),
        "error": cache.get("error"),
        "command": update_command(tag) if available and kind == "git" and tag else "",
        # Итог последнего обновления — интерфейс показывает его один раз.
        # Нет итога — именно None, а не пустой объект: `{}` во фронте истинно,
        # и окно нарисовало бы плашку о провале, которого не было
        "last_result": last_result() or None,
    }
