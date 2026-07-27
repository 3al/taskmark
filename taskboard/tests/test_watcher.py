"""Тесты живучести TasksWatcher: восстановление после молчаливой смерти потоков watchdog.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.watcher import TasksWatcher  # noqa: E402


def wait_for(pred, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class _FakeEmitter:
    def __init__(self, path: str) -> None:
        self.path = path
        self.owner = False
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


class FakeFSEventsObserver:
    """Наблюдатель с семантикой macOS FSEvents.

    `_fsevents.add_watch` хранит watch-и в глобальном для процесса реестре,
    ключ — путь: второй наблюдатель на уже занятый путь падает с
    `RuntimeError: ... it is already scheduled` прямо в потоке эмиттера,
    и тот молча умирает (в watchdog это исключение никем не ловится).
    """

    scheduled: set[str] = set()

    def __init__(self) -> None:
        self._paths: list[str] = []
        self.emitters: list[_FakeEmitter] = []
        self._alive = False
        self.daemon = False

    def schedule(self, handler, path, recursive=False) -> None:
        self._paths.append(path)

    def start(self) -> None:
        self._alive = True
        for path in self._paths:
            emitter = _FakeEmitter(path)
            if path in FakeFSEventsObserver.scheduled:
                emitter.alive = False  # add_watch бросил «already scheduled»
            else:
                FakeFSEventsObserver.scheduled.add(path)
                emitter.owner = True
            self.emitters.append(emitter)

    def stop(self) -> None:
        self._alive = False
        for emitter in self.emitters:
            if emitter.owner:
                FakeFSEventsObserver.scheduled.discard(emitter.path)
                emitter.owner = False
            emitter.alive = False

    def join(self, timeout=None) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive


class TasksWatcherRescheduleTest(unittest.TestCase):
    """Пересоздание наблюдателя не должно конфликтовать само с собой."""

    def setUp(self) -> None:
        FakeFSEventsObserver.scheduled.clear()

    def test_reschedule_same_path_keeps_emitters_alive(self) -> None:
        """Повторное наблюдение того же пути (реактивация проекта из лаунчера).

        Если новый наблюдатель поднимается до остановки старого, на macOS его
        эмиттер умирает с «already scheduled»; монитор живости видит мёртвый
        эмиттер, пересоздаёт наблюдатель — и так по кругу, а живые обновления
        доски не приходят.
        """
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("backend.watcher.Observer", FakeFSEventsObserver):
            w = TasksWatcher(debounce_sec=0.05, monitor_sec=5.0)
            w.watch(Path(tmp))
            try:
                w.watch(Path(tmp))
                observer = w._observer
                self.assertIsNotNone(observer)
                self.assertTrue(
                    all(e.is_alive() for e in observer.emitters),
                    "эмиттер нового наблюдателя умер: путь ещё занят старым")
            finally:
                w.shutdown()

    def test_monitor_restart_keeps_emitters_alive(self) -> None:
        """Тот же конфликт при перезапуске монитором — источник бесконечного цикла."""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("backend.watcher.Observer", FakeFSEventsObserver):
            w = TasksWatcher(debounce_sec=0.05, monitor_sec=0.2)
            w.watch(Path(tmp))
            try:
                old = w._observer
                for emitter in old.emitters:  # молчаливая смерть эмиттера
                    emitter.alive = False

                def restarted() -> bool:
                    observer = w._observer
                    return observer is not None and observer is not old

                self.assertTrue(wait_for(restarted, 3.0),
                                "монитор не перезапустил наблюдатель")
                time.sleep(0.5)  # ещё несколько тиков монитора
                observer = w._observer
                self.assertTrue(
                    all(e.is_alive() for e in observer.emitters),
                    "после перезапуска эмиттер снова мёртв — цикл перезапусков")
            finally:
                w.shutdown()


class TasksWatcherDebounceTest(unittest.TestCase):
    def test_trailing_event_after_burst(self) -> None:
        """Хвост серии не должен теряться.

        Дебаунс пропускает только первое событие серии. Если запись файла
        завершилась уже после того, как фронт перечитал доску по первому
        событию, финальное состояние никем не досылается — баннер на доске
        так и висит до ручного F5.
        """
        w = TasksWatcher(debounce_sec=0.2, monitor_sec=5.0)
        q = w.subscribe()
        try:
            w._on_change()  # первое событие серии — проходит
            self.assertTrue(wait_for(lambda: not q.empty(), 1.0))
            q.get()

            w._on_change()  # подавлено дебаунсом
            self.assertTrue(wait_for(lambda: not q.empty(), 2.0),
                            "хвостовое событие серии не пришло")
        finally:
            w.shutdown()


class TasksWatcherRecoveryTest(unittest.TestCase):
    def test_recovers_after_emitter_thread_death(self) -> None:
        """Поток эмиттера watchdog может молча умереть (исключение в его цикле
        никем не ловится). Наблюдатель обязан это заметить и перезапуститься,
        иначе живые обновления доски тихо прекращаются (застывшие алерты на фронте).
        """
        with tempfile.TemporaryDirectory() as tmp:
            w = TasksWatcher(debounce_sec=0.05, monitor_sec=0.2)
            q = w.subscribe()
            w.watch(Path(tmp))
            try:
                # Штатное событие доходит до подписчика
                (Path(tmp) / "a.md").write_text("x", encoding="utf-8")
                self.assertTrue(wait_for(lambda: not q.empty()),
                                "стартовое событие не дошло до подписчика")
                q.get()

                # Симулируем молчаливую смерть эмиттера watchdog
                old_observer = w._observer
                emitters = list(old_observer.emitters)
                self.assertTrue(emitters, "у наблюдателя нет эмиттеров")
                for e in emitters:
                    e.stop()
                self.assertTrue(
                    wait_for(lambda: all(not e.is_alive() for e in emitters)),
                    "эмиттер не завершился")
                # Поток-диспетчер при этом остаётся жив — смерть снаружи не видна
                self.assertTrue(old_observer.is_alive())

                # Наблюдатель должен восстановиться
                def restored() -> bool:
                    observer = w._observer
                    return observer is not None and observer is not old_observer \
                        and observer.is_alive()

                self.assertTrue(wait_for(restored),
                                "наблюдатель не восстановился после смерти эмиттера")

                # И события снова доходят до подписчика
                time.sleep(0.2)  # окно дебаунса
                (Path(tmp) / "b.md").write_text("x", encoding="utf-8")
                self.assertTrue(wait_for(lambda: not q.empty()),
                                "событие не дошло после восстановления наблюдателя")
            finally:
                w.shutdown()

    def test_watches_agentic_env_outside_tasks_dir(self) -> None:
        """Скиллы и команды лежат в корне проекта, а не в tasks/.

        Без наблюдения за ними алерт об устаревших скиллах появляется только
        после ручной перезагрузки страницы — в отличие от create_task.py,
        который лежит внутри tasks/ и обновляется живьём.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / "tasks"
            tasks_dir.mkdir()
            skill = root / ".claude" / "skills" / "start-task" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("исходный", encoding="utf-8")
            command = root / ".opencode" / "commands" / "start-task.md"
            command.parent.mkdir(parents=True)
            command.write_text("исходный", encoding="utf-8")

            w = TasksWatcher(debounce_sec=0.05, monitor_sec=0.2)
            q = w.subscribe()
            w.watch(tasks_dir)
            try:
                skill.write_text("устарел", encoding="utf-8")
                self.assertTrue(wait_for(lambda: not q.empty()),
                                "правка скилла не дошла до подписчика")
                q.get()

                time.sleep(0.2)  # окно дебаунса
                command.write_text("устарела", encoding="utf-8")
                self.assertTrue(wait_for(lambda: not q.empty()),
                                "правка команды opencode не дошла до подписчика")
            finally:
                w.shutdown()

    def test_no_resurrect_after_stop(self) -> None:
        """Явная остановка не должна отменяться монитором."""
        with tempfile.TemporaryDirectory() as tmp:
            w = TasksWatcher(debounce_sec=0.05, monitor_sec=0.2)
            w.watch(Path(tmp))
            try:
                w.stop()
                self.assertIsNone(w._observer)
                time.sleep(0.6)  # несколько тиков монитора
                self.assertIsNone(w._observer,
                                  "монитор воскресил остановленный наблюдатель")
            finally:
                w.shutdown()


if __name__ == "__main__":
    unittest.main()
