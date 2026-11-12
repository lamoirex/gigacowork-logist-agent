"""Нормализация значений из выгрузок: текст, даты, номера заказов, поставщики,
способы доставки, страны, ФИО и короткие имена логистов.

Только стандартная библиотека. Все функции чистые и детерминированные.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

# ---------------------------------------------------------------- текст

_SPACES = re.compile(r"\s+")
# Латинские двойники кириллических букв (и наоборот) в названиях поставщиков
# не трогаем: это разные строки. Для номера заказа кириллическая «С» — частая
# ошибка ввода, поэтому она обрабатывается отдельно в parse_order.


def to_str(value) -> str:
    """Значение ячейки -> строка без краевых пробелов ('' для None)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value)
    s = unicodedata.normalize("NFKC", s).replace(" ", " ")
    return _SPACES.sub(" ", s).strip()


def norm_text(value) -> str:
    """Для сравнения: регистр, ё->е, схлопнутые пробелы."""
    return to_str(value).casefold().replace("ё", "е")


def norm_header(value) -> str:
    """Для сравнения заголовков: только буквы, цифры и знак №."""
    s = norm_text(value)
    return "".join(ch for ch in s if ch.isalnum() or ch == "№")


def is_empty(value, markers) -> bool:
    return norm_text(value) in markers


# ---------------------------------------------------------------- даты

_DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y %H:%M:%S",
    "%d.%m.%y %H:%M",
    "%d.%m.%y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
)
_EXCEL_EPOCH = dt.datetime(1899, 12, 30)


def parse_datetime(value):
    """Ячейка -> datetime или None. Понимает Excel-даты, серийные номера и строки."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None, microsecond=0)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 <= value <= 80000:  # 1954..2119 — правдоподобный серийный номер Excel
            return (_EXCEL_EPOCH + dt.timedelta(days=float(value))).replace(microsecond=0)
        return None
    s = to_str(value)
    if not s:
        return None
    s = s.replace("г.", "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fmt_date(value, fmt="%d.%m.%Y") -> str:
    d = parse_datetime(value)
    return d.strftime(fmt) if d else ""


# ---------------------------------------------------------------- заказ


def parse_order(comment, pattern: str, prefix: str = "S"):
    """Комментарий ФУЛ -> (номер или None, список всех найденных разных номеров)."""
    s = to_str(comment)
    rx = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё])" + pattern + r"(?!\d)", re.IGNORECASE)
    found = []
    for m in rx.finditer(s):
        num = prefix + m.group(1)
        if num not in found:
            found.append(num)
    if len(found) == 1:
        return found[0], found
    return None, found


def norm_order(value, pattern: str, prefix: str = "S"):
    """Номер заказа в реестре Cloud -> канонический вид или None."""
    num, _ = parse_order(value, pattern, prefix)
    return num


# ---------------------------------------------------------------- поставщик

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def supplier_key(name, legal_forms) -> str:
    """Ключ сравнения поставщиков: без регистра, знаков, лишних пробелов и правовых форм."""
    s = norm_text(name)
    s = _PUNCT.sub(" ", s).replace("_", " ")
    tokens = [t for t in s.split() if t]
    lf = set(legal_forms)
    core = [t for t in tokens if t not in lf]
    return " ".join(core or tokens)


# ---------------------------------------------------------------- способ доставки и страна


def norm_method(value, methods: dict, markers):
    """-> (канон или None, 'ok' | 'empty' | 'unknown')."""
    if is_empty(value, markers):
        return None, "empty"
    s = norm_text(value)
    s2 = _SPACES.sub(" ", s.replace("_", " ").replace("-", " ")).strip()
    for canon, aliases in methods.items():
        for a in aliases:
            a = norm_text(a)
            if s == a or s2 == _SPACES.sub(" ", a.replace("-", " ")):
                return canon, "ok"
    return None, "unknown"


def norm_country(value, countries: dict, markers):
    """-> (отображаемое значение или None, is_china: bool | None)."""
    if is_empty(value, markers):
        return None, None
    raw = to_str(value)
    s = norm_text(raw).strip(" .")
    china = {norm_text(a) for a in countries["china_aliases"]}
    if s in china:
        return raw, True
    return raw, False


# ---------------------------------------------------------------- ФИО и короткие имена


def split_fio(fio):
    """'Иванова Елена Петровна' -> ('иванова', 'е', 'п') — фамилия и инициалы для сравнения."""
    parts = [p for p in re.split(r"[\s.]+", norm_text(fio)) if p]
    if not parts:
        return "", "", ""
    surname = parts[0]
    first = parts[1][0] if len(parts) > 1 else ""
    patr = parts[2][0] if len(parts) > 2 else ""
    return surname, first, patr


def parse_short_name(value):
    """Короткое имя из Cloud -> (фамилия, [инициалы]).

    Поддерживает: 'Иванова Е.', 'Иванова Е.П.', 'Иванова Е. П.', 'Е. Иванова',
    'Е.П. Иванова', 'Иванова', 'Иванова Елена', 'иванова елена петровна'.
    """
    s = norm_text(value)
    if not s:
        return "", []
    # разделяем «Е.П.» на «Е. П.»
    s = re.sub(r"(\w)\.(?=\w)", r"\1. ", s)
    tokens = [t for t in s.split(" ") if t]
    words, initials = [], []
    for t in tokens:
        bare = t.strip(".,")
        if not bare:
            continue
        if t.endswith(".") and len(bare) <= 2 or len(bare) == 1:
            initials.append(bare[0])
        else:
            words.append(bare)
    if not words:
        return "", []
    surname = words[0]
    # Полные имя/отчество после фамилии превращаем в инициалы
    initials = [w[0] for w in words[1:]] + initials if len(words) > 1 else initials
    return surname, initials[:2]


def name_matches(short, fio) -> bool:
    surname, initials = parse_short_name(short)
    fs, ff, fp = split_fio(fio)
    if not surname or surname != fs:
        return False
    full = [ff, fp]
    for i, ini in enumerate(initials):
        if i >= len(full) or not full[i] or full[i] != ini:
            return False
    return True


def short_style(value) -> str:
    """Стиль записи логиста в Cloud: 'surname_i', 'surname_io', 'surname', 'full', 'other'."""
    s = to_str(value)
    if re.fullmatch(r"[^\s.]+\s+[^\s.]\.", s):
        return "surname_i"
    if re.fullmatch(r"[^\s.]+\s+[^\s.]\.\s?[^\s.]\.", s):
        return "surname_io"
    if re.fullmatch(r"[^\s.]+", s):
        return "surname"
    if re.fullmatch(r"[^\s.]+\s+[^\s.]{2,}\s+[^\s.]{2,}", s):
        return "full"
    return "other"


def format_short(fio, style: str) -> str:
    parts = [p for p in to_str(fio).split(" ") if p]
    if not parts:
        return ""
    surname = parts[0]
    first = parts[1][0].upper() + "." if len(parts) > 1 else ""
    patr = parts[2][0].upper() + "." if len(parts) > 2 else ""
    if style == "surname_io":
        return f"{surname} {first}{patr}".strip()
    if style == "surname":
        return surname
    if style == "full":
        return to_str(fio)
    return f"{surname} {first}".strip()
