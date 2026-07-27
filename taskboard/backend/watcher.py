"""Слежение за папкой tasks/ активного проекта и рассылка SSE-событий."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from backend.scaffold import agentic_paths


class TasksWatcher:
    """Watchdog-наблюдатель с дебаунсом и подписчиками (SSE)."""

    def __init__(self, debounce_sec: float = 0.3, monitor_sec: float = 2.0) -> None:
        self._observer: Observer | None = None
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._debounce = debounce_sec
        self._last_event = 0.0
        self._trailing: threading.Timer | None = None
        self._trailing_lock = threading.Lock()
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
        with self._trailing_lock:
            if self._trailing:
                self._trailing.cancel()
                self._trailing = None
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

    def _observed_paths(self) -> list[Path]:
        """Папки под наблюдением: tasks/ проекта + агентское окружение в его корне.

        Скиллы и команды живут вне tasks/, но их устаревание тоже показывается
        на доске — без наблюдения за ними алерт обновлялся бы только по F5.
        """
        if not self._watched or not self._watched.is_dir():
            return []
        return [self._watched, *agentic_paths(self._watched.parent)]

    def _start_observer(self) -> bool:
        """Пересоздать наблюдателя на self._observed_paths(). Под _watch_lock.

        Старый наблюдатель останавливается ДО подъёма нового. На macOS watch-и
        FSEvents глобальны в процессе: второй наблюдатель на ещё занятый путь
        падает с «it is already scheduled» прямо в потоке эмиттера, тот молча
        умирает, монитор живости пересоздаёт наблюдателя — и так по кругу, а
        живые обновления доски не приходят.

        Возвращает True, если наблюдатель поднят (False — наблюдать нечего).
        """
        previous, self._observer = self._observer, None
        if previous:
            previous.stop()
            previous.join(timeout=2)

        paths = self._observed_paths()
        if not paths:
            return False

        handler = _Handler(self._on_change)
        fresh = Observer()
        for path in dict.fromkeys(str(p) for p in paths):
            fresh.schedule(handler, path, recursive=True)
        fresh.daemon = True
        fresh.start()
        self._observer = fresh
        return True

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
                if not self._watching:
                    continue
                observer = self._observer
                # _observer == None при живом наблюдении — сорвавшийся старт
                # (или исчезнувшая папка): пробуем поднять заново
                dead = (
                    observer is None
                    or not observer.is_alive()
                    or any(not e.is_alive() for e in observer.emitters)
                )
                if dead:
                    try:
                        if self._start_observer():
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
            self._schedule_trailing()
            return
        self._last_event = now
        self._notify()

    def _schedule_trailing(self) -> None:
        """Досылка события в конце серии.

        Пропускать хвост серии нельзя: фронт перечитывает доску по первому
        событию, и если запись файла завершилась уже после этого, последнее
        состояние никем не досылается — алерт на доске висит до ручного F5.
        """
        with self._trailing_lock:
            if self._trailing and self._trailing.is_alive():
                return  # хвост уже запланирован
            self._trailing = threading.Timer(self._debounce, self._emit_trailing)
            self._trailing.daemon = True
            self._trailing.start()

    def _emit_trailing(self) -> None:
        if self.stopped.is_set():
            return
        self._last_event = time.monotonic()
        self._notify()

    def _notify(self) -> None:
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
