"""Развёртывание структуры tasks/ и агентского окружения (scaffold из UI)."""

from __future__ import annotations

import difflib
import re
import shutil
from datetime import date
from pathlib import Path, PurePosixPath

from backend.epics import EPICS_FILE
from backend.statuses import load_pipeline

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TASKS_TEMPLATES = TEMPLATES_DIR / "tasks"
AGENTIC_TEMPLATES = TEMPLATES_DIR / "agentic"
SKILLS_TEMPLATES = AGENTIC_TEMPLATES / ".claude" / "skills"
COMMANDS_TEMPLATES = AGENTIC_TEMPLATES / ".opencode" / "commands"
VAULT_TEMPLATES = TEMPLATES_DIR / "vault"

# Маркеры волт-блоков в шаблонах скиллов и правил: при vault=False вырезаются целиком
VAULT_START = "<!-- vault -->"
VAULT_END = "<!-- /vault -->"

# Инструменты, которые нужны только волту: без него это лишние файлы, на
# которые к тому же никто не ссылается (волт-блоки соседних скиллов вырезаны)
VAULT_SKILLS = ("write-vault",)

# Папка волта фиксирована: скиллы и правила ссылаются на `vault/` десятками
# упоминаний в тексте — подстановка имени в каждое усложнила бы шаблоны ради
# случая, которого никто не просил
VAULT_DIR = "vault"

# Что в vault/SYS/ — поставка (сверяется с шаблоном), а что данные пользователя.
# README и таксономию заполняет он сам: сверять их с эталоном значило бы
# показывать баннер устаревания на каждую собственную запись
VAULT_TEMPLATE_FILES = ("SYS/structure.md",
                        "SYS/templates/business-note.md",
                        "SYS/templates/code-note.md")
VAULT_DATA_FILES = ("SYS/README.md", "SYS/taxonomy.md")

# Служебные папки волта (верхний уровень поставляемых путей): только они
# наблюдаются на изменения — заметки пользователя в доменных папках правятся
# постоянно, и дёргать ими доску незачем
VAULT_SYSTEM_DIRS = tuple(dict.fromkeys(
    PurePosixPath(rel).parts[0] for rel in VAULT_TEMPLATE_FILES + VAULT_DATA_FILES))

# Маркер наличия секции правил в агентском файле
RULES_MARKER = "TASK MANAGEMENT"

# Рубрики внутри раздела создания задач: по ним create_task.py раскладывает новое
BACKLOG_SUBSECTIONS = ("Рефакторинг (в порядке выполнения)", "Новый функционал и баги")

# Эталон структуры файла задачи: его же копирует человек вручную, на него
# ссылаются скиллы, и из него create_task.py собирает новую задачу
TASK_TEMPLATE_FILE = "_TEMPLATE.md"

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

# Волт — локальная внешняя память проекта, а не его исходники
VAULT_GITIGNORE = (
    "# Knowledge Vault — локальная память проекта, в git не попадает\n*\n"
)

# Среды агентов, для которых разворачивается окружение. Выбор хранится в
# конфиге проекта (ключ "harnesses"): по раскладке на диске его не угадать —
# папки может не быть просто потому, что проект ещё не открывали в этой среде,
# а планы на неё знает только пользователь.
HARNESSES = ("claude", "opencode")

# Файл правил каждой среды — тот, который она реально читает
HARNESS_RULES_FILE = {"claude": "CLAUDE.md", "opencode": "AGENTS.md"}

# Опция scaffold, которой можно отказаться от конкретного файла правил
_RULES_OPTION = {"CLAUDE.md": "rules_claude", "AGENTS.md": "rules_agents"}


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

    # Про волт в правилах пишем только тем, у кого он есть: иначе агент читает
    # инструкцию к хранилищу, которого в проекте нет
    template = (AGENTIC_TEMPLATES / "rules_section.md").read_text(encoding="utf-8")
    if not cfg.get("vault"):
        template = _strip_vault_blocks(template)

    return template.format(
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
    Расходящиеся с шаблоном инструменты (правленные скиллы, скрипты) полное
    развёртывание тоже не трогает — они попадают в diverged: у пользователя
    там могут быть свои инструкции, и терять их по нажатию одной кнопки
    нельзя. Перезапись возможна только точечно (parts), то есть по кнопке
    «Обновить» рядом с diff, где видно, что именно изменится.
    """
    options = options or {}
    # Выбор сред задаёт состав поставки: он же ляжет в конфиг проекта, поэтому
    # дальше все функции раскладки читают его отсюда, а не гадают по диску
    chosen = options.get("harnesses")
    if isinstance(chosen, dict):
        cfg = {**cfg, "harnesses": {h: bool(chosen.get(h)) for h in HARNESSES}}
    active = harnesses(tasks_dir.parent, cfg)

    opt_skills = options.get("skills", any(active.values()))
    opt_commands = options.get("commands", active["opencode"])
    opt_vault = options.get("vault", False)
    parts = options.get("parts")

    created: list[str] = []
    skipped: list[str] = []
    replaced: list[str] = []
    diverged: list[str] = []
    # Перезаписываем только при точечном восстановлении: там пользователь
    # нажал «Обновить» у конкретного элемента, посмотрев его diff
    overwrite = bool(parts)

    # --- Структура tasks/ (полностью или только запрошенные части) ---
    tasks_dir.mkdir(parents=True, exist_ok=True)
    want = set(parts) if parts else {"board", "create_script", "status_script",
                                     "template", "epics", "gitignore", "logs"}

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
    # обновляем до шаблонной, но только по явной команде (кнопка «Обновить»).
    # Полное развёртывание расходящийся файл не трогает — см. overwrite
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
        elif overwrite:
            script_path.write_text(template_text, encoding="utf-8")
            replaced.append(script_name)
        else:
            diverged.append(script_name)

    # Шаблон задачи — инструмент, а не данные: правится редко, но обновляется
    # до эталона по кнопке, как скрипты
    if "template" in want:
        template_path = tasks_dir / TASK_TEMPLATE_FILE
        template_text = (TASKS_TEMPLATES / TASK_TEMPLATE_FILE).read_text(encoding="utf-8")
        if not template_path.exists():
            template_path.write_text(template_text, encoding="utf-8")
            created.append(TASK_TEMPLATE_FILE)
        elif _same_content(_read(template_path), template_text):
            skipped.append(TASK_TEMPLATE_FILE)
        elif overwrite:
            template_path.write_text(template_text, encoding="utf-8")
            replaced.append(TASK_TEMPLATE_FILE)
        else:
            diverged.append(TASK_TEMPLATE_FILE)

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
        if "vault" in want:
            c, r, s, d = deploy_vault(project_root, overwrite=True, names=names)
            created += c
            replaced += r
            skipped += s
            diverged += d
            # Волт без своего скилла — половина работы: пользователь нажал одну
            # кнопку и ждёт рабочее хранилище, а не заготовку под него.
            # Недостающее создаём, правленное не трогаем (overwrite=False)
            if names is None:
                for agentic in ("skills", "commands"):
                    if agentic in want:
                        continue  # эту часть и так развернут ниже, целиком
                    # Команды нужны только opencode: у остальных их не бывает
                    if agentic == "commands" and not active["opencode"]:
                        continue
                    c, r, s, d = refresh_agentic(project_root, agentic, vault=True, cfg=cfg,
                                                 names=list(VAULT_SKILLS), overwrite=False)
                    created += c
                    replaced += r
                    diverged += d
        for part in ("skills", "commands", "rules"):
            if part not in want:
                continue
            c, r, s, d = refresh_agentic(project_root, part, names=names, cfg=cfg)
            created += c
            replaced += r
            skipped += s
            diverged += d
            # Кнопка на баннере разворачивает часть в папку, которой могло ещё
            # не быть: без .gitignore её содержимое утечёт в git проекта
            if part in ("skills", "commands") and c:
                if part == "commands":
                    outcome = _ensure_commands_ignored(project_root)
                    if outcome == "created":
                        created.append(".opencode/.gitignore")
                    elif outcome == "appended":
                        replaced.append(".opencode/.gitignore (дописано: commands/)")
                else:
                    agent_dir = _deployed_skills(project_root, cfg).parent
                    if _write_if_absent(agent_dir / ".gitignore", AGENTIC_GITIGNORE):
                        created.append(f"{agent_dir.name}/.gitignore")
        return {"created": created, "skipped": skipped, "replaced": replaced,
                "diverged": diverged, "rules": {"appended": [], "already_present": []}}

    # --- Агентское окружение (в корне проекта) ---

    if opt_skills:
        # Режим волта здесь задаёт пользователь чекбоксом, а не текущее состояние проекта
        c, r, s, d = refresh_agentic(project_root, "skills", vault=opt_vault, cfg=cfg,
                                     overwrite=overwrite)
        created += c
        replaced += r
        skipped += s
        diverged += d
        # .gitignore кладём в ту агентскую папку, куда легли скиллы
        agent_dir = _deployed_skills(project_root, cfg).parent
        if _write_if_absent(agent_dir / ".gitignore", AGENTIC_GITIGNORE):
            created.append(f"{agent_dir.name}/.gitignore")
        else:
            skipped.append(f"{agent_dir.name}/.gitignore")

    # Волт — часть поставки, а не только режим текстов: без структуры скиллы
    # ссылались бы на папку, которой никто не создаёт
    if opt_vault:
        c, r, s, d = deploy_vault(project_root, overwrite=overwrite)
        created += c
        replaced += r
        skipped += s
        diverged += d

    if opt_commands:
        c, r, s, d = refresh_agentic(project_root, "commands", vault=opt_vault,
                                     overwrite=overwrite)
        created += c
        replaced += r
        skipped += s
        diverged += d
        outcome = _ensure_commands_ignored(project_root)
        if outcome == "created":
            created.append(".opencode/.gitignore")
        elif outcome == "appended":
            replaced.append(".opencode/.gitignore (дописано: commands/)")
        else:
            skipped.append(".opencode/.gitignore")

    # Состав файлов правил задаёт выбор сред; отдельные ключи (rules_agents /
    # rules_claude, легаси-«rules» на оба) оставлены как явное «этот не надо»
    legacy_rules = options.get("rules", True)
    rules_appended: list[str] = []
    rules_present: list[str] = []
    rules_names = [n for n in rules_files(project_root, cfg)
                   if options.get(_RULES_OPTION[n], legacy_rules)]
    if rules_names:
        rules_appended, rules_present = _append_rules(project_root, rules_names, cfg)
        # Уже развёрнутую секцию не переписываем: пользователь мог дополнить её
        # под свой процесс. Разошедшуюся показываем в diverged — обновляется
        # кнопкой рядом с diff, как скиллы
        diverged += [name for name, _path, expected in _rules_targets(project_root, cfg)
                     if name in rules_present
                     and not _same_content(_current_text("rules", project_root / name),
                                           expected)]

    return {
        "created": created,
        "skipped": skipped,
        "replaced": replaced,
        "diverged": diverged,
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


# Записи .gitignore, которые уже покрывают папку commands
_COMMANDS_COVERING = {"*", "**", "commands", "commands/", "/commands", "/commands/"}


def _ensure_commands_ignored(project_root: Path) -> str:
    """Гарантировать, что .opencode/.gitignore игнорирует папку commands.

    Вернуть "created" | "appended" | "present". Существующий файл может быть
    пользовательским, поэтому не перезаписываем его шаблоном, а дописываем
    недостающую запись в конец — иначе развёрнутые команды утекают в git.
    """
    path = project_root / ".opencode" / ".gitignore"
    if _write_if_absent(path, AGENTIC_GITIGNORE):
        return "created"
    text = path.read_text(encoding="utf-8")
    entries = {line.strip() for line in text.splitlines()}
    if entries & _COMMANDS_COVERING:
        return "present"
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "commands/\n", encoding="utf-8")
    return "appended"


# --- Выбор сред (харнессов) ---

def harness_choice(cfg: dict | None) -> dict | None:
    """Сохранённый выбор сред или None, если его ещё не делали.

    None — не «сред нет», а «не спрашивали»: UI показывает диалог выбора,
    а проверки полноты по средам до ответа молчат.
    """
    raw = (cfg or {}).get("harnesses")
    if not isinstance(raw, dict) or not any(h in raw for h in HARNESSES):
        return None
    return {h: bool(raw.get(h)) for h in HARNESSES}


def detect_harnesses(project_root: Path) -> dict:
    """Чем проект похоже пользуется — предзаполнение диалога выбора.

    Не найдено ничего — проект просто не открывали ни в одной среде;
    предлагаем обе, лишнее пользователь снимет сам.
    """
    found = {
        "claude": (project_root / ".claude").is_dir() or (project_root / "CLAUDE.md").is_file(),
        "opencode": ((project_root / ".opencode").is_dir()
                     or (project_root / "AGENTS.md").is_file()),
    }
    return found if any(found.values()) else {h: True for h in HARNESSES}


def harnesses(project_root: Path, cfg: dict | None = None) -> dict:
    """Действующий набор сред: сохранённый выбор, иначе определённый по проекту."""
    return harness_choice(cfg) or detect_harnesses(project_root)


# --- Актуальность развёрнутого агентского окружения ---

def _deployed_skills(project_root: Path, cfg: dict | None = None) -> Path:
    """Где живут наши скиллы — одна копия на проект.

    opencode читает и `.claude/skills`, поэтому при обеих средах дублировать
    нечего; отдельная папка `.opencode/skills` нужна только проекту без
    Claude Code. Смена выбора не удаляет прежнюю копию (там могут быть правки),
    но в проверке участвует только действующее расположение.
    """
    active = harnesses(project_root, cfg)
    if active["opencode"] and not active["claude"]:
        return project_root / ".opencode" / "skills"
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


def _skill_targets(project_root: Path, vault: bool | None = None,
                   cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для каждого скилла шаблона.

    Перечисляем только скиллы шаблонов: чужие файлы рядом (собственные скиллы
    пользователя) в целевой список не попадают и не трогаются.
    Эталон выбирается по режиму волта: явный (выбор пользователя при
    развёртывании) или, если не задан, определённый по самому проекту.
    """
    if vault is None:
        vault = uses_vault(project_root, cfg)
    skills_dir = _deployed_skills(project_root, cfg)
    out: list[tuple[str, Path, str]] = []
    for skill_dir in sorted(SKILLS_TEMPLATES.iterdir()):
        if not skill_dir.is_dir():
            continue
        # Волт-скиллы без волта не поставляются: иначе проект получает
        # инструмент к хранилищу, которого у него нет
        if skill_dir.name in VAULT_SKILLS and not vault:
            continue
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        text = raw if vault else _strip_vault_blocks(raw)
        out.append((skill_dir.name, skills_dir / skill_dir.name / "SKILL.md", text))
    return out


def _command_targets(project_root: Path, vault: bool | None = None,
                     cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для команд opencode.

    Обёртка волт-скилла едет только вместе с волтом — как и сам скилл.
    """
    if vault is None:
        vault = uses_vault(project_root, cfg)
    return [
        (f.stem, _deployed_commands(project_root) / f.name, f.read_text(encoding="utf-8"))
        for f in sorted(COMMANDS_TEMPLATES.glob("*.md"))
        if vault or f.stem not in VAULT_SKILLS
    ]


# --- Knowledge Vault ---

def _vault_dir(project_root: Path) -> Path:
    return project_root / VAULT_DIR


def _vault_targets(project_root: Path) -> list[tuple[str, Path, str]]:
    """(имя, путь, эталон) для поставляемой части волта.

    Только правила ведения и шаблоны заметок: README и таксономию наполняет
    пользователь, а его заметки — вообще не наша поставка.
    """
    vault = _vault_dir(project_root)
    return [(rel, vault / rel, (VAULT_TEMPLATES / rel).read_text(encoding="utf-8"))
            for rel in VAULT_TEMPLATE_FILES]


def deploy_vault(project_root: Path, overwrite: bool = False,
                 names: list[str] | None = None
                 ) -> tuple[list[str], list[str], list[str], list[str]]:
    """Развернуть структуру `vault/`: правила, шаблоны заметок, каркас таксономии.

    Файлы пользователя (README, таксономия, сами заметки) не трогаем: они
    создаются один раз, дальше это его содержимое. names — обновить только
    перечисленные элементы (кнопка рядом с diff).
    Возвращает (created, replaced, skipped, diverged).
    """
    vault = _vault_dir(project_root)
    created: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    diverged: list[str] = []
    wanted = set(names) if names is not None else None

    for rel, path, expected in _vault_targets(project_root):
        if wanted is not None and rel not in wanted:
            continue
        name = f"{VAULT_DIR}/{rel}"
        current = _read(path)
        if _same_content(current, expected):
            skipped.append(name)
            continue
        if current is not None and not overwrite:
            diverged.append(name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        (created if current is None else replaced).append(name)

    # Точечное обновление — только названные файлы: остального пользователь
    # в этот момент не просил
    if wanted is not None:
        return created, replaced, skipped, diverged

    # Имя проекта и дата подставляются один раз при создании: дальше это текст
    # пользователя, и переписывать его нам нечем
    for rel in VAULT_DATA_FILES:
        path = vault / rel
        name = f"{VAULT_DIR}/{rel}"
        text = (VAULT_TEMPLATES / rel).read_text(encoding="utf-8").format(
            project=project_root.name, date=date.today().isoformat())
        if _write_if_absent(path, text):
            created.append(name)
        else:
            skipped.append(name)

    # Волт — локальная память разработчика, а не часть чужого репозитория
    if _write_if_absent(vault / ".gitignore", VAULT_GITIGNORE):
        created.append(f"{VAULT_DIR}/.gitignore")
    else:
        skipped.append(f"{VAULT_DIR}/.gitignore")
    return created, replaced, skipped, diverged


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
    Обе возможные папки скиллов: наблюдаем за тем, что реально лежит на диске,
    независимо от текущего выбора сред.
    Волт — только служебная часть (`vault/SYS`): она сверяется с поставкой и
    рождает баннеры, а заметки в доменных папках — данные пользователя.
    """
    candidates = (project_root / ".claude" / "skills",
                  project_root / ".opencode" / "skills",
                  _deployed_commands(project_root),
                  *(project_root / VAULT_DIR / name for name in VAULT_SYSTEM_DIRS))
    return [p for p in candidates if p.is_dir()]


def uses_vault(project_root: Path, cfg: dict | None = None) -> bool:
    """Нужны ли в скиллах блоки волта знаний.

    Приоритет у выбора пользователя из конфига проекта: галка должна что-то
    менять, а перезаписывать файлы молча мы больше не имеем права — со
    сменой эталона расхождение просто становится видно в баннере.
    Ключа нет (проект развёрнут до его появления) — определяем по самим
    файлам: при vault=False блоки вырезаны вместе с маркерами.
    """
    if isinstance(cfg, dict) and "vault" in cfg:
        return bool(cfg["vault"])
    for skill in _deployed_skills(project_root, cfg).glob("*/SKILL.md"):
        if VAULT_START in (_read(skill) or ""):
            return True
    return False


RULES_FILES = ("AGENTS.md", "CLAUDE.md")


def rules_files(project_root: Path, cfg: dict | None = None) -> list[str]:
    """Файлы правил, нужные проекту: по одному на выбранную среду.

    Раньше состав угадывался по тому, какие файлы уже лежат в корне, — из-за
    чего проект без единого агентского файла получал оба. Теперь его задаёт
    выбор сред: каждая среда читает свой файл.
    """
    active = harnesses(project_root, cfg)
    return [HARNESS_RULES_FILE[h] for h in HARNESSES if active[h]]


def rules_deployed(project_root: Path, cfg: dict | None = None) -> list[str]:
    """Агентские файлы, в которых секция правил уже развёрнута."""
    out: list[str] = []
    for name in rules_files(project_root, cfg):
        target = project_root / name
        if target.is_file() and _rules_bounds(target.read_text(encoding="utf-8-sig")):
            out.append(name)
    return out


def rules_missing(project_root: Path, cfg: dict | None = None) -> list[str]:
    """Агентские файлы, которым не хватает секции правил.

    Файл без секции — полурабочее состояние: агент, который читает именно его,
    не знает процесса. Файла нет совсем — заведём при развёртывании.
    """
    deployed = rules_deployed(project_root, cfg)
    return [n for n in rules_files(project_root, cfg) if n not in deployed]


def _rules_targets(project_root: Path, cfg: dict) -> list[tuple[str, Path, str]]:
    """(имя файла, путь, эталонный текст секции) для развёрнутых правил.

    Эталон считается под пайплайн проекта и с номером заголовка, который
    секция занимает в этом файле, — иначе перенумерация выглядела бы
    расхождением.
    """
    out: list[tuple[str, Path, str]] = []
    for name in rules_deployed(project_root, cfg):
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

    Часть, которую в проекте ещё не разворачивали, здесь не проверяется — про
    её отсутствие целиком сообщает environment_issues; это окно показывает
    расхождения внутри уже развёрнутого.
    """
    active = harnesses(project_root, cfg)
    parts: list[tuple[str, list[tuple[str, Path, str]]]] = []
    if any(_deployed_skills(project_root, cfg).glob("*/SKILL.md")):
        parts.append(("skills", _skill_targets(project_root, cfg=cfg)))
    if active["opencode"] and any(_deployed_commands(project_root).glob("*.md")):
        parts.append(("commands", _command_targets(project_root, cfg=cfg)))
    if uses_vault(project_root, cfg) and _vault_dir(project_root).is_dir():
        parts.append(("vault", _vault_targets(project_root)))
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


def agentic_diff(project_root: Path, part: str, name: str, cfg: dict | None = None) -> dict:
    """Unified diff «развёрнутое → эталон» для скилла, команды, файла волта или секции правил.

    Направление выбрано так, чтобы «+» читалось как «появится после обновления».
    Эталон берётся с учётом волт-режима проекта (иначе у проектов без волта
    вырезанные блоки выглядели бы расхождением) и пайплайна — для правил.
    """
    if part == "skills":
        targets = _skill_targets(project_root, cfg=cfg)
    elif part == "commands":
        # cfg обязателен и здесь: без него режим волта определяется по файлам,
        # и diff начинает спорить со списком расхождений, который считан по конфигу
        targets = _command_targets(project_root, cfg=cfg)
    elif part == "vault":
        targets = _vault_targets(project_root)
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
                    names: list[str] | None = None, cfg: dict | None = None,
                    overwrite: bool = True) -> tuple[list[str], list[str], list[str], list[str]]:
    """Развернуть/обновить скиллы, команды или секцию правил до эталона.

    vault=None — сохранить режим волта, уже сложившийся в проекте (точечное
    обновление из UI). names — обновить только перечисленные элементы.
    overwrite=False — недостающее создать, а расходящееся не трогать: в
    правленном скилле могут быть инструкции пользователя, и одна кнопка не
    должна их стирать. Такие файлы возвращаются в diverged.
    Возвращает (created, replaced, skipped, diverged) — относительные пути.
    """
    if part == "rules":
        # Правила живут секцией внутри пользовательского файла: недостающую
        # дописываем, устаревшую пересобираем только по явной команде.
        # Состав файлов задаёт выбор сред (CLAUDE.md / AGENTS.md)
        wanted = list(names) if names else sorted(
            set(rules_deployed(project_root, cfg)) | set(rules_missing(project_root, cfg)))
        appended, _present = _append_rules(project_root, wanted, cfg or {})
        if not overwrite:
            stale = [name for name, _path, expected in _rules_targets(project_root, cfg or {})
                     if name in wanted and name not in appended
                     and not _same_content(_current_text("rules", project_root / name), expected)]
            return appended, [], [], stale
        updated = sync_rules(project_root, cfg or {}, wanted)
        return appended, [n for n in updated if n not in appended], [], []

    if part == "vault":
        created, replaced, skipped, diverged = deploy_vault(
            project_root, overwrite=overwrite, names=names)
        return created, replaced, skipped, diverged

    targets = (_skill_targets(project_root, vault, cfg) if part == "skills"
               else _command_targets(project_root, vault, cfg))
    if names is not None:
        wanted = set(names)
        targets = [t for t in targets if t[0] in wanted]

    created: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    diverged: list[str] = []
    for _name, path, expected in targets:
        # Путь скиллов зависит от выбранных сред, поэтому берём его из цели,
        # а не из константы
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        current = _read(path)
        if _same_content(current, expected):
            skipped.append(rel)  # различия только в переводах строк — не трогаем
            continue
        if current is not None and not overwrite:
            diverged.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        (created if current is None else replaced).append(rel)
    return created, replaced, skipped, diverged


# --- Полнота поставки: единый реестр частей окружения ---

# Из чего состоит поставка. Одна таблица отвечает и за развёртывание, и за
# проверку: часть не может «поставляться, но молча не проверяться». Раньше
# проверялось только уже развёрнутое, и отсутствие целой части принималось за
# выбор пользователя — отсюда молчание про несуществующие скиллы и обёртки.
#   part     — ключ точечного развёртывания (scaffold parts) и кнопки в UI
#   harness  — от какой среды зависит: None — часть проекта, общая для всех;
#              "any" — нужна, если выбрана хоть одна среда
#   missing / outdated — коды деградаций валидатора (None — устаревания нет)
ENV_PARTS = (
    {"part": "create_script", "harness": None,
     "missing": "no_create_script", "outdated": "outdated_script"},
    {"part": "status_script", "harness": None,
     "missing": "no_status_script", "outdated": "outdated_status_script"},
    {"part": "template", "harness": None,
     "missing": "no_template", "outdated": "outdated_template"},
    {"part": "epics", "harness": None, "missing": "no_epics", "outdated": None},
    {"part": "logs", "harness": None, "missing": "no_logs", "outdated": None},
    {"part": "skills", "harness": "any",
     "missing": "no_skills", "outdated": "outdated_skills"},
    {"part": "commands", "harness": "opencode",
     "missing": "no_commands", "outdated": "outdated_commands"},
    {"part": "rules", "harness": "any",
     "missing": "no_rules", "outdated": "outdated_rules"},
    # Волт проверяется только у тех, кто его выбрал: отказ от внешней памяти —
    # решение пользователя, а не пробел поставки
    {"part": "vault", "harness": None, "vault_only": True,
     "missing": "no_vault", "outdated": "outdated_vault"},
)


def _script_state(tasks_dir: Path, cfg: dict, cfg_key: str,
                  template_name: str) -> tuple[list[str], list[str]]:
    """(нет, устарел) для скрипта-инструмента в tasks/."""
    name = cfg.get(cfg_key, template_name)
    path = tasks_dir / name
    if not path.is_file():
        return [name], []
    template = (TASKS_TEMPLATES / template_name).read_text(encoding="utf-8")
    return [], ([] if _read(path) == template else [name])


def _targets_state(part: str,
                   targets: list[tuple[str, Path, str]]) -> tuple[list[str], list[str], list[str]]:
    """(нет части целиком, недостающие элементы, разошедшиеся) для многофайловой части.

    Ни одного файла на диске — часть не разворачивали; хотя бы один есть —
    остальные разбираем поэлементно. Отсутствующий файл и отставший от шаблона
    — разные вещи: называть несуществующий скилл «устаревшим» значит врать
    в баннере (пользователь ищет, что же там устарело, а файла просто нет).
    """
    current = [(name, _current_text(part, path), expected) for name, path, expected in targets]
    if targets and all(text is None for _n, text, _e in current):
        return [name for name, _p, _e in targets], [], []
    absent = [name for name, text, _e in current if text is None]
    modified = [name for name, text, expected in current
                if text is not None and not _same_content(text, expected)]
    return [], absent, modified


def environment_issues(tasks_dir: Path, cfg: dict) -> list[dict]:
    """Пробелы поставки: [{part, code, state, names}] — запись на вид проблемы.

    state: "missing" — части нет вовсе; "outdated" — часть развёрнута, но
    отдельные элементы разошлись с шаблоном или отсутствуют.
    Пустой список — окружение полно и актуально.
    Части невыбранной среды не проверяются: отказ от opencode — это решение
    пользователя, а не пробел поставки.
    """
    project_root = tasks_dir.parent
    active = harnesses(project_root, cfg)
    decided = harness_choice(cfg) is not None

    vault_on = uses_vault(project_root, cfg)

    issues: list[dict] = []
    for spec in ENV_PARTS:
        part, harness = spec["part"], spec["harness"]
        if spec.get("vault_only") and not vault_on:
            continue
        # Пока среды не выбраны, их части не проверяем: спросим в диалоге, а не
        # будем угадывать по диску и ругаться на то, чего пользователь не хотел
        if harness is not None and not decided:
            continue
        if harness == "any" and not any(active.values()):
            continue
        if harness in HARNESSES and not active[harness]:
            continue

        # partial — часть развёрнута, но отдельных элементов не хватает
        # (появился новый скилл, включили волт): чинится тем же «Развернуть»
        partial: list[str] = []
        if part == "create_script":
            missing, outdated = _script_state(tasks_dir, cfg, "create_script", "create_task.py")
        elif part == "status_script":
            missing, outdated = _script_state(tasks_dir, cfg, "status_script", "set_status.py")
        elif part == "template":
            path = tasks_dir / TASK_TEMPLATE_FILE
            template = (TASKS_TEMPLATES / TASK_TEMPLATE_FILE).read_text(encoding="utf-8")
            missing = [] if path.is_file() else [TASK_TEMPLATE_FILE]
            outdated = ([] if not path.is_file() or _same_content(_read(path), template)
                        else [TASK_TEMPLATE_FILE])
        elif part == "epics":
            missing = [] if (tasks_dir / EPICS_FILE).is_file() else [EPICS_FILE]
            outdated = []  # реестр эпиков — данные пользователя, эталона нет
        elif part == "logs":
            name = cfg.get("logs_dir", "logs")
            missing = [] if (tasks_dir / name).is_dir() else [f"{name}/"]
            outdated = []
        elif part == "skills":
            missing, partial, outdated = _targets_state(
                part, _skill_targets(project_root, cfg=cfg))
        elif part == "commands":
            missing, partial, outdated = _targets_state(
                part, _command_targets(project_root, cfg=cfg))
        elif part == "vault":
            missing, partial, outdated = _targets_state(part, _vault_targets(project_root))
        else:  # rules
            missing = rules_missing(project_root, cfg)
            _m, _absent, outdated = _targets_state(part, _rules_targets(project_root, cfg))

        for state, names in (("missing", missing), ("partial", partial), ("outdated", outdated)):
            # Недостающие элементы разворачиваются той же кнопкой, что и
            # отсутствующая часть, поэтому код у них общий — различается текст
            code = spec["missing"] if state == "partial" else spec[state]
            if names and code:
                issues.append({"part": part, "code": code, "state": state, "names": names})
    return issues
