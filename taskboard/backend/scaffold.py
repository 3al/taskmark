"""Развёртывание структуры tasks/ и агентского окружения (scaffold из UI)."""

from __future__ import annotations

import difflib
import re
import shutil
from datetime import date
from pathlib import Path, PurePosixPath

from backend import baseline, template_history
from backend.config import TASK_TYPES
from backend.epics import EPICS_FILE
from backend.statuses import load_pipeline

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TASKS_TEMPLATES = TEMPLATES_DIR / "tasks"
AGENTIC_TEMPLATES = TEMPLATES_DIR / "agentic"
SKILLS_TEMPLATES = AGENTIC_TEMPLATES / ".claude" / "skills"
COMMANDS_TEMPLATES = AGENTIC_TEMPLATES / ".opencode" / "commands"
VAULT_TEMPLATES = TEMPLATES_DIR / "vault"

# Опциональные блоки шаблонов: возможность проекта → что исчезает из поставки,
# когда она выключена. Реестр, а не пара констант: заказчик у механизма не один
# (волт, дальше — внешние источники ревью), и вторая зашитая пара развела бы
# вырезание по копиям. Выключенная возможность агенту не видна вовсе: блок
# `<!-- marker -->…<!-- /marker -->` уходит вместе с маркерами, а её скиллы не
# разворачиваются — иначе это инструмент к тому, чего в проекте нет, на который
# к тому же никто не ссылается (блоки соседних скиллов вырезаны).
#   key    — ключ возможности в конфиге проекта
#   marker — имя маркера в шаблонах скиллов и правил
#   skills — скиллы, поставляемые только вместе с возможностью
OPTIONAL_BLOCKS = (
    {"key": "vault", "marker": "vault", "skills": ("write-vault",)},
    # Внешние источники ревью: свой скилл им не нужен — это шаги внутри
    # review-task, которых у выключившего возможность просто нет
    {"key": "review_sources", "marker": "review_sources", "skills": ()},
)

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

# Рубрики внутри раздела создания задач: по ним create_task.py раскладывает
# новое. Выводятся из каталога типов — рубрика и тип это одно и то же понятие,
# и вторым списком оно уже разъезжалось (TASK-119)
BACKLOG_SUBSECTIONS = tuple(meta["section"] for meta in TASK_TYPES.values())

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


def _block_markers(marker: str) -> tuple[str, str]:
    """Открывающий и закрывающий маркер опционального блока в шаблоне."""
    return f"<!-- {marker} -->", f"<!-- /{marker} -->"


def feature_skills(key: str) -> tuple[str, ...]:
    """Скиллы, поставляемые только вместе с возможностью (пусто — таких нет)."""
    for spec in OPTIONAL_BLOCKS:
        if spec["key"] == key:
            return spec["skills"]
    return ()


def _skipped_skills(features: set[str]) -> set[str]:
    """Скиллы всех выключенных возможностей — их в проект не разворачиваем."""
    return {name for spec in OPTIONAL_BLOCKS if spec["key"] not in features
            for name in spec["skills"]}


def _enabled_features(values: dict | None) -> set[str]:
    """Возможности, включённые в переданном наборе.

    Набор — конфиг проекта или опции развёртывания: ключи в них одни и те же,
    а спрашивают у них одно и то же — какие блоки шаблонов остаются.
    """
    values = values if isinstance(values, dict) else {}
    return {spec["key"] for spec in OPTIONAL_BLOCKS if values.get(spec["key"])}


def strip_optional_blocks(text: str, features) -> str:
    """Вырезать блоки выключенных возможностей вместе с маркерами.

    features — включённые возможности; их блоки остаются как есть, вместе с
    маркерами: по ним же режим проекта опознаётся по файлам.
    Если внутри вырезанного блока был заголовок «## Шаг N» — перенумеровать
    последующие шаги и ссылки на них («шаг 6-7» → «шаг 5-6»). Перенумерация
    одна на проход, сколько бы блоков ни сняли.
    """
    enabled = set(features)
    cut = {spec["marker"] for spec in OPTIONAL_BLOCKS if spec["key"] not in enabled}
    if not cut:
        return text
    starts = {_block_markers(m)[0] for m in cut}
    ends = {_block_markers(m)[1] for m in cut}

    out: list[str] = []
    skip = False
    removed_steps: list[int] = []
    for line in text.splitlines():
        marker = line.strip()
        if not skip and marker in starts:
            skip = True
            continue
        if skip and marker in ends:
            skip = False
            continue
        if skip:
            m = re.match(r"^##\s+Шаг\s+(\d+)", line.strip())
            if m:
                removed_steps.append(int(m.group(1)))
            continue
        out.append(line)

    result = _renumber_steps("\n".join(out), removed_steps)

    # Схлопнуть серии пустых строк, оставшиеся после вырезки
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n"


def _renumber_steps(text: str, removed: list[int]) -> str:
    """Сдвинуть номера шагов и ссылки на них на число вырезанных шагов перед ними.

    Считается за один проход по всем вырезанным блокам: сдвиг «на -1» на
    каждый блок по очереди применялся к уже сдвинутым номерам, и при двух
    вырезанных шагах нумерация оставалась с дырой.
    """
    if not removed:
        return text

    def shift(num: int) -> str:
        return str(num - sum(1 for r in removed if r < num))

    def replace(m: re.Match) -> str:
        head = m.group(1) + shift(int(m.group(2)))
        if m.group(3):
            head += "-" + shift(int(m.group(3)))
        return head

    return re.sub(r"(шаг\w*\s+)(\d+)(?:-(\d+))?", replace, text, flags=re.IGNORECASE)


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

    # Про выключенную возможность в правилах не пишем: иначе агент читает
    # инструкцию к тому, чего в проекте нет
    template = (AGENTIC_TEMPLATES / "rules_section.md").read_text(encoding="utf-8")
    template = strip_optional_blocks(template, _enabled_features(cfg))

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
            baseline.backup(project_root, "rules", name, content[start:end], cfg)
            target.write_text(fresh, encoding="utf-8")
            updated.append(name)
        _remember(project_root, "rules", name, _current_text("rules", target) or "", cfg)
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
        _remember(project_root, "rules", name, _current_text("rules", target) or "", cfg)
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
    # Какие опциональные блоки остаются в текстах — решает пользователь здесь и
    # сейчас (чекбоксы развёртывания), а не состояние проекта на диске
    opt_features = _enabled_features(options)
    opt_vault = "vault" in opt_features
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
    project_root = tasks_dir.parent
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
        current = _read(script_path)
        if current is None:
            script_path.write_text(template_text, encoding="utf-8")
            _remember(project_root, part, script_name, template_text, cfg)
            created.append(script_name)
        elif _same_content(current, template_text):
            skipped.append(script_name)
            if baseline.read(project_root, part, script_name, cfg) is None:
                _remember(project_root, part, script_name, template_text, cfg)
        elif overwrite:
            baseline.backup(project_root, part, script_name, current, cfg)
            script_path.write_text(template_text, encoding="utf-8")
            _remember(project_root, part, script_name, template_text, cfg)
            replaced.append(script_name)
        else:
            diverged.append(script_name)

    # Шаблон задачи — инструмент, а не данные: правится редко, но обновляется
    # до эталона по кнопке, как скрипты
    if "template" in want:
        template_path = tasks_dir / TASK_TEMPLATE_FILE
        template_text = (TASKS_TEMPLATES / TASK_TEMPLATE_FILE).read_text(encoding="utf-8")
        current = _read(template_path)
        if current is None:
            template_path.write_text(template_text, encoding="utf-8")
            _remember(project_root, "template", TASK_TEMPLATE_FILE, template_text, cfg)
            created.append(TASK_TEMPLATE_FILE)
        elif _same_content(current, template_text):
            skipped.append(TASK_TEMPLATE_FILE)
            if baseline.read(project_root, "template", TASK_TEMPLATE_FILE, cfg) is None:
                _remember(project_root, "template", TASK_TEMPLATE_FILE, template_text, cfg)
        elif overwrite:
            baseline.backup(project_root, "template", TASK_TEMPLATE_FILE, current, cfg)
            template_path.write_text(template_text, encoding="utf-8")
            _remember(project_root, "template", TASK_TEMPLATE_FILE, template_text, cfg)
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

    # Точечное восстановление: только запрошенные части
    if parts:
        names = options.get("names")
        if "vault" in want:
            c, r, s, d = deploy_vault(project_root, overwrite=True, names=names, cfg=cfg)
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
                    c, r, s, d = refresh_agentic(
                        project_root, agentic, cfg=cfg, overwrite=False,
                        features=project_features(project_root, cfg) | {"vault"},
                        names=list(feature_skills("vault")))
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
            # не быть: без .gitignore её содержимое утечёт в git проекта.
            # Проверяем независимо от того, создали ли что-то сейчас: часть
            # может быть давно на месте, а игнор её так и не покрывать
            if part in ("skills", "commands"):
                target = (_deployed_skills(project_root, cfg) if part == "skills"
                          else _deployed_commands(project_root))
                outcome = _ensure_ignored(target.parent, target.name)
                if outcome == "created":
                    created.append(f"{target.parent.name}/.gitignore")
                elif outcome == "appended":
                    replaced.append(
                        f"{target.parent.name}/.gitignore (дописано: {target.name}/)")
        return {"created": created, "skipped": skipped, "replaced": replaced,
                "diverged": diverged, "rules": {"appended": [], "already_present": []}}

    # --- Агентское окружение (в корне проекта) ---

    if opt_skills:
        # Состав блоков здесь задаёт пользователь чекбоксами, а не текущее
        # состояние проекта
        c, r, s, d = refresh_agentic(project_root, "skills", features=opt_features,
                                     cfg=cfg, overwrite=overwrite)
        created += c
        replaced += r
        skipped += s
        diverged += d
        # .gitignore кладём в ту агентскую папку, куда легли скиллы
        skills_dir = _deployed_skills(project_root, cfg)
        outcome = _ensure_ignored(skills_dir.parent, skills_dir.name)
        if outcome == "created":
            created.append(f"{skills_dir.parent.name}/.gitignore")
        elif outcome == "appended":
            replaced.append(
                f"{skills_dir.parent.name}/.gitignore (дописано: {skills_dir.name}/)")
        else:
            skipped.append(f"{skills_dir.parent.name}/.gitignore")

    # Волт — часть поставки, а не только режим текстов: без структуры скиллы
    # ссылались бы на папку, которой никто не создаёт
    if opt_vault:
        c, r, s, d = deploy_vault(project_root, overwrite=overwrite, cfg=cfg)
        created += c
        replaced += r
        skipped += s
        diverged += d

    if opt_commands:
        c, r, s, d = refresh_agentic(project_root, "commands", features=opt_features,
                                     overwrite=overwrite)
        created += c
        replaced += r
        skipped += s
        diverged += d
        commands_dir = _deployed_commands(project_root)
        outcome = _ensure_ignored(commands_dir.parent, commands_dir.name)
        if outcome == "created":
            created.append(f"{commands_dir.parent.name}/.gitignore")
        elif outcome == "appended":
            replaced.append(
                f"{commands_dir.parent.name}/.gitignore (дописано: {commands_dir.name}/)")
        else:
            skipped.append(f"{commands_dir.parent.name}/.gitignore")

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


# Записи .gitignore, покрывающие в папке вообще всё
_ANY_COVERING = ("*", "**", "*/", "**/")


def _ignore_covers(text: str, folder: str) -> bool:
    """Прячет ли этот .gitignore папку folder.

    Разбор нарочно грубый — по точным записям: полноценные правила git
    (вложенность, порядок, отрицания) нам не нужны, а ошибиться в сторону
    «уже покрыто» нельзя — тогда папка утечёт в git. Отрицание `!folder/`
    покрытием не считается: это ровно противоположное указание.
    """
    covering = set(_ANY_COVERING)
    for name in (folder, f"/{folder}"):
        covering |= {name, f"{name}/", f"{name}/*", f"{name}/**"}
    return any(line.strip() in covering for line in text.splitlines())


def _ensure_ignored(agent_dir: Path, folder: str) -> str:
    """Гарантировать, что <agent_dir>/.gitignore прячет папку folder.

    Вернуть "created" | "appended" | "present". Существующий файл может быть
    пользовательским (`.claude/.gitignore` со своими записями — обычное дело),
    поэтому не перезаписываем его шаблоном, а дописываем недостающую запись
    в конец — иначе развёрнутое окружение утекает в git проекта.
    """
    path = agent_dir / ".gitignore"
    if _write_if_absent(path, AGENTIC_GITIGNORE):
        return "created"
    text = path.read_text(encoding="utf-8")
    if _ignore_covers(text, folder):
        return "present"
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + f"{folder}/\n", encoding="utf-8")
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
    Живёт оно в `baseline`: там же сравниваются слепок с эталоном, и двух
    разных «одинаково» у нас быть не должно.
    """
    return baseline.same_text(current, expected)


def _remember(project_root: Path, part: str, name: str, text: str,
              cfg: dict | None = None) -> None:
    """Записать слепок развёрнутого элемента — то, из чего проект развернули.

    Вызывается сразу после записи файла: без слепка следующее расхождение
    снова станет безымянным («отличается от шаблона»), а слить правки будет
    не с чем.
    """
    baseline.write(project_root, part, name, text, cfg)


def _skill_targets(project_root: Path, features: set[str] | None = None,
                   cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для каждого скилла шаблона.

    Перечисляем только скиллы шаблонов: чужие файлы рядом (собственные скиллы
    пользователя) в целевой список не попадают и не трогаются.
    Эталон выбирается по набору возможностей: явному (выбор пользователя при
    развёртывании) или, если не задан, определённому по самому проекту.
    """
    if features is None:
        features = project_features(project_root, cfg)
    skipped = _skipped_skills(features)
    skills_dir = _deployed_skills(project_root, cfg)
    out: list[tuple[str, Path, str]] = []
    for skill_dir in sorted(SKILLS_TEMPLATES.iterdir()):
        if not skill_dir.is_dir():
            continue
        # Скилл выключенной возможности не поставляется: иначе проект получает
        # инструмент к тому, чего у него нет
        if skill_dir.name in skipped:
            continue
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        out.append((skill_dir.name, skills_dir / skill_dir.name / "SKILL.md",
                    strip_optional_blocks(raw, features)))
    return out


def _command_targets(project_root: Path, features: set[str] | None = None,
                     cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь развёрнутого файла, эталонный текст) для команд opencode.

    Обёртка едет только вместе со своим скиллом — то есть с возможностью,
    которой он принадлежит.
    """
    if features is None:
        features = project_features(project_root, cfg)
    skipped = _skipped_skills(features)
    return [
        (f.stem, _deployed_commands(project_root) / f.name, f.read_text(encoding="utf-8"))
        for f in sorted(COMMANDS_TEMPLATES.glob("*.md"))
        if f.stem not in skipped
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
                 names: list[str] | None = None, cfg: dict | None = None
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
            if baseline.read(project_root, "vault", rel, cfg) is None:
                _remember(project_root, "vault", rel, expected, cfg)
            continue
        if current is not None and not overwrite:
            diverged.append(name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if current is not None:
            baseline.backup(project_root, "vault", rel, current, cfg)
        path.write_text(expected, encoding="utf-8")
        _remember(project_root, "vault", rel, expected, cfg)
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


def project_features(project_root: Path, cfg: dict | None = None) -> set[str]:
    """Возможности проекта, от которых зависит состав текстов поставки.

    Приоритет у выбора пользователя из конфига проекта: галка должна что-то
    менять, а перезаписывать файлы молча мы больше не имеем права — со
    сменой эталона расхождение просто становится видно в баннере.
    Ключа нет (проект развёрнут до его появления) — определяем по самим
    файлам: у выключенной возможности блоки вырезаны вместе с маркерами.
    """
    values = cfg if isinstance(cfg, dict) else {}
    out: set[str] = set()
    for spec in OPTIONAL_BLOCKS:
        enabled = (bool(values[spec["key"]]) if spec["key"] in values
                   else _marker_deployed(project_root, spec["marker"], cfg))
        if enabled:
            out.add(spec["key"])
    return out


def _marker_deployed(project_root: Path, marker: str, cfg: dict | None) -> bool:
    """Остались ли маркеры возможности в развёрнутых скиллах проекта."""
    start = _block_markers(marker)[0]
    return any(start in (_read(skill) or "")
               for skill in _deployed_skills(project_root, cfg).glob("*/SKILL.md"))


def uses_vault(project_root: Path, cfg: dict | None = None) -> bool:
    """Нужны ли в скиллах блоки волта знаний — частный случай project_features.

    Волт отличается от прочих опциональных блоков тем, что он ещё и часть
    поставки (папка `vault/`), поэтому спрашивают о нём отдельно и по имени.
    """
    return "vault" in project_features(project_root, cfg)


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


# Части поставки из одного файла в tasks/: сравниваются и разрешаются так же,
# как многофайловые, поэтому в окно расхождений попадают наравне с ними.
# Имя элемента переименуемо настройкой, имя шаблона — нет
SINGLE_FILE_PARTS = ("create_script", "status_script", "template")


def _single_targets(project_root: Path, part: str,
                    cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь, эталон) для одиночного файла поставки в tasks/."""
    cfg = cfg or {}
    if part == "template":
        name = template_name = TASK_TEMPLATE_FILE
    else:
        cfg_key, template_name = next((k, t) for p, k, t in TOOL_SCRIPTS if p == part)
        name = cfg.get(cfg_key, template_name)
    tasks_dir = project_root / cfg.get("tasks_dir", "tasks")
    return [(name, tasks_dir / name,
             (TASKS_TEMPLATES / template_name).read_text(encoding="utf-8"))]


def part_targets(project_root: Path, part: str,
                 cfg: dict | None = None) -> list[tuple[str, Path, str]]:
    """(имя, путь, эталон) для элементов части — единая точка для всех частей.

    cfg обязателен везде, где эталон зависит от настроек проекта: без него
    режим волта определяется по файлам на диске, а список расхождений считан
    по конфигу — окно и баннер начинают спорить друг с другом.
    """
    if part == "skills":
        return _skill_targets(project_root, cfg=cfg)
    if part == "commands":
        return _command_targets(project_root, cfg=cfg)
    if part == "vault":
        return _vault_targets(project_root)
    if part == "rules":
        return _rules_targets(project_root, cfg or {})
    if part in SINGLE_FILE_PARTS:
        return _single_targets(project_root, part, cfg)
    return []


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
    for part in SINGLE_FILE_PARTS:
        targets = _single_targets(project_root, part, cfg)
        if targets[0][1].is_file():
            parts.append((part, targets))
    return parts


def _template_source(part: str, name: str, features: set[str]):
    """(файл шаблона, приведение его текста к развёрнутому виду) или None.

    Нужно, чтобы искать предка в истории шаблонов: сравнивать историческую
    версию надо с тем, что в этот проект действительно положили бы, — у
    скиллов это текст с вырезанными блоками выключенных возможностей.

    Правила исключены намеренно: их секция не копируется, а собирается кодом
    под пайплайн проекта, и старый текст шаблона без старого кода к
    развёрнутому виду не привести.
    """
    if part == "skills":
        return SKILLS_TEMPLATES / name / "SKILL.md", lambda t: strip_optional_blocks(t, features)
    if part == "commands":
        return COMMANDS_TEMPLATES / f"{name}.md", None
    if part == "vault":
        return VAULT_TEMPLATES / name, None
    if part in SINGLE_FILE_PARTS:
        template_name = (TASK_TEMPLATE_FILE if part == "template"
                         else next(t for p, _k, t in TOOL_SCRIPTS if p == part))
        return TASKS_TEMPLATES / template_name, None
    return None


def resolved_base(project_root: Path, part: str, name: str, current: str | None,
                  cfg: dict | None = None) -> dict:
    """Основа сравнения и слияния: {text, origin, exact, version, ratio}.

    origin: "store" — слепок, записанный при развёртывании: это факт, из чего
            элемент развернули;
            "history" — ближайшая по содержанию версия шаблона из истории
            инструмента. Это подбор: проект мог быть развёрнут до появления
            слепка, а мог и вовсе не разворачиваться из шаблона — тексты
            бывают старше самого инструмента;
            None — основы нет, слить не с чем.
    """
    stored = baseline.read(project_root, part, name, cfg)
    if stored is not None:
        return {"text": stored, "origin": "store", "exact": True,
                "version": None, "ratio": None, "usable": True}
    source = (None if current is None
              else _template_source(part, name, project_features(project_root, cfg)))
    guess = (template_history.guess_base(source[0], current, source[1])
             if source and source[0].is_file() else None)
    if guess is None:
        return {"text": None, "origin": None, "exact": False,
                "version": None, "ratio": None, "usable": False}
    # Негодная основа всё равно возвращается с процентом: отказ от слияния
    # нужно объяснить, а не изобразить отсутствием кнопки
    return {"text": guess["text"] if guess["usable"] else None, "origin": "history",
            "exact": guess["exact"], "version": guess["version"],
            "ratio": guess["ratio"], "usable": guess["usable"]}


def element_state(project_root: Path, part: str, name: str, path: Path, expected: str,
                  cfg: dict | None = None) -> str:
    """Состояние элемента поставки с учётом предка (baseline.state).

    Восстановленный из истории предок участвует в состоянии **только при
    точном совпадении**: тогда это не догадка, а доказательство, что файл
    после развёртывания не правили. Приблизительная догадка годится как
    основа слияния, но переименовывать ею состояние нельзя — молчаливо
    спрятанный баннер хуже честного «происхождение неизвестно».
    """
    current = _current_text(part, path)
    base = resolved_base(project_root, part, name, current, cfg)
    return baseline.state(current, expected, base["text"] if base["exact"] else None)


def agentic_stale_details(project_root: Path, cfg: dict | None = None) -> list[dict]:
    """Расхождения, о которых стоит сказать: [{part, name, state, path, mergeable}].

    Кастомизированный элемент сюда не попадает: если шаблон с момента
    развёртывания не двигался, файл актуален — правки в нём внёс сам
    пользователь, и напоминать ему об этом нечем. Прежний критерий («файл
    равен шаблону») означал вечный баннер на каждую свою правку.
    """
    items: list[dict] = []
    for part, targets in _deployed_parts(project_root, cfg):
        for name, path, expected in targets:
            state = element_state(project_root, part, name, path, expected, cfg)
            if state in (baseline.SAME, baseline.CUSTOMIZED):
                continue
            base = resolved_base(project_root, part, name,
                                 _current_text(part, path), cfg)
            items.append({
                "part": part,
                "name": name,
                "state": state,
                # Сливать можно только имея общую основу: без неё
                # трёхстороннего merge не существует. Подобранная по истории
                # шаблонов версия для этого годится — состояние она не меняет,
                # но выбор «слить» возвращает
                "mergeable": base["text"] is not None and state in (
                    baseline.CONFLICT, baseline.UNKNOWN),
                "base_origin": base["origin"],
                "base_version": base["version"],
                "base_exact": base["exact"],
                "base_ratio": base["ratio"],
                "base_usable": base["usable"],
                "path": str(path.relative_to(project_root)).replace("\\", "/"),
            })
    return items


def _unified(before: str, after: str, from_label: str, to_label: str) -> str:
    return "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=from_label, tofile=to_label, lineterm=""))


def agentic_diff(project_root: Path, part: str, name: str, cfg: dict | None = None) -> dict:
    """Что развёрнутое и эталон говорят друг о друге — тремя диффами.

    - `diff` — «развёрнутое → эталон»: «+» читается как «появится после
      обновления». Это то, что видно всегда, в том числе без слепка.
    - `template_diff` — «слепок → эталон»: что нового в шаблоне.
    - `local_diff` — «слепок → развёрнутое»: что своего в проекте.

    Последние два — суть расхождения по отдельности: сводный diff смешивает
    их в одну кучу, и по нему нельзя решить, что именно потеряешь. Без слепка
    они пусты: разделить нечем.
    """
    target = next((t for t in part_targets(project_root, part, cfg) if t[0] == name), None)
    if target is None:
        return {"ok": False, "error": f"Неизвестный элемент: {part}/{name}"}

    _name, path, expected = target
    current = _current_text(part, path)
    resolved = resolved_base(project_root, part, name, current, cfg)
    base = resolved["text"]
    base_label = "было развёрнуто" if resolved["origin"] == "store" else "основа сравнения"
    state = element_state(project_root, part, name, path, expected, cfg)

    diff = _unified(current or "", expected, f"{name} — в проекте", f"{name} — шаблон")
    body = [ln for ln in diff.splitlines() if not ln.startswith(("---", "+++"))]

    return {
        "ok": True, "part": part, "name": name, "state": state,
        "diff": diff,
        "added": sum(1 for ln in body if ln.startswith("+")),
        "removed": sum(1 for ln in body if ln.startswith("-")),
        # Подпись основы зависит от того, факт это или подбор: называть
        # угаданную версию «было развёрнуто» значит утверждать то, чего мы
        # не знаем — файл мог никогда из шаблона не разворачиваться
        "template_diff": ("" if base is None else
                          _unified(base, expected, f"{name} — {base_label}",
                                   f"{name} — шаблон")),
        "local_diff": ("" if base is None else
                       _unified(base, current or "", f"{name} — {base_label}",
                                f"{name} — в проекте")),
        "base_origin": resolved["origin"],
        "base_version": resolved["version"],
        "base_exact": resolved["exact"],
        "base_ratio": resolved["ratio"],
        "base_usable": resolved["usable"],
        "mergeable": base is not None and state in (baseline.CONFLICT, baseline.UNKNOWN),
    }


def _write_element(part: str, path: Path, text: str) -> None:
    """Записать элемент: правила — секцией внутри чужого файла, остальное целиком."""
    if part != "rules":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    bounds = _rules_bounds(content)
    if bounds is None:
        path.write_text(content.rstrip("\n") + "\n\n" + text.rstrip("\n") + "\n",
                        encoding="utf-8")
        return
    start, end = bounds
    path.write_text(content[:start] + text.rstrip("\n") + "\n" + content[end:],
                    encoding="utf-8")


def resolve_element(project_root: Path, part: str, name: str, action: str,
                    cfg: dict | None = None) -> dict:
    """Разрешить расхождение элемента одним из трёх исходов.

    - `merge` — слить свои правки с новым шаблоном (`git merge-file`).
      Конфликтующие куски остаются в файле маркерами: выбрать за человека
      нельзя, но и прятать выбор от него незачем.
    - `template` — взять шаблон, прежнее содержимое уходит в бэкап.
    - `keep` — оставить свою версию: файл не трогаем, слепок приравниваем
      шаблону. Элемент перестаёт светиться в баннере до следующей правки
      шаблона — это согласие с текущим, а не вечное молчание.

    Любой исход заканчивается одинаково: слепок = шаблон. Именно он, а не сам
    файл, отвечает на вопрос «отстали ли мы от поставки».
    """
    target = next((t for t in part_targets(project_root, part, cfg) if t[0] == name), None)
    if target is None:
        return {"ok": False, "error": f"Неизвестный элемент: {part}/{name}"}

    _name, path, expected = target
    current = _current_text(part, path)
    conflicts = 0

    if action == "keep":
        if current is None:
            return {"ok": False, "error": f"Файла нет — оставлять нечего: {part}/{name}"}
        _remember(project_root, part, name, expected, cfg)
        return {"ok": True, "part": part, "name": name, "action": action,
                "conflicts": 0, "backup": None,
                "state": element_state(project_root, part, name, path, expected, cfg)}

    if action == "merge":
        resolved = resolved_base(project_root, part, name, current, cfg)
        base = resolved["text"]
        if base is None or current is None:
            # Причину называем числом: «сливать не с чем» без объяснения
            # выглядит произволом — у соседнего элемента кнопка ведь есть
            near = (f" Ближайшая версия шаблона совпадает на "
                    f"{round((resolved['ratio'] or 0) * 100)}%."
                    if resolved["ratio"] is not None else "")
            return {"ok": False,
                    "error": "Нет основы для слияния: сливать не с чем — возьмите "
                             f"шаблон или оставьте свою версию.{near}"}
        merged = baseline.merge(base, current, expected)
        if merged is None:
            return {"ok": False, "error": "Слияние не выполнено: не найден git"}
        text, conflicts = merged
    elif action == "template":
        text = expected
    else:
        return {"ok": False, "error": f"Неизвестное действие: {action}"}

    backup_path = (baseline.backup(project_root, part, name, current, cfg)
                   if current is not None else None)
    _write_element(part, path, text)
    _remember(project_root, part, name, expected, cfg)
    return {"ok": True, "part": part, "name": name, "action": action,
            "conflicts": conflicts, "backup": backup_path,
            "state": element_state(project_root, part, name, path, expected, cfg)}


def refresh_agentic(project_root: Path, part: str, features: set[str] | None = None,
                    names: list[str] | None = None, cfg: dict | None = None,
                    overwrite: bool = True) -> tuple[list[str], list[str], list[str], list[str]]:
    """Развернуть/обновить скиллы, команды или секцию правил до эталона.

    features=None — сохранить набор возможностей, уже сложившийся в проекте
    (точечное обновление из UI). names — обновить только перечисленные элементы.
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
            project_root, overwrite=overwrite, names=names, cfg=cfg)
        return created, replaced, skipped, diverged

    targets = (_skill_targets(project_root, features, cfg) if part == "skills"
               else _command_targets(project_root, features, cfg))
    if names is not None:
        wanted = set(names)
        targets = [t for t in targets if t[0] in wanted]

    created: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    diverged: list[str] = []
    for name, path, expected in targets:
        # Путь скиллов зависит от выбранных сред, поэтому берём его из цели,
        # а не из константы
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        current = _read(path)
        if _same_content(current, expected):
            skipped.append(rel)  # различия только в переводах строк — не трогаем
            # Файл и так шаблонный: заводим слепок, если его не было. Так
            # проект, развёрнутый до появления слепков, лечится сам, а не
            # остаётся с элементами неизвестного происхождения навсегда
            if baseline.read(project_root, part, name, cfg) is None:
                _remember(project_root, part, name, expected, cfg)
            continue
        if current is not None and not overwrite:
            diverged.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if current is not None:
            baseline.backup(project_root, part, name, current, cfg)
        path.write_text(expected, encoding="utf-8")
        _remember(project_root, part, name, expected, cfg)
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


# Состояния, о которых баннер молчит: файл либо шаблонный, либо правленный
# пользователем при неизменившемся шаблоне — в обоих случаях он актуален
_QUIET_STATES = (baseline.SAME, baseline.CUSTOMIZED)


def _script_state(tasks_dir: Path, cfg: dict, part: str) -> tuple[list[str], list[str]]:
    """(нет, устарел) для одиночного файла поставки в tasks/."""
    project_root = tasks_dir.parent
    targets = _single_targets(project_root, part, cfg)
    name, path, expected = targets[0]
    if not path.is_file():
        return [name], []
    state = element_state(project_root, part, name, path, expected, cfg)
    return [], ([] if state in _QUIET_STATES else [name])


# Маркер контракта в скрипте: `SCRIPT_CAPABILITIES = {"stall", ...}`. Читаем
# файл, а не импортируем: копия пользователя могла быть отредактирована до
# неработоспособности, и падать на этом валидатор не должен
_CAPABILITIES_RE = re.compile(r"SCRIPT_CAPABILITIES\s*=\s*\{([^}]*)\}")
_CAPABILITY_RE = re.compile(r"[\"']([\w-]+)[\"']")


def script_capabilities(tasks_dir: Path, cfg: dict) -> set[str]:
    """Что умеет развёрнутая копия set_status.py — по маркеру контракта.

    Пустое множество — копии нет или она старше маркера: возможностей, о
    которых спрашивают, у неё нет ни одной. Спрашивать нужно там, где конфиг
    объявляет то, что исполняет скрипт: он обновляется кнопкой, отдельно от
    самого инструмента, и настройка легко оказывается впереди копии.
    """
    text = _read(tasks_dir / cfg.get("status_script", "set_status.py"))
    if text is None:
        return set()
    m = _CAPABILITIES_RE.search(text)
    return set(_CAPABILITY_RE.findall(m.group(1))) if m else set()


def _targets_state(project_root: Path, part: str, targets: list[tuple[str, Path, str]],
                   cfg: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """(нет части целиком, недостающие элементы, разошедшиеся) для многофайловой части.

    Ни одного файла на диске — часть не разворачивали; хотя бы один есть —
    остальные разбираем поэлементно. Отсутствующий файл и отставший от шаблона
    — разные вещи: называть несуществующий скилл «устаревшим» значит врать
    в баннере (пользователь ищет, что же там устарело, а файла просто нет).
    Кастомизированный элемент не устарел: шаблон с развёртывания не двигался.
    """
    states = [(name, element_state(project_root, part, name, path, expected, cfg))
              for name, path, expected in targets]
    if targets and all(state == baseline.MISSING for _n, state in states):
        return [name for name, _state in states], [], []
    absent = [name for name, state in states if state == baseline.MISSING]
    modified = [name for name, state in states
                if state not in _QUIET_STATES and state != baseline.MISSING]
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
        if part in SINGLE_FILE_PARTS:
            missing, outdated = _script_state(tasks_dir, cfg, part)
        elif part == "epics":
            missing = [] if (tasks_dir / EPICS_FILE).is_file() else [EPICS_FILE]
            outdated = []  # реестр эпиков — данные пользователя, эталона нет
        elif part == "logs":
            name = cfg.get("logs_dir", "logs")
            missing = [] if (tasks_dir / name).is_dir() else [f"{name}/"]
            outdated = []
        elif part in ("skills", "commands", "vault"):
            missing, partial, outdated = _targets_state(
                project_root, part, part_targets(project_root, part, cfg), cfg)
        else:  # rules
            missing = rules_missing(project_root, cfg)
            _m, _absent, outdated = _targets_state(
                project_root, part, _rules_targets(project_root, cfg), cfg)

        for state, names in (("missing", missing), ("partial", partial), ("outdated", outdated)):
            # Недостающие элементы разворачиваются той же кнопкой, что и
            # отсутствующая часть, поэтому код у них общий — различается текст
            code = spec["missing"] if state == "partial" else spec[state]
            if names and code:
                issues.append({"part": part, "code": code, "state": state, "names": names})
    return issues
