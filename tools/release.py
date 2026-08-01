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

    release.py --apply --bump LEVEL --notes ФАЙЛ --tasks TASK-001,TASK-002
        {"ok": true, "version": "1.1.0", "tag": "v1.1.0"}
        Отказ — ненулевой код возврата и {"ok": false, "error": "..."}.

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

LEVELS = ("major", "minor", "patch")

# Секция версии в changelog: «## [1.4.0] — 2026-08-01» до следующей такой же
_SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
                      re.S | re.M)

_VERSION = re.compile(r"^\d+(\.\d+)*$")


# --- Версия ----------------------------------------------------------------


def parse_version(value: str) -> tuple[int, ...]:
    """Строка версии → кортеж чисел. Мусор — ValueError."""
    text = (value or "").strip().lstrip("vV")
    if not _VERSION.match(text):
        raise ValueError(f"не похоже на версию: {value!r}")
    return tuple(int(p) for p in text.split("."))


def current_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


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


def _git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
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


def blockers() -> list[str]:
    """Что мешает выпускать прямо сейчас. Список, а не первое встреченное.

    Человеку нужно увидеть всё сразу: чинить по одному, каждый раз запуская
    выпуск заново, — худший из возможных сценариев.
    """
    found: list[str] = []
    try:
        if _git("status", "--porcelain", "--untracked-files=no"):
            found.append("в рабочем дереве есть незакоммиченные правки")
        if _git("rev-parse", "--abbrev-ref", "HEAD") != "main":
            found.append("выпуск делается не с ветки main")
    except (subprocess.CalledProcessError, FileNotFoundError):
        found.append("git недоступен или это не репозиторий")
    if not dist_is_fresh():
        found.append("собранный фронтенд старее исходников — нужен npm run build")
    try:
        # Совпадение — норма: между выпусками changelog описывает установленную
        # версию. Расхождение значит, что VERSION и changelog подняли порознь
        section = top_section()
        if section["version"] != current_version():
            found.append(
                f"changelog описывает {section['version']}, "
                f"а установлена {current_version()} — они разошлись")
    except (ValueError, OSError) as exc:
        found.append(f"changelog: {exc}")
    return found


def check(bump: str | None = None) -> dict:
    """Что скилл показывает человеку до подтверждения. Ничего не меняет."""
    current = current_version()
    result: dict = {"ok": True, "current": current, "next": None,
                    "blockers": blockers()}
    if bump:
        try:
            result["next"] = next_version(current, bump)
        except ValueError as exc:
            result["ok"] = False
            result["error"] = str(exc)
    return result


# --- Выпуск ----------------------------------------------------------------


def apply(bump: str, notes: str, tasks: list[str]) -> dict:
    """Выпустить версию: changelog → VERSION → манифест → коммит → тег.

    Пуш и GitHub Release здесь не делаются: они необратимы для пользователей,
    и решение остаётся за человеком (скилл спрашивает и зовёт `--publish`).
    """
    version = next_version(current_version(), bump)
    stoppers = blockers()
    if stoppers:
        return {"ok": False, "error": "; ".join(stoppers)}

    insert_section(CHANGELOG, version, date.today().isoformat(), notes)
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    manifest = build_manifest(CHANGELOG, version)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    _git("add", "--", str(VERSION_FILE), str(CHANGELOG), str(MANIFEST))
    body = "Задачи выпуска: " + ", ".join(tasks) if tasks else "Выпуск без привязки к задачам."
    _git("commit", "-m", f"Релиз {version}", "-m", body)

    # Заметки в аннотацию тега: их читают из консоли (`git show`, `git tag -n`).
    # На странице тега разметка не рендерится — это работа Release, см. publish()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md",
                                     delete=False) as tmp:
        tmp.write(f"Taskmark {version}\n\n{manifest['notes'].strip()}\n")
        annotation = Path(tmp.name)
    try:
        _git(*tag_args(manifest["tag"], annotation))
    finally:
        annotation.unlink(missing_ok=True)
    return {"ok": True, "version": version, "tag": manifest["tag"],
            "commit": _git("rev-parse", "--short", "HEAD")}


def publish() -> dict:
    """Отправить коммит и тег, затем создать GitHub Release.

    Отдельный шаг: наружу — только по решению человека. Release создаётся **после**
    пуша: без тега на удалённом создавать нечего. Его провал выпуск не отменяет —
    тег и манифест уже на месте, значит обновления доедут.

    Версия, тег и заметки берутся из `release.json` — он единственный источник
    и уже лежит в релизном коммите.
    """
    _git("push", "origin", "main", "--tags")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
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


# --- CLI -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Выпуск версии Taskmark")
    parser.add_argument("--check", action="store_true", help="проверить готовность, ничего не менять")
    parser.add_argument("--apply", action="store_true", help="выпустить версию (без пуша)")
    parser.add_argument("--publish", action="store_true", help="отправить коммит и тег")
    parser.add_argument("--bump", choices=LEVELS, help="разряд версии")
    parser.add_argument("--notes", help="файл с текстом секции changelog")
    parser.add_argument("--tasks", default="", help="состав выпуска: TASK-001,TASK-002")
    args = parser.parse_args()

    try:
        if args.check:
            result = check(bump=args.bump)
        elif args.apply:
            if not args.bump or not args.notes:
                raise ValueError("для --apply нужны --bump и --notes")
            notes = Path(args.notes).read_text(encoding="utf-8")
            tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
            result = apply(args.bump, notes, tasks)
        elif args.publish:
            result = publish()
        else:
            raise ValueError("укажите --check, --apply или --publish")
    except Exception as exc:  # noqa: BLE001 — наружу отдаём json, а не трассировку
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
