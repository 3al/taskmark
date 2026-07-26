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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.watcher import TasksWatcher  # noqa: E402


def wait_for(pred, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


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
                self.assertTrue(
                    wait_for(lambda: w._observer is not old_observer
                             and w._observer.is_alive()),
                    "наблюдатель не восстановился после смерти эмиттера")

                # И события снова доходят до подписчика
                time.sleep(0.2)  # окно дебаунса
                (Path(tmp) / "b.md").write_text("x", encoding="utf-8")
                self.assertTrue(wait_for(lambda: not q.empty()),
                                "событие не дошло после восстановления наблюдателя")
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
