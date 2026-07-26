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

    def __init__(self, debounce_sec: float = 0.3, monitor_sec: float = 2.0) -> None:
        self._observer: Observer | None = None
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._debounce = debounce_sec
        self._last_event = 0.0
        self._watched: Path | None = None
        # Флаг остановки: SSE-генераторы проверяют его, чтобы завершиться
        # при shutdown сервера (иначе uvicorn --reload виснет на open SSE)
        self.stopped = threading.Event()
        # Монитор живости: поток эмиттера watchdog может молча умереть
        # (исключение в его цикле никем не ловится) — тогда живые обновления
        # тихо прекращаются. Монитор замечает это и пересоздаёт наблюдателя.
        self._monitor_sec = monitor_sec
        self._monitor: threading.Thread | None = None
        self._watch_lock = threading.Lock()
        self._watching = False

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
        with self._watch_lock:
            self._watched = path
            self._watching = path.is_dir()
            self._start_observer()
        self._ensure_monitor()

    def stop(self) -> None:
        with self._watch_lock:
            self._watching = False
            self._stop_observer()

    def _start_observer(self) -> None:
        """(Пересоздать наблюдателя на self._watched. Под _watch_lock."""
        self._stop_observer()
        if not self._watched or not self._watched.is_dir():
            return
        handler = _Handler(self._on_change)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watched), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def _stop_observer(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def _ensure_monitor(self) -> None:
        if self._monitor and self._monitor.is_alive():
            return
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()

    def _monitor_loop(self) -> None:
        while not self.stopped.wait(self._monitor_sec):
            with self._watch_lock:
                if not self._watching or not self._observer:
                    continue
                dead = (
                    not self._observer.is_alive()
                    or any(not e.is_alive() for e in self._observer.emitters)
                )
                if dead:
                    try:
                        self._start_observer()
                        print("[taskboard] watcher: наблюдатель перезапущен "
                              f"({self._watched})", flush=True)
                    except Exception:
                        pass  # повторим на следующем тике

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
