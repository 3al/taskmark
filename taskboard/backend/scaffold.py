"""Развёртывание структуры tasks/ и агентского окружения (scaffold из UI)."""

from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

from backend.statuses import load_pipeline

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TASKS_TEMPLATES = TEMPLATES_DIR / "tasks"
AGENTIC_TEMPLATES = TEMPLATES_DIR / "agentic"
SKILLS_TEMPLATES = AGENTIC_TEMPLATES / ".claude" / "skills"
COMMANDS_TEMPLATES = AGENTIC_TEMPLATES / ".opencode" / "commands"

# Маркеры волт-блоков в шаблонах скиллов: при vault=False вырезаются целиком
VAULT_START = "<!-- vault -->"
VAULT_END = "<!-- /vault -->"

# Маркер наличия секции правил в агентском файле
RULES_MARKER = "TASK MANAGEMENT"

# Рубрики внутри раздела создания задач: по ним create_task.py раскладывает новое
BACKLOG_SUBSECTIONS = ("Рефакторинг (в порядке выполнения)", "Новый функционал и баги")

# Скрипты-инструменты в tasks/: (часть scaffold, ключ конфига, имя шаблона).
# Имя в проекте переименуемо через настройки, шаблон — нет
TOOL_SCRIPTS = (
    ("create_script", "create_script", "create_task.py"),
    ("status_script", "status_script", "set_status.py"),
)

# .gitignore для разворачиваемых агентских папок: не загрязнять git-дерево проекта
AGENTIC_GITIGNORE = (
    "# Агентское окружение, развёрнутое taskboard — в git не попадает\n*\n"
)


def _strip_vault_blocks(text: str) -> str:
    """Вырезать блоки <!-- vault --> ... <!-- /vault --> вместе с маркерами.

    Если внутри вырезанного блока был заголовок «## Шаг N» — перенумеровать
    последующие шаги и ссылки на них («шаг 6-7» → «шаг 5-6»).
    """
    out: list[str] = []
    skip = False
    removed_steps: list[int] = []
    for line in text.splitlines():
        marker = line.strip()
        if marker == VAULT_START:
            skip = True
            continue
        if marker == VAULT_END:
            skip = False
            continue
        if skip:
            m = re.match(r"^##\s+Шаг\s+(\d+)", line.strip())
            if m:
                removed_steps.append(int(m.group(1)))
            continue
        out.append(line)
    result = "\n".join(out)

    # Перенумерация: каждый вырезанный шаг сдвигает большие номера на -1
    for n in sorted(removed_steps):
        result = re.sub(
            r"(шаг\w*\s+)(\d+)(?:-(\d+))?",
            lambda m: _decrement_step_ref(m, n),
            result,
            flags=re.IGNORECASE,
        )

    # Схлопнуть серии пустых строк, оставшиеся после вырезки
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n"


def _decrement_step_ref(m: re.Match, removed: int) -> str:
    """Уменьшить номер шага (и конец диапазона) на 1, если он больше вырезанного."""
    num = int(m.group(2))
    head = m.group(1) + (str(num - 1) if num > removed else str(num))
    if m.group(3):
        tail = int(m.group(3))
        head += "-" + (str(tail - 1) if tail > removed else str(tail))
    return head


def _copy_file(src: Path, dst: Path) -> bool:
    """Скопировать файл, если dst не существует. Вернуть True, если создан."""
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _renumber_rules(rules_text: str, target_text: str) -> str:
    """Перенумеровать заголовок секции правил под существующие секции файла.

    В файле с секциями вида «# N. ...» — следующий номер; без нумерации —
    заголовок без номера.
    """
    numbers = [
        int(m.group(1))
        for m in re.finditer(r"^#\s+(\d+)\.", target_text, flags=re.MULTILINE)
    ]
    if not numbers:
        return re.sub(r"^#\s*\d+\.\s*", "# ", rules_text, count=1, flags=re.MULTILINE)
    return re.sub(
        r"^#\s*\d+\.", f"# {max(numbers) + 1}.", rules_text, count=1, flags=re.MULTILINE
    )


def render_rules(cfg: dict) -> str:
    """Секция правил под жизненный цикл конкретного проекта.

    Жёстко вписанный «backlog → development → review → testing → completed»
    врал бы всем, кто настроил пайплайн под себя, поэтому статусы в правилах
    подставляются из конфига — как и в скиллах, которые спрашивают их у скрипта.
    """
    pipeline = load_pipeline(cfg)
    statuses = pipeline.statuses()
    route = [s for s in statuses if not s.get("offramp")]
    offramps = [s for s in statuses if s.get("offramp")]

    line = " → ".join(s["key"] for s in route)
    if offramps:
        line += "  (+ вне маршрута: " + ", ".join(s["key"] for s in offramps) + ")"

    return (AGENTIC_TEMPLATES / "rules_section.md").read_text(encoding="utf-8").format(
        pipeline_line=line,
        statuses_line=" | ".join(s["key"] for s in statuses),
        sections_line=", ".join(s["section"] for s in statuses),
        pick_section=pipeline.section_of(pipeline.action("pick") or "") or "Queue",
        create_section=pipeline.section_of(pipeline.action("create") or "") or "Backlog",
        start_status=pipeline.action("start") or "development",
    )


def sync_rules(project_root: Path, cfg: dict, names: list[str] | None = None) -> list[str]:
    """Переписать уже развёрнутую секцию правил под текущий пайплайн проекта.

    Вызывается после смены жизненного цикла: иначе в AGENTS.md/CLAUDE.md
    остаётся описание прежнего маршрута, и агент действует по нему.
    Трогаем только саму секцию — остальной текст файла принадлежит пользователю.
    names — обновить только перечисленные файлы (точечное обновление из UI).
    """
    updated: list[str] = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        if names is not None and name not in names:
            continue
        target = project_root / name
        if not target.is_file():
            continue
        content = target.read_text(encoding="utf-8-sig")
        bounds = _rules_bounds(content)
        if not bounds:
            continue
        start, end = bounds
        section = _renumber_rules(render_rules(cfg), content[:start])
        fresh = content[:start] + section.rstrip("\n") + "\n" + content[end:]
        if fresh != content:
            target.write_text(fresh, encoding="utf-8")
            updated.append(name)
    return updated


def _rules_bounds(content: str) -> tuple[int, int] | None:
    """Границы секции правил в агентском файле: (начало заголовка, конец секции)."""
    heading = None
    for m in re.finditer(r"^#\s+.*$", content, flags=re.MULTILINE):
        if RULES_MARKER.lower() in m.group(0).lower():
            heading = m
            break
    if heading is None:
        return None
    rest = content[heading.end():]
    nxt = re.search(r"^#\s+", rest, flags=re.MULTILINE)
    return heading.start(), heading.end() + (nxt.start() if nxt else len(rest))


def _append_rules(project_root: Path, names: list[str], cfg: dict) -> tuple[list[str], list[str]]:
    """Дописать секцию правил в указанные агентские файлы (создаёт при отсутствии).

    Вернуть (дописано, уже_было).
    """
    rules_text = render_rules(cfg)

    appended: list[str] = []
    present: list[str] = []
    for name in names:
        target = project_root / name
        # utf-8-sig: BOM в начале файла не должен ломать детект секций и нумерацию
        content = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        if RULES_MARKER.lower() in content.lower():
            present.append(name)  # существующую секцию обновляет sync_rules
            continue
        section = _renumber_rules(rules_text, content)
        target.write_text(content.rstrip("\n") + "\n\n" + section, encoding="utf-8")
        appended.append(name)
    return appended, present


def scaffold_project(tasks_dir: Path, cfg: dict, options: dict | None = None) -> dict:
    """Развернуть структуру tasks/ и (опционально) агентское окружение.

    options:
      skills       — скиллы Claude Code в <корень>/.claude/skills/
      commands     — команды-обёртки opencode в <корень>/.opencode/commands/
      rules_agents — секция правил TASK MANAGEMENT в AGENTS.md
      rules_claude — секция правил TASK MANAGEMENT в CLAUDE.md
      vault        — оставить волт-блоки в скиллах (иначе вырезаются)
      parts        — точечное восстановление перечисленных частей
                     (board | create_script | epics | gitignore | logs |
                     skills | commands); остальное не трогается

    Существующие артефакты не перезаписываются (попадают в skipped).
    Исключение — инструменты, а не данные: create_task.py, скиллы и
    команды обновляются до шаблонной версии (попадают в replaced).
    """
    options = options or {}
    opt_skills = options.get("skills", True)
    opt_commands = options.get("commands", True)
    # Легаси-ключ "rules" управляет обоими файлами, если раздельные не переданы
    legacy_rules = options.get("rules", True)
    opt_rules_agents = options.get("rules_agents", legacy_rules)
    opt_rules_claude = options.get("rules_claude", legacy_rules)
    opt_vault = options.get("vault", False)
    parts = options.get("parts")

    created: list[str] = []
    skipped: list[str] = []
    replaced: list[str] = []

    # --- Структура tasks/ (полностью или только запрошенные части) ---
    tasks_dir.mkdir(parents=True, exist_ok=True)
    want = set(parts) if parts else {"board", "create_script", "status_script",
                                     "epics", "gitignore", "logs"}

    if "board" in want:
        board_name = cfg.get("board_file", "board.md")
        if _copy_file_raw(
            TASKS_TEMPLATES / "board.md",
            tasks_dir / board_name,
            lambda t: t.format(sections=render_sections(load_pipeline(cfg))),
        ):
            created.append(board_name)
        else:
            skipped.append(board_name)

    # Скрипты — инструменты, а не данные пользователя: устаревшую версию
    # обновляем до шаблонной (иначе проект остаётся без свежих возможностей)
    for part, cfg_key, template_name in TOOL_SCRIPTS:
        if part not in want:
            continue
        script_name = cfg.get(cfg_key, template_name)
        script_path = tasks_dir / script_name
        template_text = (TASKS_TEMPLATES / template_name).read_text(encoding="utf-8")
        if not script_path.exists():
            script_path.write_text(template_text, encoding="utf-8")
            created.append(script_name)
        elif script_path.read_text(encoding="utf-8-sig") == template_text:
            skipped.append(script_name)
        else:
            script_path.write_text(template_text, encoding="utf-8")
            replaced.append(script_name)

    # (часть, файл-шаблон, имя в проекте): шаблон gitignore хранится без точки —
    # иначе он сам срабатывает как ignore-правило в репозитории инструмента
    for part, src_name, dst_name in (("epics", "epics.md", "epics.md"),
                                     ("gitignore", "gitignore_template", ".gitignore")):
        if part in want:
            if _copy_file(TASKS_TEMPLATES / src_name, tasks_dir / dst_name):
                created.append(dst_name)
            else:
                skipped.append(dst_name)

    if "logs" in want:
        logs_name = cfg.get("logs_dir", "logs")
        logs_path = tasks_dir / logs_name
        if logs_path.exists():
            skipped.append(f"{logs_name}/")
        else:
            logs_path.mkdir()
            created.append(f"{logs_name}/")

    project_root = tasks_dir.parent

    # Точечное восстановление: только запрошенные части
    if parts:
        names = options.get("names")
        for part in ("skills", "commands", "rules"):
            if part in want:
                c, r, s = refresh_agentic(project_root, part, names=names, cfg=cfg)
                created += c
                replaced += r
                skipped += s
        return {"created": created, "skipped": skipped, "replaced": replaced,
                "rules": {"appended": [], "already_present": []}}

    # --- Агентское окружение (в корне проекта) ---

    if opt_skills:
        # Режим волта здесь задаёт пользователь чекбоксом, а не текущее состояние проекта
        c, r, s = refresh_agentic(project_root, "skills", vault=opt_vault)
        created += c
        replaced += r
        skipped += s
        if _write_if_absent(project_root / ".claude" / ".gitignore", AGENTIC_GITIGNORE):
            created.append(".claude/.gitignore")
        else:
            skipped.append(".claude/.gitignore")

    if opt_commands:
        c, r, s = refresh_agentic(project_root, "commands")
        created += c
        replaced += r
        skipped += s
        if _write_if_absent(project_root / ".opencode" / ".gitignore", AGENTIC_GITIGNORE):
            created.append(".opencode/.gitignore")
        else:
            skipped.append(".opencode/.gitignore")

    rules_appended: list[str] = []
    rules_present: list[str] = []
    rules_names = ([n for n, on in (("AGENTS.md", opt_rules_agents),
                                    ("CLAUDE.md", opt_rules_claude)) if on])
    if rules_names:
        rules_appended, rules_present = _append_rules(project_root, rules_names, cfg)
        # Уже развёрнутую секцию приводим к эталону под текущий пайплайн:
        # правила — такой же инструмент, как скиллы, а не данные пользователя
        sync_rules(project_root, cfg, rules_present)

    return {
        "created": created,
        "skipped": skipped,
        "replaced": replaced,
        "rules": {"appended": rules_appended, "already_present": rules_present},
    }


def _copy_file_raw(src: Path, dst: Path, transform) -> bool:
    """Записать src в dst через текстовую трансформацию, если dst не существует."""
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(transform(src.read_text(encoding="utf-8")), encoding="utf-8")
    return True


def _write_if_absent(dst: Path, text: str) -> bool:
    """Записать текст в dst, если файла нет. Вернуть True, если создан."""
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return True


# --- Актуальность развёрнутого агентского окружения ---

def _deployed_skills(project_root: Path) -> Path:
    return project_root / ".claude" / "skills"


def _deployed_commands(project_root: Path) -> Path:
    return project_root / ".opencode" / "commands"


def _read(path: Path) -> str | None:
    """Прочитать файл (BOM не должен считаться расхождением). None — нет файла."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception:
        return None


def _same_content(current: str | None, expected: str) -> bool:
    """Совпадает ли развёрнутый файл с эталоном — построчно, как в diff.

    Ровно то сравнение, что показывает пользователю agentic_diff: иначе
    расхождение, невидимое в diff (редактор съел хвостовой перевод строки),
    даёт баннер устаревания, который нечем объяснить и нельзя убрать правкой.
    """
    return current is not None and current.splitlines() == expected.splitlines()


def _skill_targets(project_root: Path, vault: bool | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для каждого скилла шаблона.

    Эталон выбирается по режиму волта: явный (выбор пользователя при
    развёртывании) или, если не задан, определённый по самому проекту.
    """
    if vault is None:
        vault = uses_vault(project_root)
    out: list[tuple[str, Path, str]] = []
    for skill_dir in sorted(SKILLS_TEMPLATES.iterdir()):
        if not skill_dir.is_dir():
            continue
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        text = raw if vault else _strip_vault_blocks(raw)
        out.append((skill_dir.name,
                    _deployed_skills(project_root) / skill_dir.name / "SKILL.md",
                    text))
    return out


def _command_targets(project_root: Path) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для команд opencode."""
    return [
        (f.stem, _deployed_commands(project_root) / f.name, f.read_text(encoding="utf-8"))
        for f in sorted(COMMANDS_TEMPLATES.glob("*.md"))
    ]


def render_sections(pipeline) -> str:
    """Разделы доски по пайплайну проекта: `## <раздел>` в порядке жизненного цикла.

    Раздел создания задач получает подразделы-рубрики — по ним create_task.py
    раскладывает новые задачи; остальные разделы стартуют пустыми.
    """
    create = pipeline.action("create")
    blocks: list[str] = []
    for status in pipeline.statuses():
        blocks.append(f"## {status['section']}\n")
        if status["key"] == create:
            for title in BACKLOG_SUBSECTIONS:
                blocks.append(f"### {title}\n\n_(нет)_\n")
        else:
            blocks.append("_(нет)_\n")
    return "\n".join(blocks)


def agentic_paths(project_root: Path) -> list[Path]:
    """Развёрнутые папки агентского окружения (для наблюдения за изменениями).

    Точечно скиллы и команды, а не корень и не `.claude` целиком: рядом
    лежат часто меняющиеся настройки самих агентов — это был бы шум.
    """
    return [p for p in (_deployed_skills(project_root), _deployed_commands(project_root))
            if p.is_dir()]


def uses_vault(project_root: Path) -> bool:
    """Развёрнуты ли скиллы с блоками волта знаний.

    Определяем по самим файлам: при vault=False блоки вырезаны вместе с
    маркерами, значит их наличие = проект развёрнут с поддержкой волта.
    """
    for skill in _deployed_skills(project_root).glob("*/SKILL.md"):
        if VAULT_START in (_read(skill) or ""):
            return True
    return False


RULES_FILES = ("AGENTS.md", "CLAUDE.md")


def rules_deployed(project_root: Path) -> list[str]:
    """Агентские файлы, в которых секция правил уже развёрнута."""
    out: list[str] = []
    for name in RULES_FILES:
        target = project_root / name
        if target.is_file() and _rules_bounds(target.read_text(encoding="utf-8-sig")):
            out.append(name)
    return out


def rules_missing(project_root: Path) -> list[str]:
    """Агентские файлы, которым не хватает секции правил.

    Существующий файл без секции — это полурабочее состояние: агент, который
    читает именно его, не знает процесса. Файлов нет совсем — заводим оба.
    """
    existing = [n for n in RULES_FILES if (project_root / n).is_file()]
    if not existing:
        return list(RULES_FILES)
    deployed = rules_deployed(project_root)
    return [n for n in existing if n not in deployed]


def _rules_targets(project_root: Path, cfg: dict) -> list[tuple[str, Path, str]]:
    """(имя файла, путь, эталонный текст секции) для развёрнутых правил.

    Эталон считается под пайплайн проекта и с номером заголовка, который
    секция занимает в этом файле, — иначе перенумерация выглядела бы
    расхождением.
    """
    out: list[tuple[str, Path, str]] = []
    for name in rules_deployed(project_root):
        target = project_root / name
        content = target.read_text(encoding="utf-8-sig")
        bounds = _rules_bounds(content)
        if bounds:
            out.append((name, target, _renumber_rules(render_rules(cfg), content[:bounds[0]])))
    return out


def _current_text(part: str, path: Path) -> str | None:
    """Текущее содержимое развёрнутой части (для правил — только их секция)."""
    if part != "rules":
        return _read(path)
    content = _read(path)
    if content is None:
        return None
    bounds = _rules_bounds(content)
    return content[bounds[0]:bounds[1]] if bounds else None


def _deployed_parts(project_root: Path,
                    cfg: dict | None = None) -> list[tuple[str, list[tuple[str, Path, str]]]]:
    """Развёрнутые части окружения с их эталонами.

    Часть, которую в проекте вообще не разворачивали, не проверяется: не все
    проекты хотят скиллы, требовать их обновления — шум.
    """
    parts: list[tuple[str, list[tuple[str, Path, str]]]] = []
    if any(_deployed_skills(project_root).glob("*/SKILL.md")):
        parts.append(("skills", _skill_targets(project_root)))
    if any(_deployed_commands(project_root).glob("*.md")):
        parts.append(("commands", _command_targets(project_root)))
    rules = _rules_targets(project_root, cfg or {})
    if rules:
        parts.append(("rules", rules))
    return parts


def agentic_stale_details(project_root: Path, cfg: dict | None = None) -> list[dict]:
    """Подробности по расхождениям: [{part, name, state, path}].

    state: "modified" — файл есть, но отличается от шаблона;
           "missing"  — шаблон появился позже развёртывания.
    """
    items: list[dict] = []
    for part, targets in _deployed_parts(project_root, cfg):
        for name, path, expected in targets:
            current = _current_text(part, path)
            if _same_content(current, expected):
                continue
            items.append({
                "part": part,
                "name": name,
                "state": "missing" if current is None else "modified",
                "path": str(path.relative_to(project_root)).replace("\\", "/"),
            })
    return items


def agentic_stale(project_root: Path, cfg: dict | None = None) -> dict[str, list[str]]:
    """Устаревшие/недостающие части: {"skills": [...], "commands": [...], "rules": [...]}."""
    result: dict[str, list[str]] = {"skills": [], "commands": [], "rules": []}
    for item in agentic_stale_details(project_root, cfg):
        result[item["part"]].append(item["name"])
    return result


def agentic_diff(project_root: Path, part: str, name: str, cfg: dict | None = None) -> dict:
    """Unified diff «развёрнутое → эталон» для скилла, команды или секции правил.

    Направление выбрано так, чтобы «+» читалось как «появится после обновления».
    Эталон берётся с учётом волт-режима проекта (иначе у проектов без волта
    вырезанные блоки выглядели бы расхождением) и пайплайна — для правил.
    """
    if part == "skills":
        targets = _skill_targets(project_root)
    elif part == "commands":
        targets = _command_targets(project_root)
    else:
        targets = _rules_targets(project_root, cfg or {})
    target = next((t for t in targets if t[0] == name), None)
    if target is None:
        return {"ok": False, "error": f"Неизвестный элемент: {part}/{name}"}

    _name, path, expected = target
    current = _current_text(part, path)
    if current is None:
        state = "missing"
    else:
        state = "modified" if not _same_content(current, expected) else "same"

    diff_lines = list(difflib.unified_diff(
        (current or "").splitlines(),
        expected.splitlines(),
        fromfile=f"{name} — в проекте",
        tofile=f"{name} — шаблон",
        lineterm="",
    ))
    body = [ln for ln in diff_lines if not ln.startswith(("---", "+++"))]
    added = sum(1 for ln in body if ln.startswith("+"))
    removed = sum(1 for ln in body if ln.startswith("-"))

    return {"ok": True, "part": part, "name": name, "state": state,
            "diff": "\n".join(diff_lines), "added": added, "removed": removed}


def refresh_agentic(project_root: Path, part: str, vault: bool | None = None,
                    names: list[str] | None = None,
                    cfg: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """Развернуть/обновить скиллы, команды или секцию правил до эталона.

    Как и create_task.py, это инструмент, а не данные пользователя:
    расходящийся файл перезаписывается. vault=None — сохранить режим волта,
    уже сложившийся в проекте (точечное обновление из UI).
    names — обновить только перечисленные элементы (остальные не трогать).
    Возвращает (created, replaced, skipped) — относительные пути.
    """
    if part == "rules":
        # Правила живут секцией внутри пользовательского файла: устаревшую
        # пересобираем, недостающую дописываем. Если агентских файлов нет
        # совсем — заводим оба: проект без правил полурабочий, агент не знает
        # ни очереди, ни как менять статус
        wanted = list(names) if names else sorted(
            set(rules_deployed(project_root)) | set(rules_missing(project_root)))
        appended, _present = _append_rules(project_root, wanted, cfg or {})
        updated = sync_rules(project_root, cfg or {}, wanted)
        return appended, [n for n in updated if n not in appended], []

    targets = (_skill_targets(project_root, vault) if part == "skills"
               else _command_targets(project_root))
    if names is not None:
        wanted = set(names)
        targets = [t for t in targets if t[0] in wanted]
    prefix = ".claude/skills" if part == "skills" else ".opencode/commands"

    created: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    for name, path, expected in targets:
        rel = f"{prefix}/{name}/SKILL.md" if part == "skills" else f"{prefix}/{name}.md"
        current = _read(path)
        if _same_content(current, expected):
            skipped.append(rel)  # различия только в переводах строк — не трогаем
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        (created if current is None else replaced).append(rel)
    return created, replaced, skipped
