"""Комментарии задачи: запись строки в хронологию файла задачи.

Обычно их пишет автономный `tasks/set_status.py` — он же ставит время из
системы, чтобы его нельзя было выдумать задним числом. Бэкенду это понадобилось
ровно для одного случая: человек подтверждает требование этапа с доски
(TASK-110). Подтверждение обязано оставлять след — иначе оно неотличимо от
этапа, пройденного молча.

Формат строки — зеркало скрипта (`add_note`): `- **ГГГГ-ММ-ДД ЧЧ:ММ** · кто ·
суть`. Разъедутся форматы — разъедется и чтение хронологии человеком, и разбор
`check_task_file`, который следит за порядком строк.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

NOTES_SECTION = "## Комментарии"
# Прежнее имя секции (она называлась по автору, а пишет туда давно не только
# агент). Файлы задач переименовывает разовая миграция, но пока она не прошла —
# писать нужно в существующую секцию, а не заводить рядом вторую
LEGACY_NOTES_SECTION = "## Заметки агента"
COMMITS_SECTION = "## История коммитов"

# Кем подписана строка, пришедшая с доски. Модель агента бэкенду неизвестна, а
# копировать её из соседней строки нельзя — именно так история начинает врать
BOARD_AUTHOR = "доска"


def _one_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def append_note(task_path, text: str, author: str = BOARD_AUTHOR) -> str | None:
    """Дописать строку в конец секции «Комментарии». Вернуть её.

    None — файл не читается или в нём нет ни секции комментариев, ни места
    для неё: молча портить структуру чужого файла нельзя.
    """
    path = Path(task_path)
    text = _one_line(text)
    if not text:
        return None
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None

    note = f"- **{datetime.now().strftime('%Y-%m-%d %H:%M')}** · {author} · {text}"
    lines = content.splitlines()

    start = next((i for i, ln in enumerate(lines)
                  if ln.strip() in (NOTES_SECTION, LEGACY_NOTES_SECTION)), None)
    if start is None:
        # Секции нет — заводим её перед историей коммитов (она остаётся
        # последней) или в конце файла
        at = next((i for i, ln in enumerate(lines)
                   if ln.strip() == COMMITS_SECTION), len(lines))
        block = [NOTES_SECTION, "", note, ""]
        lines[at:at] = block
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return note

    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    body = lines[start + 1:end]
    while body and not body[-1].strip():
        body.pop()
    if not body:
        body = [""]
    elif body[0].strip():
        body.insert(0, "")
    body.append(note)
    if end < len(lines):
        body.append("")
    lines[start + 1:end] = body
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note
