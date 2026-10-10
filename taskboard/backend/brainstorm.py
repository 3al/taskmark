"""Краткоживущее состояние брейншторма для превью задачи."""

from __future__ import annotations

import threading
import time
import re
from pathlib import Path


MODES = {
    "simple": ("Брейншторм", "Критик"),
    "team": ("Командный брейншторм", "Оппонент"),
}
PHASES = {
    "research": "Исследование и предложения",
    "critique": "Критика",
    "responses": "Ответы на критику",
    "final": "Финальная оценка",
    "synthesis": "Синтез решения",
}
TERMINAL = {"finished", "cancelled"}
TTL = 30 * 60


class ActivityStore:
    """Состояние по проекту, задаче и запуску; файлы задач не меняются."""

    def __init__(self, ttl: float = TTL):
        self.ttl = ttl
        self._runs: dict[tuple[str, str, str], dict] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for key in list(self._runs):
            if now - self._runs[key]["updated"] >= self.ttl:
                del self._runs[key]

    def update(self, project: str, task: str, run: str, mode: str,
               phase: str, agent: str) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (project, task, run, agent)):
            raise ValueError("Для брейншторма нужны проект, задача, запуск и агент")
        if mode not in MODES:
            raise ValueError("Неизвестный режим брейншторма")
        if phase not in PHASES and phase not in TERMINAL:
            raise ValueError("Неизвестный этап брейншторма")
        if mode == "simple" and phase in {"responses", "final"}:
            raise ValueError("Этот этап есть только у командного брейншторма")
        key = (project, task, run)
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if phase in TERMINAL:
                self._runs.pop(key, None)
            else:
                self._runs[key] = {"run": run, "mode": mode, "phase": phase,
                                   "agent": agent, "updated": now}

    def get(self, project: str, task: str | None = None) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            result = []
            for (p, t, _), state in self._runs.items():
                if p != project or (task is not None and t != task):
                    continue
                mode, phase = state["mode"], state["phase"]
                result.append({k: state[k] for k in ("run", "mode", "phase", "agent")})
                result[-1].update(task=t, mode_label=MODES[mode][0], phase_label=PHASES[phase],
                                  expires_in=max(0, self.ttl - (now - state["updated"])))
            return result


activity = ActivityStore()


def log_results(logs_dir: Path) -> dict[str, str]:
    """Последний сохранённый протокол каждой задачи; чужие логи не подходят."""
    latest: dict[str, tuple[int, str]] = {}
    try:
        files = list(logs_dir.iterdir())
    except OSError:
        return {}
    for path in files:
        match = re.fullmatch(r"(TASK-\d+)-brainstorm(?:-team)?-log\.md", path.name, re.I)
        if not match:
            continue
        try:
            if not path.is_file() or path.is_symlink():
                continue
            candidate = (path.stat().st_mtime_ns, path.name)
        except OSError:
            continue
        task = match[1].upper()
        if candidate > latest.get(task, (-1, "")):
            latest[task] = candidate
    return {task: entry[1] for task, entry in latest.items()}
