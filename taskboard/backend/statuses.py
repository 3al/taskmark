"""Пайплайн статусов задачи: каталог кирпичиков, порядок, направления, действия.

Единственный источник правды о жизненном цикле. Три независимые части конфига:

    "pipeline": ["backlog", "todo", "development", "testing", "completed", "cancelled"]
    "actions":  {"create": "backlog", "pick": "todo",
                 "start": "development", "return": "development"}
    "statuses": {"todo": {"label": "To Do", "section": "To Do", "color": "amber"}}

`pipeline` задаёт порядок сам собой: правее текущего статуса — «вперёд», левее —
«назад». Запретов нет: пайплайн описывает ожидаемый маршрут, а не забор.
`development → testing` (простая задача) и `development → completed` (ночной
хотфикс) — законные ситуации, гонять задачу по всем статусам бессмысленно.
Ожидаемым считается ближайший следующий статус — его скиллы и UI предлагают
первым, остальные доступны без церемоний.

Дубль каталога живёт в templates/tasks/set_status.py: скрипт автономен и
работает в проектах без установленного taskboard.
"""

from __future__ import annotations

# Библиотека дефолтов, а не ограничение: пользователь собирает пайплайн из этих
# кирпичиков, но может включить и свой ключ, описав его в конфиге ("statuses").
CATALOG: dict[str, dict] = {
    "backlog": {"label": "Backlog", "section": "Backlog", "color": "zinc"},
    # todo и queued — равноправные имена одного смысла (приоритизированный
    # список, откуда берут работу); включают то, чьё имя привычнее
    "todo": {"label": "To Do", "section": "To Do", "color": "amber"},
    "queued": {"label": "Очередь", "section": "Queue", "color": "amber"},
    # Возвратные статусы ставятся ЛЕВЕЕ development: тогда возврат из проверки —
    # движение назад, а «вперёд» из тестирования ведёт к релизу, а не к багам.
    # reentry отличает их от очереди: новую работу берут не отсюда, и «минуя
    # to_fix» — не пропуск шага, а нормальный ход по маршруту
    "to_fix": {"label": "На исправление", "section": "To Fix", "color": "rose",
               "reentry": True},
    "development": {"label": "Development", "section": "Development", "color": "sky"},
    # Проверка автором на локальной сборке — не работа тестировщика на стенде
    "local_testing": {"label": "Локальная проверка", "section": "Local Testing",
                      "color": "yellow"},
    "review": {"label": "Review", "section": "Review", "color": "violet"},
    "to_testing": {"label": "К тестированию", "section": "To Testing", "color": "cyan"},
    "testing": {"label": "Testing", "section": "Testing", "color": "orange"},
    # Одно семейство с to_fix и to_testing: «ждёт, чтобы попасть на этап»
    "to_release": {"label": "К релизу", "section": "To Release", "color": "teal"},
    # completed и done — взаимозаменяемые терминалы; ключ пишется во frontmatter
    # (status: done), поэтому это не только подпись. Включают один из двух
    "completed": {"label": "Completed", "section": "Completed", "color": "emerald"},
    "done": {"label": "Done", "section": "Done", "color": "emerald"},
    # Съезд с маршрута: достижим отовсюду, но никогда не ожидаемый следующий шаг
    "cancelled": {"label": "Отменена", "section": "Cancelled", "color": "stone",
                  "offramp": True},
}

DEFAULT_PIPELINE = ["backlog", "queued", "development", "review", "testing", "completed"]
DEFAULT_ACTIONS = {"create": "backlog", "start": "development"}

# Готовые маршруты: собирать пайплайн с нуля в каждом проекте — работа руками
# ради того, что у всех примерно одинаково. Это не ограничение, а стартовая
# точка: после применения пресет правится тем же редактором
PRESETS: tuple[dict, ...] = (
    {
        "name": "Простой",
        "hint": "без ревью: сделал — проверил — закрыл",
        "pipeline": ["backlog", "todo", "development", "testing", "done", "cancelled"],
        "actions": {"create": "backlog", "pick": "todo",
                    "start": "development", "return": "development"},
    },
    {
        "name": "С ревью",
        "hint": "код-ревью между разработкой и тестированием",
        "pipeline": ["backlog", "todo", "development", "review", "testing",
                     "done", "cancelled"],
        "actions": {"create": "backlog", "pick": "todo",
                    "start": "development", "return": "development"},
    },
    {
        "name": "Полный",
        "hint": "локальная проверка, ревью, стенд и релиз; возвраты через «На исправление»",
        "pipeline": ["backlog", "todo", "to_fix", "development", "local_testing",
                     "review", "to_testing", "testing", "to_release",
                     "done", "cancelled"],
        "actions": {"create": "backlog", "pick": "todo",
                    "start": "development", "return": "to_fix"},
    },
)


def _titleize(key: str) -> str:
    """Заголовок раздела для ключа без описания: hotfix_wait → Hotfix Wait."""
    return " ".join(part.capitalize() for part in key.split("_") if part)


class Pipeline:
    """Разобранный пайплайн проекта."""

    def __init__(self, statuses: list[dict], actions: dict) -> None:
        self._statuses = statuses
        self._by_key = {s["key"]: s for s in statuses}
        self._actions = actions

    # --- Состав ---

    def keys(self) -> list[str]:
        return [s["key"] for s in self._statuses]

    def statuses(self) -> list[dict]:
        return list(self._statuses)

    def get(self, key: str) -> dict | None:
        return self._by_key.get(key)

    def has(self, key: str) -> bool:
        return key in self._by_key

    def section_of(self, key: str) -> str | None:
        meta = self._by_key.get(key)
        return meta["section"] if meta else None

    def status_for_section(self, title: str) -> str | None:
        """Статус по заголовку раздела доски (регистр и отступы не важны)."""
        needle = (title or "").strip().lower()
        for meta in self._statuses:
            if meta["section"].strip().lower() == needle:
                return meta["key"]
        return None

    def section_map(self) -> dict[str, str]:
        """Заголовок раздела (lower) → статус."""
        return {s["section"].strip().lower(): s["key"] for s in self._statuses}

    # --- Направления ---

    def _index(self, key: str) -> int | None:
        for i, meta in enumerate(self._statuses):
            if meta["key"] == key:
                return i
        return None

    def forward(self, key: str) -> list[str]:
        """Куда можно двинуть задачу вперёд. Съезды доступны всегда."""
        idx = self._index(key)
        if idx is None:
            return []
        later = [s["key"] for s in self._statuses[idx + 1:]]
        offramps = [s["key"] for s in self._statuses
                    if s.get("offramp") and s["key"] != key and s["key"] not in later]
        return later + offramps

    def backward(self, key: str) -> list[str]:
        """Куда можно вернуть задачу. Из съезда — в любой статус маршрута."""
        idx = self._index(key)
        if idx is None:
            return []
        if self._statuses[idx].get("offramp"):
            return [s["key"] for s in self._statuses if s["key"] != key]
        return [s["key"] for s in self._statuses[:idx]]

    def next_expected(self, key: str) -> str | None:
        """Ожидаемый следующий шаг: ближайший вперёд, не считая съездов."""
        idx = self._index(key)
        if idx is None or self._statuses[idx].get("offramp"):
            return None
        for meta in self._statuses[idx + 1:]:
            if not meta.get("offramp"):
                return meta["key"]
        return None

    def is_forward(self, src: str, dst: str) -> bool:
        return dst in self.forward(src)

    def is_offramp(self, key: str) -> bool:
        """Съезд с маршрута (отмена): достижим отовсюду, но не шаг маршрута."""
        return bool((self._by_key.get(key) or {}).get("offramp"))

    def skipped(self, src: str, dst: str) -> list[str]:
        """Статусы, пропущенные при прыжке вперёд (для предупреждения в логе).

        Съезды и возвратные статусы не считаются пропущенными: через них
        маршрут не проходит, в них попадают по отдельному поводу.
        """
        i, j = self._index(src), self._index(dst)
        if i is None or j is None or j <= i:
            return []
        return [s["key"] for s in self._statuses[i + 1:j]
                if not s.get("offramp") and not s.get("reentry")]

    # --- Действия ---

    def action(self, name: str) -> str | None:
        return self._actions.get(name)

    def actions(self) -> dict:
        return dict(self._actions)


def _pipeline_keys(cfg: dict) -> list[str]:
    """Список статусов проекта с поддержкой конфигов, написанных до пайплайнов."""
    keys = cfg.get("pipeline")
    if not keys:
        # Старый ключ statuses был списком; новый — словарём оформления
        legacy = cfg.get("statuses")
        keys = legacy if isinstance(legacy, list) else None
    keys = list(keys) if keys else list(DEFAULT_PIPELINE)

    # Прежние queue_section/queued_status переезжают как переопределение очереди
    queued = cfg.get("queued_status")
    if queued and queued not in keys:
        keys = [queued if k == "queued" else k for k in keys]
    return [k for k in dict.fromkeys(keys) if k]


def _presentation(cfg: dict) -> dict[str, dict]:
    """Переопределения оформления из конфига проекта."""
    raw = cfg.get("statuses")
    overrides = dict(raw) if isinstance(raw, dict) else {}

    queued, section = cfg.get("queued_status", "queued"), cfg.get("queue_section")
    if section:
        meta = dict(overrides.get(queued) or {})
        meta.setdefault("section", section)
        meta.setdefault("label", section)
        overrides[queued] = meta
    return overrides


def load_pipeline(cfg: dict) -> Pipeline:
    """Собрать пайплайн из конфига проекта (дефолты → каталог → переопределения)."""
    cfg = cfg or {}
    overrides = _presentation(cfg)

    statuses: list[dict] = []
    for key in _pipeline_keys(cfg):
        meta = dict(CATALOG.get(key) or {})
        meta.update(overrides.get(key) or {})
        meta.setdefault("label", _titleize(key))
        meta.setdefault("section", _titleize(key))
        meta.setdefault("color", "zinc")
        meta["key"] = key
        statuses.append(meta)

    known = {s["key"] for s in statuses}
    # Значение может стать None: пайплайн бывает пустым, и действию некуда вести
    actions: dict[str, str | None] = dict(DEFAULT_ACTIONS)
    actions.update({k: v for k, v in (cfg.get("actions") or {}).items() if v})

    # Действие, указывающее в никуда, хуже отсутствующего: чинимся по составу
    if actions.get("create") not in known:
        actions["create"] = statuses[0]["key"] if statuses else None
    if actions.get("start") not in known:
        actions["start"] = next((s["key"] for s in statuses
                                 if s["key"] not in (actions.get("create"),)), None)
    if actions.get("return") not in known:
        actions["return"] = actions.get("start")
    if actions.get("pick") not in known:
        # Очередь ничем не привилегирована: очередь — это то, откуда берут работу,
        # то есть ближайший статус слева от начала работы. Возвратные статусы
        # пропускаем: to_fix стоит там же, но новую работу берут не из него
        keys = [s["key"] for s in statuses]
        start_idx = keys.index(actions["start"]) if actions.get("start") in keys else 0
        actions["pick"] = next(
            (s["key"] for s in reversed(statuses[:start_idx])
             if not s.get("reentry") and not s.get("offramp")),
            actions.get("create"))

    return Pipeline(statuses, actions)
