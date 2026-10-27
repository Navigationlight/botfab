"""Типи налаштувань користувача: перевірка й нормалізація значень."""
import re

HINTS = {
    "time": "час, наприклад 18:00",
    "days": "числа через кому, наприклад 3, 1",
    "number": "ціле число",
    "text": "будь-який текст до 200 символів",
}


def parse_value(kind, raw):
    """Повертає нормалізований рядок або кидає ValueError із зрозумілим поясненням."""
    raw = raw.strip()
    if kind == "time":
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("Час пиши так: 18:00 (години 0-23, хвилини 00-59).")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "days":
        if not re.fullmatch(r"\d{1,3}(\s*,\s*\d{1,3})*", raw):
            raise ValueError("Дні пиши числами через кому, наприклад: 3, 1")
        nums = list(dict.fromkeys(int(x) for x in re.split(r"\s*,\s*", raw)))
        if any(n > 365 for n in nums):
            raise ValueError("Кожне число має бути не більше 365.")
        return ", ".join(map(str, nums))
    if kind == "number":
        if not re.fullmatch(r"-?\d{1,9}", raw):
            raise ValueError("Потрібне ціле число, наприклад 5.")
        return str(int(raw))
    if kind == "text":
        if not raw or len(raw) > 200:
            raise ValueError("Текст має бути від 1 до 200 символів.")
        return raw
    raise ValueError(f"Невідомий тип «{kind}».")


def days_list(value):
    return [int(x) for x in value.split(",")]