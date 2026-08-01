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
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import version
from .config import GLOBAL_DIR, DEFAULTS

# Кэш последней проверки: рядом с остальным глобальным состоянием инструмента
CACHE_FILE = GLOBAL_DIR / "update.json"

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


def fetch_manifest(url: str, timeout: int = TIMEOUT) -> dict:
    """Скачать и разобрать манифест релиза.

    Наружу отдаётся только статичный User-Agent: ни о проекте, ни о пользователе
    не сообщается ничего.
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
                 fetch=fetch_manifest) -> dict:
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

    fresh = {"checked_at": time.time(), "url": url, "error": None, "latest": latest}
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


# --- Сводка для интерфейса -------------------------------------------------


def status(cfg: dict, root: Path) -> dict:
    """Что показать в окне обновления. Только из кэша, без сети."""
    cache = read_cache()
    latest = cache.get("latest") if isinstance(cache.get("latest"), dict) else None
    current = version.current()

    available = False
    if latest and version.is_valid(str(latest.get("version", ""))):
        available = version.compare(str(latest["version"]), current) > 0

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
        "checked_at": cache.get("checked_at"),
        "error": cache.get("error"),
        "command": update_command(tag) if available and kind == "git" and tag else "",
    }
