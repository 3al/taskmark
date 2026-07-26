"""Развёртывание структуры tasks/ и агентского окружения (scaffold из UI)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TASKS_TEMPLATES = TEMPLATES_DIR / "tasks"
AGENTIC_TEMPLATES = TEMPLATES_DIR / "agentic"

# Маркеры волт-блоков в шаблонах скиллов: при vault=False вырезаются целиком
VAULT_START = "<!-- vault -->"
VAULT_END = "<!-- /vault -->"

# Маркер наличия секции правил в агентском файле
RULES_MARKER = "TASK MANAGEMENT"

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


def _copy_file(src: Path, dst: Path, strip_vault: bool = False) -> bool:
    """Скопировать файл, если dst не существует. Вернуть True, если создан."""
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if strip_vault:
        text = _strip_vault_blocks(src.read_text(encoding="utf-8"))
        dst.write_text(text, encoding="utf-8")
    else:
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

    Существующие артефакты не перезаписываются (попадают в skipped).
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
    want = set(parts) if parts else {"board", "create_script", "epics", "gitignore", "logs"}

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

    if "create_script" in want:
        # Скрипт — инструмент, а не данные: устаревшую версию обновляем
        # до шаблонной (поддержка произвольных подразделов, эпики и т.д.)
        script_name = cfg.get("create_script", "create_task.py")
        script_path = tasks_dir / script_name
        template_text = (TASKS_TEMPLATES / "create_task.py").read_text(encoding="utf-8")
        if not script_path.exists():
            script_path.write_text(template_text, encoding="utf-8")
            created.append(script_name)
        elif script_path.read_text(encoding="utf-8-sig") == template_text:
            skipped.append(script_name)
        else:
            script_path.write_text(template_text, encoding="utf-8")
            replaced.append(script_name)

    for part, name in (("epics", "epics.md"), ("gitignore", ".gitignore")):
        if part in want:
            if _copy_file(TASKS_TEMPLATES / name, tasks_dir / name):
                created.append(name)
            else:
                skipped.append(name)

    if "logs" in want:
        logs_name = cfg.get("logs_dir", "logs")
        logs_path = tasks_dir / logs_name
        if logs_path.exists():
            skipped.append(f"{logs_name}/")
        else:
            logs_path.mkdir()
            created.append(f"{logs_name}/")

    # Точечное восстановление — агентское окружение не трогаем
    if parts:
        return {"created": created, "skipped": skipped, "replaced": replaced,
                "rules": {"appended": [], "already_present": []}}

    # --- Агентское окружение (в корне проекта) ---
    project_root = tasks_dir.parent

    if opt_skills:
        skills_src = AGENTIC_TEMPLATES / ".claude" / "skills"
        for skill_dir in sorted(skills_src.iterdir()):
            if not skill_dir.is_dir():
                continue
            rel = f".claude/skills/{skill_dir.name}/SKILL.md"
            if _copy_file(skill_dir / "SKILL.md", project_root / rel,
                          strip_vault=not opt_vault):
                created.append(rel)
            else:
                skipped.append(rel)
        if _write_if_absent(project_root / ".claude" / ".gitignore", AGENTIC_GITIGNORE):
            created.append(".claude/.gitignore")
        else:
            skipped.append(".claude/.gitignore")

    if opt_commands:
        commands_src = AGENTIC_TEMPLATES / ".opencode" / "commands"
        for cmd_file in sorted(commands_src.glob("*.md")):
            rel = f".opencode/commands/{cmd_file.name}"
            if _copy_file(cmd_file, project_root / rel):
                created.append(rel)
            else:
                skipped.append(rel)
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
