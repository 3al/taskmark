"""Развёртывание структуры tasks/ и агентского окружения (scaffold из UI)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

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


def _append_rules(project_root: Path, names: list[str]) -> tuple[list[str], list[str]]:
    """Дописать секцию правил в указанные агентские файлы (создаёт при отсутствии).

    Вернуть (дописано, уже_было).
    """
    rules_text = (AGENTIC_TEMPLATES / "rules_section.md").read_text(encoding="utf-8")

    appended: list[str] = []
    present: list[str] = []
    for name in names:
        target = project_root / name
        # utf-8-sig: BOM в начале файла не должен ломать детект секций и нумерацию
        content = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        if RULES_MARKER.lower() in content.lower():
            present.append(name)
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
            lambda t: t.format(queue_section=cfg.get("queue_section", "Queue")),
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

    # Точечное восстановление: только запрошенные части, правила не трогаем
    if parts:
        for part in ("skills", "commands"):
            if part in want:
                c, r, s = refresh_agentic(project_root, part)
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
        rules_appended, rules_present = _append_rules(project_root, rules_names)

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


def agentic_stale(project_root: Path) -> dict[str, list[str]]:
    """Устаревшие/недостающие части агентского окружения.

    Возвращает {"skills": [имена], "commands": [имена]}. Часть, которую в
    проекте вообще не разворачивали, не проверяется (пустой список): не
    все проекты хотят скиллы, требовать их обновления — шум.
    """
    result: dict[str, list[str]] = {"skills": [], "commands": []}

    if any(_deployed_skills(project_root).glob("*/SKILL.md")):
        result["skills"] = [
            name for name, path, expected in _skill_targets(project_root)
            if _read(path) != expected
        ]
    if any(_deployed_commands(project_root).glob("*.md")):
        result["commands"] = [
            name for name, path, expected in _command_targets(project_root)
            if _read(path) != expected
        ]
    return result


def refresh_agentic(project_root: Path, part: str,
                    vault: bool | None = None) -> tuple[list[str], list[str], list[str]]:
    """Развернуть/обновить скиллы или команды до шаблонной версии.

    Как и create_task.py, это инструмент, а не данные пользователя:
    расходящийся файл перезаписывается. vault=None — сохранить режим волта,
    уже сложившийся в проекте (точечное обновление из UI).
    Возвращает (created, replaced, skipped) — относительные пути.
    """
    targets = (_skill_targets(project_root, vault) if part == "skills"
               else _command_targets(project_root))
    prefix = ".claude/skills" if part == "skills" else ".opencode/commands"

    created: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    for name, path, expected in targets:
        rel = f"{prefix}/{name}/SKILL.md" if part == "skills" else f"{prefix}/{name}.md"
        current = _read(path)
        if current == expected:
            skipped.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        (created if current is None else replaced).append(rel)
    return created, replaced, skipped
