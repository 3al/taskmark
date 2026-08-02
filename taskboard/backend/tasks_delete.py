"""Удаление задачи: файл и строка доски за одно действие (TASK-043).

Убрать задачу можно было только руками — стереть файл и вычистить строку из
board.md. Операция регулярная (ошибочно заведённые, дубли, эксперименты), а
делать её в двух местах — верный способ рассинхронизировать доску с файлами.

Возможность **выключена по умолчанию** и включается в настройках проекта:
доска работает с файлами пользователя, и кнопка необратимого удаления не должна
оказаться под рукой у того, кто её не просил. Проверка живёт здесь, а не только
в UI, — правило одно для всех клиентов.
"""

from __future__ import annotations

from pathlib import Path

from backend.stall import set_blocked_by, stall_of
from backend.task_parser import find_task_file, parse_frontmatter


def _meta(path: Path) -> dict:
    try:
        return parse_frontmatter(path.read_text(encoding="utf-8-sig"))[0]
    except OSError:
        return {}


def delete_plan(tasks_dir: Path, task_id: str) -> dict:
    """Что произойдёт при удалении: название, статус, кого это заденет.

    Диалог подтверждения должен назвать последствия до, а не после: задача
    может держать другие (`blocks`), и их пометки придётся снять.
    """
    tasks_dir = Path(tasks_dir)
    task_id = task_id.strip().upper()
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    meta = _meta(path)
    return {"ok": True, "task": task_id, "file": path.name,
            "title": meta.get("title", ""), "status": meta.get("status", ""),
            "blocks": stall_of(meta)["blocks"]}


def delete_task(tasks_dir: Path, cfg: dict, task_id: str) -> dict:
    """Удалить задачу: снять пометки у соседей, стереть файл, вычистить доску.

    Порядок важен: обратные ссылки снимаются **до** удаления файла — их правит
    `set_blocked_by`, которому нужна сама задача. Иначе у соседей остались бы
    блокеры-призраки: ждут задачу, которой больше нет.
    """
    if not cfg.get("delete_tasks"):
        return {"ok": False,
                "error": "Удаление задач выключено в настройках проекта"}

    tasks_dir = Path(tasks_dir)
    task_id = task_id.strip().upper()
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}

    # Кого держала удаляемая задача — им пометку снимаем
    unblocked: list[str] = []
    for dependant in stall_of(_meta(path))["blocks"]:
        other = find_task_file(tasks_dir, dependant)
        if other is None:
            continue
        rest = [b for b in stall_of(_meta(other))["blocked_by"] if b != task_id]
        if set_blocked_by(tasks_dir, dependant, rest).get("ok"):
            unblocked.append(dependant)

    entry_removed = _drop_entry(tasks_dir / cfg.get("board_file", "board.md"), task_id)

    try:
        path.unlink()
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось удалить файл: {exc}"}

    return {"ok": True, "task": task_id, "file": path.name,
            "board": entry_removed, "unblocked": unblocked}


def _drop_entry(board_path: Path, task_id: str) -> bool:
    """Убрать строку задачи с доски. False — строки не было (это не ошибка).

    Заглушку `_(нет)_` в опустевший раздел здесь не возвращаем: этим занимается
    смена статуса, а удаление не должно знать про формат разделов больше, чем
    нужно, чтобы убрать одну строку.
    """
    try:
        lines = board_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False
    kept = [ln for ln in lines if not ln.lstrip().startswith(f"- {task_id} ")]
    if len(kept) == len(lines):
        return False
    board_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True
