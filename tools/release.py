#!/usr/bin/env python3
"""Выпуск версии Taskmark: подсчёт версии, changelog, манифест, тег.

Этот скрипт знает про устройство **этого** репозитория — файл `taskboard/VERSION`,
`CHANGELOG.md`, манифест `release.json`, собранный фронтенд и git-теги. Поэтому
в поставку пользователям он не идёт: у них «выпустить» значит совсем другое.

Подключается к скиллу выпуска ключом `release_script` в конфиге проекта — тем же
приёмом, что `create_script` и `status_script`. Контракт от проекта не зависит:

    release.py --check [--bump LEVEL]
        {"ok": true, "current": "1.0.0", "next": "1.1.0", "blockers": []}
        Ничего не меняет. Скилл зовёт до вопросов человеку.

    release.py --apply --bump LEVEL --notes ФАЙЛ --tasks TASK-001,TASK-002 --commits abc1234
        {"ok": true, "version": "1.1.0", "tag": "v1.1.0"}
        Отказ — ненулевой код возврата и {"ok": false, "error": "..."}; коммит,
        которому нужен код вне состава, — отказ с {"conflict": {"commit", "needs"}}.

    Интеграционная ветка (`integration_branch` проекта, по умолчанию `dev`):
    работа идёт в ней, а `--apply` переносит в `main` только `--commits`,
    пересобирает фронтенд, ставит тег и вливает `main` обратно.

    release.py --history
        [{"version": "1.1.0", "tag": "v1.1.0", "released_at": "...",
          "annotated": true, "commit": "1733bab", "tasks": ["TASK-089"]}]
        Только читает git: когда вышла версия и что в неё вошло.

Разряд версии и текст заметок приходят снаружи: и то и другое — решение человека,
а не свойство коммитов.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "taskboard" / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"
MANIFEST = ROOT / "release.json"
FRONTEND_SRC = ROOT / "taskboard" / "frontend" / "src"
FRONTEND_DIST = ROOT / "taskboard" / "frontend" / "dist"

# Собранный фронтенд в переносах не участвует: каждый коммит задачи пересобирает
# его под свои исходники, и файлы с хэшами в именах конфликтуют на любом переносе.
# В выпускаемой ветке он собирается один раз — из её собственных исходников
GENERATED = ("taskboard/frontend/dist",)

# Выпускаемая ветка: из неё пользователи получают тег и манифест
RELEASE_BRANCH = "main"
# Дефолт интеграционной ветки — тот же, что в поставке (`backend/config.py`)
DEFAULT_INTEGRATION = "dev"

LEVELS = ("major", "minor", "patch")

# Секция версии в changelog: «## [1.4.0] — 2026-08-01» до следующей такой же
_SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
                      re.S | re.M)

_VERSION = re.compile(r"^\d+(\.\d+)*$")

# Состав выпуска в теле релизного коммита: «Задачи выпуска: TASK-089, TASK-090»
_TASKS_LINE = re.compile(r"^Задачи выпуска:(?P<list>.*)$", re.M)
_TASK_ID = re.compile(r"TASK-\d+")

# Поля тега для истории. `*`-поля разыменовывают аннотированный тег в коммит:
# у обычного тега они пусты, зато сам объект и есть коммит. Пустое поле не должно
# оказаться последним — хвост вывода обрезается, и строка теряет колонку
_TAG_FIELDS = ("%(refname:short)", "%(taggerdate:iso-strict)",
               "%(creatordate:iso-strict)", "%(*objectname:short)",
               "%(objectname:short)")


# --- Версия ----------------------------------------------------------------


def parse_version(value: str) -> tuple[int, ...]:
    """Строка версии → кортеж чисел. Мусор — ValueError."""
    text = (value or "").strip().lstrip("vV")
    if not _VERSION.match(text):
        raise ValueError(f"не похоже на версию: {value!r}")
    return tuple(int(p) for p in text.split("."))


def _paths(root: Path) -> dict:
    """Файлы выпуска относительно корня репозитория."""
    if root == ROOT:
        return {"version": VERSION_FILE, "changelog": CHANGELOG, "manifest": MANIFEST,
                "src": FRONTEND_SRC, "dist": FRONTEND_DIST}
    return {"version": root / "taskboard" / "VERSION", "changelog": root / "CHANGELOG.md",
            "manifest": root / "release.json",
            "src": root / "taskboard" / "frontend" / "src",
            "dist": root / "taskboard" / "frontend" / "dist"}


def current_version(root: Path = ROOT) -> str:
    return _paths(root)["version"].read_text(encoding="utf-8").strip()


def next_version(current: str, bump: str) -> str:
    """Следующая версия по разряду.

    Младшие разряды обнуляются: подняли minor — patch становится нулём.
    Руками об это спотыкаются регулярно, поэтому арифметика тут, а не в голове.
    """
    if bump not in LEVELS:
        raise ValueError(f"неизвестный разряд: {bump!r} (ожидалось {'|'.join(LEVELS)})")
    parts = list(parse_version(current))
    parts += [0] * (3 - len(parts))
    major, minor, patch = parts[:3]
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


# --- Changelog и манифест --------------------------------------------------


def top_section(path: Path = CHANGELOG) -> dict:
    """Верхняя секция changelog: версия и тело без заголовка."""
    match = _SECTION.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"в {path.name} нет ни одной секции вида «## [версия]»")
    return {"version": match.group("version").strip(),
            "body": match.group("body").strip()}


def insert_section(path: Path, version: str, when: str, body: str) -> None:
    """Вставить секцию новой версии выше всех остальных.

    Шапка файла (заголовок и вступление) остаётся на месте: секция встаёт перед
    первой существующей, а если их нет — в конец.
    """
    text = path.read_text(encoding="utf-8")
    section = f"## [{version}] — {when}\n\n{body.strip()}\n"
    match = _SECTION.search(text)
    if match is None:
        path.write_text(text.rstrip("\n") + "\n\n" + section, encoding="utf-8")
        return
    head, tail = text[:match.start()], text[match.start():]
    path.write_text(head.rstrip("\n") + "\n\n" + section + "\n" + tail, encoding="utf-8")


def build_manifest(path: Path = CHANGELOG, version: str | None = None) -> dict:
    """Манифест релиза из верхней секции changelog.

    Версия сверяется намеренно: «подняли VERSION, changelog забыли» — самая
    частая ошибка ручного выпуска, и молча выпускать такое нельзя.
    """
    section = top_section(path)
    if version is not None and section["version"] != version:
        raise ValueError(
            f"верхняя секция changelog — {section['version']}, а выпускается {version}")
    released = version or section["version"]
    return {"version": released, "tag": "v" + released,
            "date": date.today().isoformat(), "notes": section["body"]}


# --- Готовность ------------------------------------------------------------


def dist_is_fresh(src: Path = FRONTEND_SRC, dist: Path = FRONTEND_DIST) -> bool:
    """Собранный фронтенд не старее исходников?

    Тег — это то, что доедет пользователю, а `dist` коммитится в репозиторий.
    Выпустить исходники без пересборки значит отдать людям старый интерфейс.
    """
    index = dist / "index.html"
    if not index.is_file():
        return False
    newest = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0)
    return index.stat().st_mtime >= newest


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout.strip()


def tag_args(tag: str, notes_path: Path) -> tuple[str, ...]:
    """Аргументы `git tag` для аннотированного тега с заметками выпуска.

    `--cleanup=verbatim` обязателен: без него git вырезает строки, начинающиеся
    с `#`, считая их комментариями, и заголовки групп changelog («### Добавлено»)
    исчезают молча — список при этом остаётся, и заметить трудно.

    Заметки передаются файлом, а не через `-m`: они многострочные и с разметкой.
    """
    return ("tag", "-a", tag, "--cleanup=verbatim", "-F", str(notes_path))


def release_args(tag: str, title: str, notes_path: Path) -> tuple[str, ...]:
    """Аргументы `gh release create` для **уже существующего** тега."""
    return ("gh", "release", "create", tag, "--title", title,
            "--notes-file", str(notes_path))


def create_github_release(tag: str, title: str, notes_path: Path) -> dict:
    """Создать GitHub Release. Витрина: провал выпуск не отменяет.

    Release нужен не для механизма обновлений — тот читает манифест из репозитория, —
    а для людей: только у Release разметка отрендерена, и значок «Latest» считается
    по нему. Без Release посетитель страницы релизов видит прошлую версию как
    последнюю и скачивает устаревший архив.
    """
    if shutil.which("gh") is None:
        return {"ok": False,
                "reason": "gh не установлен — создайте Release вручную для тега " + tag}
    try:
        subprocess.run(release_args(tag, title, notes_path), cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", check=True)
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "reason": (exc.stderr or str(exc)).strip()}
    return {"ok": True}


# Слово без ASCII: мусор перекодировки не содержит пробелов и латиницы, поэтому
# проверяется по словам. U+FFFD — след байта, которого в cp1251 нет вовсе
_NON_ASCII_RUN = re.compile(r"[^\x00-\x7f�]+")
_MEANINGFUL = re.compile(r"[Ѐ-ӿ -⁯«»]")


def garbled_fragments(text: str) -> list[str]:
    """Слова, похожие на UTF-8, прочитанный как cp1251: «Р—Р°РїСѓСЃРє».

    Признак — обратимость: слово кодируется в cp1251 и эти байты разбираются
    как строгий UTF-8 в кириллицу или типографику. Обычный русский текст так не
    разбирается: буквы cp1251 лежат в 0xC0–0xFF, и за ведущим байтом UTF-8 не
    идёт продолжение. Поэтому редкие сочетания букв ложных срабатываний не дают.
    """
    found: list[str] = []
    for run in _NON_ASCII_RUN.findall(text):
        try:
            decoded = run.encode("cp1251").decode("utf-8")
        except UnicodeError:
            continue
        if decoded != run and _MEANINGFUL.search(decoded) and run not in found:
            found.append(run)
    return found


def notes_problems(notes: str) -> list[str]:
    """Что в тексте заметок мешает выпуску. Пока одно — испорченная кодировка."""
    fragments = garbled_fragments(notes)
    if not fragments:
        return []
    sample = ", ".join(f"«{f}»" for f in fragments[:3])
    return [f"текст заметок испорчен перекодировкой (UTF-8 прочитан как cp1251): {sample}"]


def blockers(root: Path = ROOT) -> list[str]:
    """Что мешает выпускать прямо сейчас. Список, а не первое встреченное.

    Человеку нужно увидеть всё сразу: чинить по одному, каждый раз запуская
    выпуск заново, — худший из возможных сценариев.
    """
    paths = _paths(root)
    integration = integration_branch(root)
    found: list[str] = []
    try:
        if _git("status", "--porcelain", "--untracked-files=no", cwd=root):
            found.append("в рабочем дереве есть незакоммиченные правки")
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
        if integration:
            # Работа и выпуск идут из интеграционной ветки: с неё скрипт уходит
            # в выпускаемую и на неё же возвращается
            if branch != integration:
                found.append(f"выпуск делается не с ветки {integration}")
            if not _branch_exists(root, RELEASE_BRANCH):
                found.append(f"нет ветки {RELEASE_BRANCH}, куда переносится выпуск")
        elif branch != RELEASE_BRANCH:
            found.append(f"выпуск делается не с ветки {RELEASE_BRANCH}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        found.append("git недоступен или это не репозиторий")
    # Со схемой фронтенд собирается в выпускаемой ветке заново — свежесть
    # сборки интеграционной ветки выпуску не важна
    if not integration and not dist_is_fresh(paths["src"], paths["dist"]):
        found.append("собранный фронтенд старее исходников — нужен npm run build")
    try:
        # Совпадение — норма: между выпусками changelog описывает установленную
        # версию. Расхождение значит, что VERSION и changelog подняли порознь
        section = top_section(paths["changelog"])
        if section["version"] != current_version(root):
            found.append(
                f"changelog описывает {section['version']}, "
                f"а установлена {current_version(root)} — они разошлись")
    except (ValueError, OSError) as exc:
        found.append(f"changelog: {exc}")
    return found


def warnings(root: Path = ROOT) -> list[str]:
    """Что выпуску не мешает, но человеку стоит знать.

    Коммит интеграционной ветки, не записанный ни в одну задачу, не попадёт ни
    в один выпуск: переносятся только коммиты из «Истории коммитов» задач.
    """
    integration = integration_branch(root)
    if not integration or not _branch_exists(root, RELEASE_BRANCH) \
            or not _branch_exists(root, integration):
        return []
    try:
        pending = _pending(root, integration)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    listed = listed_commits(root / "tasks")
    out = []
    for sha in pending:
        if any(sha.startswith(short) for short in listed):
            continue
        subject = _git("log", "-1", "--format=%s", sha, cwd=root)
        out.append(f"коммит {sha[:7]} «{subject}» не записан ни в одну задачу — "
                   f"в выпуск он не попадёт")
    return out


def check(bump: str | None = None, root: Path = ROOT) -> dict:
    """Что скилл показывает человеку до подтверждения. Ничего не меняет."""
    current = current_version(root)
    result: dict = {"ok": True, "current": current, "next": None,
                    "blockers": blockers(root), "warnings": warnings(root)}
    if bump:
        try:
            result["next"] = next_version(current, bump)
        except ValueError as exc:
            result["ok"] = False
            result["error"] = str(exc)
    return result


# --- Интеграционная ветка ----------------------------------------------------


def integration_branch(root: Path = ROOT) -> str:
    """Ветка, куда коммитится проверенное. Пусто — схема выключена.

    Слои те же, что у скриптов задач: дефолт → глобальный конфиг → проект.
    """
    value = DEFAULT_INTEGRATION
    for path in (Path.home() / ".taskboard" / "config.json",
                 root / "tasks" / ".taskboard.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "integration_branch" in data:
            value = data["integration_branch"]
    return str(value or "").strip()


_COMMIT_LINE = re.compile(r"^\s*[-*]\s*`(?P<hash>[0-9a-fA-F]{7,40})`", re.M)


def listed_commits(tasks_dir: Path) -> set[str]:
    """Хэши из «Истории коммитов» всех задач проекта (в нижнем регистре)."""
    found: set[str] = set()
    for path in tasks_dir.glob("TASK-*.md"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        _, _, tail = text.partition("## История коммитов")
        found.update(m.group("hash").lower() for m in _COMMIT_LINE.finditer(tail))
    return found


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    """git без исключения на ненулевой код: отказ переноса — ответ, а не сбой."""
    return subprocess.run(("git", *args), cwd=root, capture_output=True,
                          text=True, encoding="utf-8")


def _branch_exists(root: Path, name: str) -> bool:
    return _run(root, "rev-parse", "--verify", "--quiet",
                f"refs/heads/{name}").returncode == 0


def _pending(root: Path, integration: str) -> list[str]:
    """Коммиты интеграционной ветки, которых нет в выпускаемой, от старых к новым.

    Перенесённый коммит в выпускаемой ветке живёт под другим хэшем, поэтому
    сравнение идёт по содержимому (`git cherry`), а не по хэшам.
    """
    out = _git("cherry", RELEASE_BRANCH, integration, cwd=root)
    fresh = {line[2:].strip() for line in out.splitlines() if line.startswith("+")}
    order = _git("rev-list", "--reverse", "--no-merges",
                 f"{RELEASE_BRANCH}..{integration}", cwd=root).splitlines()
    return [sha for sha in order if sha in fresh]


def _drop_generated(root: Path) -> None:
    """Вернуть собранные файлы к состоянию HEAD: перенос их не трогает."""
    for rel in GENERATED:
        _run(root, "reset", "-q", "--", rel)
        _run(root, "checkout", "-q", "HEAD", "--", rel)
        extra = _run(root, "ls-files", "--others", "--exclude-standard", "--", rel)
        for name in extra.stdout.splitlines():
            (root / name).unlink(missing_ok=True)


def transfer(root: Path, commits: list[str], integration: str) -> dict:
    """Перенести коммиты состава из интеграционной ветки в выпускаемую.

    Порядок — как в интеграционной ветке, а не как в аргументе. Уже
    перенесённые пропускаются. Коммит, который не ложится без кода вне состава,
    откатывает перенос целиком: выпускаемая ветка остаётся как была, а работа
    возвращается на интеграционную.
    """
    pending = _pending(root, integration)
    selected: dict[str, str] = {}
    for short in commits:
        res = _run(root, "rev-parse", "--verify", "--quiet", f"{short}^{{commit}}")
        sha = res.stdout.strip()
        if res.returncode or not sha:
            return {"ok": False, "error": f"коммит {short} не найден в репозитории"}
        if _run(root, "merge-base", "--is-ancestor", sha, integration).returncode:
            return {"ok": False,
                    "error": f"коммит {short} не лежит в ветке {integration}"}
        selected[sha] = short
    moving = [sha for sha in pending if sha in selected]

    start = _git("rev-parse", RELEASE_BRANCH, cwd=root)
    _git("switch", "-q", RELEASE_BRANCH, cwd=root)
    moved: list[str] = []
    for sha in moving:
        _run(root, "cherry-pick", "--no-commit", sha)
        _drop_generated(root)
        unmerged = _run(root, "diff", "--name-only", "--diff-filter=U").stdout.split()
        if unmerged:
            # Кто из невыбранных трогал те же файлы раньше — без них и не ложится
            touched = set(_git("rev-list", f"{RELEASE_BRANCH}..{sha}~1", "--",
                               *unmerged, cwd=root).splitlines())
            needs = [_git("rev-parse", "--short", c, cwd=root) for c in pending
                     if c not in selected and c in touched]
            rollback(root, start, integration)
            return {"ok": False,
                    "error": f"коммит {selected[sha]} не переносится без кода вне "
                             f"выпуска: {', '.join(unmerged)}",
                    "conflict": {"commit": selected[sha], "needs": needs}}
        if _run(root, "diff", "--cached", "--quiet").returncode:
            _git("commit", "-q", "--no-verify", "-C", sha, cwd=root)
            moved.append(selected[sha])
        _run(root, "cherry-pick", "--quit")
    return {"ok": True, "start": start, "moved": moved}


def rollback(root: Path, start: str, integration: str) -> None:
    """Вернуть выпускаемую ветку к началу переноса и уйти на интеграционную."""
    _run(root, "cherry-pick", "--quit")
    _run(root, "reset", "-q", "--hard", start)
    _drop_generated(root)
    _run(root, "switch", "-q", integration)


def merge_back(root: Path, integration: str) -> dict:
    """Влить выпуск в интеграционную ветку и вернуться на неё.

    Без этого версия, changelog и манифест в ней отстанут, а следующий выпуск
    не узнает, что коммиты уже перенесены. Собранный фронтенд остаётся свой:
    сборка интеграционной ветки построена из её исходников, а они полнее.
    """
    _git("switch", "-q", integration, cwd=root)
    _run(root, "merge", "--no-ff", "--no-commit", RELEASE_BRANCH)
    _drop_generated(root)
    unmerged = _run(root, "diff", "--name-only", "--diff-filter=U").stdout.split()
    if unmerged:
        _run(root, "merge", "--abort")
        return {"ok": False,
                "error": f"слияние {RELEASE_BRANCH} в {integration} упёрлось в "
                         f"конфликт: {', '.join(unmerged)} — влейте вручную"}
    _git("commit", "-q", "--no-verify", "--no-edit", cwd=root)
    return {"ok": True}


def build_frontend(root: Path) -> None:
    """Пересобрать фронтенд в рабочем дереве (`npm run build`)."""
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm не найден — фронтенд выпуска собрать нечем")
    subprocess.run((npm, "run", "build"), cwd=root / "taskboard" / "frontend",
                   check=True, capture_output=True, text=True, encoding="utf-8")


# --- Выпуск ----------------------------------------------------------------


def apply(bump: str, notes: str, tasks: list[str], commits: list[str] | None = None,
          root: Path = ROOT, build=build_frontend) -> dict:
    """Выпустить версию: перенос → changelog → VERSION → манифест → коммит → тег.

    При интеграционной ветке в выпускаемую переносятся только `commits`, после
    выпуска она вливается обратно, и работа продолжается на интеграционной.

    Пуш и GitHub Release здесь не делаются: они необратимы для пользователей,
    и решение остаётся за человеком (скилл спрашивает и зовёт `--publish`).
    """
    paths = _paths(root)
    version = next_version(current_version(root), bump)
    # Испорченный текст ни на одном шаге не падает и уезжает в тег и манифест —
    # поэтому это преграда, а не предупреждение
    stoppers = notes_problems(notes) + blockers(root)
    if stoppers:
        return {"ok": False, "error": "; ".join(stoppers)}

    integration = integration_branch(root)
    result: dict = {}
    if integration:
        if commits is None:
            return {"ok": False, "error": "при интеграционной ветке нужен --commits: "
                                          "в выпуск уходят только коммиты состава"}
        moved = transfer(root, commits, integration)
        if not moved["ok"]:
            return moved
        result["moved"] = moved["moved"]
        try:
            build(root)
        except Exception as exc:  # noqa: BLE001 — откатываем при любом провале сборки
            rollback(root, moved["start"], integration)
            return {"ok": False, "error": f"сборка фронтенда не удалась: {exc}"}

    insert_section(paths["changelog"], version, date.today().isoformat(), notes)
    paths["version"].write_text(version + "\n", encoding="utf-8")
    manifest = build_manifest(paths["changelog"], version)
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")

    _git("add", "--", str(paths["version"]), str(paths["changelog"]),
         str(paths["manifest"]), cwd=root)
    if integration:
        _git("add", "-A", "--", *GENERATED, cwd=root)
    body = "Задачи выпуска: " + ", ".join(tasks) if tasks else "Выпуск без привязки к задачам."
    _git("commit", "-q", "--no-verify", "-m", f"Релиз {version}", "-m", body, cwd=root)

    # Заметки в аннотацию тега: их читают из консоли (`git show`, `git tag -n`).
    # На странице тега разметка не рендерится — это работа Release, см. publish()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md",
                                     delete=False) as tmp:
        tmp.write(f"Taskmark {version}\n\n{manifest['notes'].strip()}\n")
        annotation = Path(tmp.name)
    try:
        _git(*tag_args(manifest["tag"], annotation), cwd=root)
    finally:
        annotation.unlink(missing_ok=True)
    commit = _git("rev-parse", "--short", "HEAD", cwd=root)
    if integration:
        result["merged"] = merge_back(root, integration)
    return {"ok": True, "version": version, "tag": manifest["tag"], "commit": commit,
            **result}


def publish(root: Path = ROOT) -> dict:
    """Отправить ветки и тег, затем создать GitHub Release.

    Отдельный шаг: наружу — только по решению человека. Release создаётся **после**
    пуша: без тега на удалённом создавать нечего. Его провал выпуск не отменяет —
    тег и манифест уже на месте, значит обновления доедут.

    Версия, тег и заметки берутся из `release.json` — он единственный источник
    и уже лежит в релизном коммите.
    """
    branches = [RELEASE_BRANCH]
    integration = integration_branch(root)
    if integration:
        branches.append(integration)
    _git("push", "origin", *branches, "--tags", cwd=root)

    manifest = json.loads(_paths(root)["manifest"].read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md",
                                     delete=False) as tmp:
        tmp.write(manifest["notes"].strip() + "\n")
        notes = Path(tmp.name)
    try:
        released = create_github_release(
            manifest["tag"], f"Taskmark {manifest['version']}", notes)
    finally:
        notes.unlink(missing_ok=True)

    return {"ok": True, "pushed": True, "release": released}


# --- История ---------------------------------------------------------------


def release_tasks(commit: str, cwd: Path = ROOT) -> list[str]:
    """Состав выпуска из тела релизного коммита.

    Строки нет или формат другой — пустой список, а не ошибка: тег мог поставить
    и человек руками, и история от этого перестать читаться не должна.
    """
    try:
        body = _git("log", "-1", "--format=%B", commit, cwd=cwd)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    line = _TASKS_LINE.search(body)
    return _TASK_ID.findall(line.group("list")) if line else []


def history(cwd: Path = ROOT) -> list[dict]:
    """История выпусков из git: когда вышла версия и что в неё вошло.

    Отдельного файла с историей нет намеренно: он стал бы третьим источником
    тех же данных и первым, кто с ними разойдётся. Тег и релизный коммит
    разойтись с выпуском не могут — они **и есть** выпуск.

    Читает и только читает: ничего не пишет и не публикует.
    """
    try:
        raw = _git("for-each-ref", "--format=" + "%09".join(_TAG_FIELDS),
                   "refs/tags", cwd=cwd)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    entries: list[dict] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != len(_TAG_FIELDS):
            continue
        tag, tagged_at, created_at, deref, obj = parts
        try:
            parse_version(tag)
        except ValueError:
            continue  # тег не про версию — не выпуск
        commit = deref or obj
        entries.append({
            "version": tag.lstrip("vV"),
            "tag": tag,
            # Аннотация хранит время простановки тега — момент выпуска. Обычный
            # тег его не хранит вовсе, тогда берём время коммита и говорим об этом
            "released_at": tagged_at or created_at,
            "annotated": bool(tagged_at),
            "commit": commit,
            "tasks": release_tasks(commit, cwd=cwd),
        })

    entries.sort(key=lambda e: (e["released_at"], parse_version(e["version"])),
                 reverse=True)
    return entries


# --- CLI -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Выпуск версии Taskmark")
    parser.add_argument("--check", action="store_true", help="проверить готовность, ничего не менять")
    parser.add_argument("--apply", action="store_true", help="выпустить версию (без пуша)")
    parser.add_argument("--publish", action="store_true", help="отправить коммит и тег")
    parser.add_argument("--history", action="store_true",
                        help="история выпусков из git: когда и что вошло")
    parser.add_argument("--bump", choices=LEVELS, help="разряд версии")
    parser.add_argument("--notes", help="файл с текстом секции changelog")
    parser.add_argument("--tasks", default="", help="состав выпуска: TASK-001,TASK-002")
    parser.add_argument("--commits", default=None,
                        help="коммиты состава через запятую (при интеграционной ветке)")
    args = parser.parse_args()

    result: dict | list
    try:
        if args.check:
            result = check(bump=args.bump)
        elif args.history:
            result = history()
        elif args.apply:
            if not args.bump or not args.notes:
                raise ValueError("для --apply нужны --bump и --notes")
            notes = Path(args.notes).read_text(encoding="utf-8")
            tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
            commits = (None if args.commits is None else
                       [c.strip() for c in args.commits.split(",") if c.strip()])
            result = apply(args.bump, notes, tasks, commits=commits)
        elif args.publish:
            result = publish()
        else:
            raise ValueError("укажите --check, --apply, --publish или --history")
    except Exception as exc:  # noqa: BLE001 — наружу отдаём json, а не трассировку
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    # История — список выпусков, и пустой список отказом не является
    return 0 if not isinstance(result, dict) or result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
