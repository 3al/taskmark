#!/usr/bin/env python3
"""Хук Claude Code: напомнить о скилле scenarios при вызове скиллов задачи.

Эксперимент со сценариями (E007-SPEC, фаза A) требует звать `scenarios` у
задач типа feature и bug: до плана и перед сдачей. Правило записано только в
CLAUDE.md, а скиллы поставки о нём не знают, и агент, идущий по их шагам, его
пропускает. Скиллы ради временного прототипа не переписываем, поэтому
напоминание приходит от хука — в момент, когда скилл уже загружен.

Лежит в `tools/`, а не в `.claude/hooks/`: `.claude/` целиком игнорируется git,
а хук — часть прототипа этого репозитория и должен быть в нём. В поставку не
идёт; подключается локальной записью `PostToolUse` с матчером `Skill` в
`.claude/settings.json` и снимается вместе с экспериментом.

**Подсказка, а не запрет**: хук всегда завершается успешно и молчит обо всём,
что пошло не так.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Среда читает ответ как UTF-8, а Windows по умолчанию пишет в кодировке консоли
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Типы задач, у которых эксперимент ждёт сценарии
TYPES = {"feature", "bug"}

# Скилл → напоминание; {task} подставляется номером задачи
HINTS = {
    "start-task": ("Эксперимент E007-SPEC: {task} — задача со сценариями. "
                   "После изучения контекста и до плана и кода вызови скилл "
                   "`scenarios {task}`."),
    "fix-task": ("Эксперимент E007-SPEC: {task} — задача со сценариями. "
                 "Сверь сценарии с найденным до плана доработок: вызови скилл "
                 "`scenarios {task}` и согласуй поправки."),
    "handoff-task": ("Эксперимент E007-SPEC: {task} — задача со сценариями. "
                     "До шагов сдачи вызови скилл `scenarios {task} трасса`: "
                     "трасса в «Критериях приёмки» и строка журнала в TASK-286."),
}

TASK_RE = re.compile(r"\bTASK-\d+\b")
TYPE_RE = re.compile(r"^type:\s*(\S+)\s*$", re.MULTILINE)


def task_type(root: Path, task: str) -> str | None:
    """Тип задачи из frontmatter её файла; None — файла или поля нет."""
    for path in sorted((root / "tasks").glob(f"{task}-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        # Только frontmatter: такая же строка в тексте задачи типом не является
        parts = text.split("---", 2)
        if len(parts) < 3 or parts[0].strip():
            return None
        m = TYPE_RE.search(parts[1])
        return m.group(1) if m else None
    return None


def hint_for(event: dict) -> str:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    # Скилл плагина приходит с префиксом «плагин:имя»
    skill = str(tool_input.get("skill") or "").rsplit(":", 1)[-1]
    template = HINTS.get(skill)
    if template is None:
        return ""
    m = TASK_RE.search(str(tool_input.get("args") or ""))
    if m is None:
        return ""
    root = Path(event.get("cwd") or ".")
    if task_type(root, m.group(0)) not in TYPES:
        return ""
    return template.format(task=m.group(0))


def main() -> None:
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "null")
    except (ValueError, UnicodeError):
        return
    if not isinstance(event, dict):
        return
    hint = hint_for(event)
    if not hint:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "PostToolUse"),
            "additionalContext": hint,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
