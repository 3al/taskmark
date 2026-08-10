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

# Перевод статуса: та же строка комментария, но в позиции автора стоит источник
# перехода («доска», «скрипт (Модель)»). Источник — часть факта наравне со
# статусами: по нему видно, прошёл переход через инструмент или мимо него, а
# мимо инструмента как раз и теряются хвосты задачи.
TRANSITION_TEXT = "{was} → {now}"

# Остальные действия жизненного цикла. Строку пишет тот же инструмент, что
# правит поля, — иначе событие остаётся только в frontmatter, где хронологии
# нет вовсе: видно нынешнее состояние, но не видно, когда и что с задачей
# сделали.
#
# Стрелки здесь нет намеренно: она — признак перевода статуса (`TRANSITION_RE`),
# и «было → стало» в тексте другого события читалось бы разбором как переход.
# По той же причине название берётся в кавычки: в нём стрелка может быть своя
BLOCK_TEXT = "блокировка: ждёт {ids}"
UNBLOCK_TEXT = "блокировка снята: {ids}"
# Обратный конец зависимости: `blocks` правится синхронно, и файл блокера
# обязан показывать, что на него кто-то встал
BLOCKS_TEXT = "блокирует {id}"
UNBLOCKS_TEXT = "больше не блокирует {id}"
PAUSE_TEXT = "пауза: {reason}"
RESUME_TEXT = "пауза снята"
TYPE_TEXT = "тип: {now} (было {was})"
TITLE_TEXT = "название: «{now}» (было «{was}»)"

# Кем подписан перевод: инструментом, а не моделью. Модель уточняет источник
# в скобках («скрипт (Claude Opus 5)»), но подпись строки — всегда источник
SCRIPT_SOURCE = "скрипт"

# Отличить перевод от смыслового комментария нужно и разбору, и рендеру, и
# одной стрелки для этого мало: она обычна в тексте («секция → «Комментарии»,
# миграция…»). Признаков три, и порознь ни один не годится:
#   - подпись строки — источник (доска / скрипт), а не модель агента;
#   - строка состоит из перехода целиком, от подписи до конца;
#   - подписи статусов не содержат запятых, кавычек и двоеточий — тем и
#     отсекается обычная фраза со стрелкой, случайно подписанная источником:
#     остальные события ЖЦ пишутся как «пауза: …», «название: «…»».
TRANSITION_RE = re.compile(
    r"^- \*\*(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\*\* · "
    rf"(?P<author>{BOARD_AUTHOR}|{SCRIPT_SOURCE}(?: \([^()·]+\))?) · "
    r"(?P<from>[^·→,:«»]+) → (?P<to>[^·→,:«»]+)$")


def _one_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def transition_note(author: str, was: str, now: str) -> str:
    """Готовая строка перевода — общая формула для обоих путей записи.

    Зеркало `TRANSITION_TEXT` в `templates/tasks/set_status.py`: скрипт
    автономен и пишет сам, но формат обязан совпадать до символа — иначе
    историю переводов нельзя ни прочитать разбором, ни показать одинаково.
    """
    return (f"- **{datetime.now().strftime('%Y-%m-%d %H:%M')}** · {_one_line(author)} · "
            + TRANSITION_TEXT.format(was=_one_line(was), now=_one_line(now)))


def append_transition(task_path, was: str, now: str,
                      author: str = BOARD_AUTHOR) -> str | None:
    """Записать перевод статуса строкой в «Комментарии». Вернуть строку.

    Пишется **всегда**, а не только когда на этапе объявлены требования:
    история переводов обязана быть полной у любого проекта. Возврат назад даёт
    две строки — сам перевод и снятое подтверждение: это разные факты об одном
    событии, и склеенные они теряют и полноту, и единый формат.
    """
    was, now = _one_line(was), _one_line(now)
    if not was or not now or was == now:
        return None
    return append_note(task_path, TRANSITION_TEXT.format(was=was, now=now), author)


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
