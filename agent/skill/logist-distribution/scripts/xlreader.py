"""Чтение выгрузок Excel и распознавание таблиц по заголовкам.

* лист выбирается по содержимому, а не по имени;
* строка заголовков ищется в первых HEADER_SCAN_ROWS строках;
* колонки ищутся по синонимам заголовков (регистр, пробелы, знаки, ё/е не важны);
* роль файла (ful / cloud / staff) определяется по набору найденных колонок.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from normalize import norm_header, to_str

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


# ---------------------------------------------------------------- распознавание


def _match_header_row(row, spec):
    """Сопоставить строку заголовков со спецификацией роли.

    Возвращает (columns, titles) — найденные поля. Синонимы проверяются по порядку,
    одна колонка не может быть отдана двум полям.
    """
    norm = [norm_header(v) for v in row]
    used, columns, titles = set(), {}, {}
    for fld, fspec in spec.items():
        for syn in fspec["synonyms"]:
            key = norm_header(syn)
            if not key:
                continue
            for idx, h in enumerate(norm):
                if idx not in used and h == key:
                    columns[fld], titles[fld] = idx, to_str(row[idx])
                    used.add(idx)
                    break
            if fld in columns:
                break
    return columns, titles


def _candidates(sheet, headers_spec):
    """Все варианты (роль, строка заголовков) для листа, где найдены все обязательные колонки."""
    out = []
    for r_idx, row in enumerate(sheet.rows[:HEADER_SCAN_ROWS]):
        if not row or all(to_str(v) == "" for v in row):
            continue
        for role in ROLES:
            spec = headers_spec[role]
            columns, titles = _match_header_row(row, spec)
            required = [f for f, s in spec.items() if s["required"]]
            if all(f in columns for f in required):
                data_rows = sum(1 for rr in sheet.rows[r_idx + 1 :] if rr and any(to_str(v) != "" for v in rr))
                score = len(columns) / len(spec)
                out.append((role, score, r_idx, columns, titles, data_rows))
    return out


def _best_partial(sheets, spec):
    """Для сообщения об ошибке: лучший неполный кандидат роли и чего в нём не хватает."""
    best = None
    for sh in sheets:
        for r_idx, row in enumerate(sh.rows[:HEADER_SCAN_ROWS]):
            if not row:
                continue
            columns, _ = _match_header_row(row, spec)
            if best is None or len(columns) > best[0]:
                missing = [spec[f]["synonyms"][0] for f, s in spec.items() if s["required"] and f not in columns]
                found = [to_str(v) for v in row if to_str(v)]
                best = (len(columns), sh, missing, found)
    return best


def detect_tables(paths, headers_spec):
    """Прочитать файлы и найти три таблицы. Возвращает (tables: dict role->Table, warnings)."""
    sheets = []
    for p in paths:
        sheets.extend(read_workbook(p))

    cands = []  # (role, score, data_rows, sheet_idx, r_idx, columns, titles)
    for s_idx, sh in enumerate(sheets):
        for role, score, r_idx, columns, titles, data_rows in _candidates(sh, headers_spec):
            cands.append((role, score, data_rows, s_idx, r_idx, columns, titles))

    warnings, tables, used_sheets = [], {}, set()
    # Сначала самые уверенные совпадения: высокий score, затем больше строк данных.
    cands.sort(key=lambda c: (-c[1], -c[2], c[3], c[4]))
    for role, score, data_rows, s_idx, r_idx, columns, titles in cands:
        if role in tables or s_idx in used_sheets:
            continue
        sh = sheets[s_idx]
        others = [c for c in cands if c[0] == role and c[3] != s_idx and c[3] not in used_sheets]
        if others:
            o = sheets[others[0][3]]
            warnings.append(
                f"{ROLE_TITLES[role]}: подходит несколько листов; взят «{sh.name}» "
                f"({os.path.basename(sh.file)}), не использован «{o.name}» ({os.path.basename(o.file)})."
            )
        header_order = [f for f, _ in sorted(columns.items(), key=lambda kv: kv[1])]
        t = Table(role, sh.file, sh.name, r_idx + 1, columns, titles, header_order)
        for rr_idx, row in enumerate(sh.rows[r_idx + 1 :], start=r_idx + 2):
            if not row or all(to_str(v) == "" for v in row):
                continue
            rec = {"_row": rr_idx}
            for fld, c in columns.items():
                rec[fld] = row[c] if c < len(row) else None
            t.records.append(rec)
        tables[role] = t
        used_sheets.add(s_idx)

    missing_roles = [r for r in ROLES if r not in tables]
    if missing_roles:
        msgs = []
        for role in missing_roles:
            partial = _best_partial([s for i, s in enumerate(sheets) if i not in used_sheets], headers_spec[role])
            if partial and partial[0] > 0:
                _, sh, missing, found = partial
                msgs.append(
                    f"{ROLE_TITLES[role]}: ближе всего лист «{sh.name}» ({os.path.basename(sh.file)}), "
                    f"но не найдены колонки: {', '.join(missing)}. Найдены: {', '.join(found[:20])}."
                )
            else:
                msgs.append(f"{ROLE_TITLES[role]}: не найден ни в одном приложенном файле.")
        raise InputError("Не удалось распознать входные файлы.\n" + "\n".join(msgs))
    return tables, warnings
