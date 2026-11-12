"""Чтение выгрузок Excel и распознавание таблиц по заголовкам.

* лист выбирается по содержимому, а не по имени;
* строка заголовков ищется в первых HEADER_SCAN_ROWS строках;
* колонки ищутся по синонимам заголовков (регистр, пробелы, знаки, ё/е не важны);
* роль файла (ful / cloud / staff) определяется по набору найденных колонок.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

HEADER_SCAN_ROWS = 30
ROLES = ("ful", "cloud", "staff")
ROLE_TITLES = {"ful": "Выгрузка ФУЛ (1С)", "cloud": "Реестр Cloud", "staff": "Сотрудники (1С)"}


class InputError(Exception):
    """Ошибка входных данных, понятная пользователю."""


@dataclass
class Sheet:
    file: str
    name: str
    rows: list  # list[list[value]]


@dataclass
class Table:
    role: str
    file: str
    sheet: str
    header_row: int  # 1-based номер строки заголовков
    columns: dict  # field -> индекс колонки (0-based)
    titles: dict  # field -> заголовок как в файле
    header_order: list  # поля в порядке колонок файла
    records: list = field(default_factory=list)  # list[dict]: field -> value, '_row' -> номер строки

    def describe(self) -> str:
        return f"{os.path.basename(self.file)} / лист «{self.sheet}», заголовки в строке {self.header_row}"


# ---------------------------------------------------------------- чтение файлов


def _read_xlsx(path):
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # повреждённый файл, не Excel и т.п.
        raise InputError(f"Не удалось открыть {os.path.basename(path)}: {exc}") from exc
    sheets = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        sheets.append(Sheet(path, ws.title, rows))
    wb.close()
    return sheets


def _read_xls(path):
    try:
        import xlrd
    except ImportError as exc:
        raise InputError("Для чтения .xls нужен пакет xlrd. Сохраните файл как .xlsx.") from exc
    try:
        book = xlrd.open_workbook(path)
    except Exception as exc:
        raise InputError(f"Не удалось открыть {os.path.basename(path)}: {exc}") from exc
    sheets = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                else:
                    row.append(cell.value)
            rows.append(row)
        sheets.append(Sheet(path, sh.name, rows))
    return sheets


def read_workbook(path):
    if not os.path.isfile(path):
        raise InputError(f"Файл не найден: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return _read_xlsx(path)
    if ext == ".xls":
        return _read_xls(path)
    raise InputError(f"Неподдерживаемый формат {os.path.basename(path)}: нужен .xlsx или .xls")
