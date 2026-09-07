"""Разбор пользовательского ввода срока; хранение остаётся календарной датой."""
import re
from calendar import monthrange
from datetime import date, timedelta


def parse_due_input(value: str, today: "date | None" = None) -> "date | None":
    """Дата или целое число дней, недель, месяцев от локального сегодня.

    Копия в backend/due_input.py и автономном set_status.py: синхронность
    проверяют общие календарные примеры в test_due_input.py.
    """
    value = (value or "").strip().lower()
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return date.fromisoformat(value)
        match = re.fullmatch(
            r"([0-9]+)\s+(день|дня|дней|неделя|недели|недель|месяц|месяца|месяцев)",
            value,
        )
        if not match:
            return None
        count = int(match.group(1))
        unit = match.group(2)
        base = today or date.today()
        if unit.startswith("месяц"):
            year, month = divmod(base.year * 12 + base.month - 1 + count, 12)
            month += 1
            return date(year, month, min(base.day, monthrange(year, month)[1]))
        return base + timedelta(days=count * (7 if unit.startswith("недел") else 1))
    except (ValueError, OverflowError):
        return None
