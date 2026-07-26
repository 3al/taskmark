"""Слежение за папкой tasks/ активного проекта и рассылка SSE-событий."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class TasksWatcher:
    """Watchdog-наблюдатель с дебаунсом и подписчиками (SSE)."""

    def __init__(self, debounce_sec: float = 0.3) -> None:
        self._observer: Observer | None = None
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._debounce = debounce_sec
        self._last_event = 0.0
        self._watched: Path | None = None
        # Флаг остановки: SSE-генераторы проверяют его, чтобы завершиться
        # при shutdown сервера (иначе uvicorn --reload виснет на open SSE)
        self.stopped = threading.Event()

    def shutdown(self) -> None:
        """Завершить все SSE-подписки и остановить наблюдение."""
        self.stopped.set()
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait("shutdown")
            except queue.Full:
                pass
        self.stop()

    def watch(self, path: Path) -> None:
        """Перенавесить наблюдение на новую папку."""
        self.stop()
        if not path.is_dir():
            return
        self._watched = path
        handler = _Handler(self._on_change)
        self._observer = Observer()
        self._observer.schedule(handler, str(path), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def subscribe(self) -> queue.Queue:
        """Подписаться на события изменений."""
        q: queue.Queue = queue.Queue(maxsize=10)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _on_change(self) -> None:
        # Дебаунс: watcher шлёт серию событий на одну запись файла
        now = time.monotonic()
        if now - self._last_event < self._debounce:
            return
        self._last_event = now
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait("changed")
            except queue.Full:
                pass


class _Handler(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        self._callback = callback

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        # Реагируем только на md/log-файлы, игнорируем кэши
        name = Path(event.src_path).name
        if name.startswith(".") or "__pycache__" in event.src_path:
            return
        self._callback()
