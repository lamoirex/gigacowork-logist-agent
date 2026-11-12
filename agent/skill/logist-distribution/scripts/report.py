"""Запись результата: Excel-файл с листами и summary.json для модели.

Файл детерминирован: нет текущего времени и случайности, фиксированные
метаданные книги — повторный запуск даёт то же содержимое.
"""

from __future__ import annotations

import datetime as dt

import engine as E

FIXED_TS = dt.datetime(2000, 1, 1)
HEADER_FILL = "1F4E79"
UNDEF_FILL = "FCE4D6"
BLOCK_FILL = "F8CBAD"
CHECK_FILL = "FFF2CC"
DATE_FMT = "DD.MM.YYYY"
DATETIME_FMT = "DD.MM.YYYY HH:MM"


def _style_sheet(ws, widths, undefined):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor=HEADER_FILL)
    undef_fill = PatternFill("solid", fgColor=UNDEF_FILL)
    for cell in ws[1]:
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.value == undefined:
                cell.fill = undef_fill
            if isinstance(cell.value, dt.datetime):
                cell.number_format = DATETIME_FMT if (cell.value.hour or cell.value.minute) else DATE_FMT
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def write_excel(res: E.Result, path: str, eng: E.Engine, inputs_desc: list, warnings: list):
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    U = eng.U
    wb = openpyxl.Workbook()
    wb.properties.creator = "logist-distribution"
    wb.properties.created = FIXED_TS
    wb.properties.modified = FIXED_TS
    wb.properties.lastModifiedBy = "logist-distribution"

    # --- Сводка
    ws = wb.active
    ws.title = "Сводка"
    s = res.stats
    rows = [
        ("Показатель", "Значение"),
        ("ФУЛ в выгрузке", s["ful_total"]),
        ("Новые строки Cloud", s["new_rows"]),
        ("  из них с логистом", s["new_rows_with_logist"]),
        ("  из них без логиста (не определено)", s["new_rows_without_logist"]),
        ("Повторные отправки (заказ есть в Cloud)", s["repeats_cloud"]),
        ("Повторные отправки (дубль в пачке)", s["repeats_batch"]),
        ("Технические дубли строк", s["tech_duplicates"]),
        ("Возможные пропуски", s["possible_misses"]),
        ("ФУЛ без номера заказа", s["no_order"]),
        ("Требует внимания — всего", s["attention_total"]),
        ("  из них блокирующих", s["attention_blocking"]),
        ("Последняя запись Cloud (Дата получения УЛ)", res.last_cloud_date or U),
        ("", ""),
        ("Входные данные", ""),
    ]
    rows += [(role, desc) for role, desc in inputs_desc]
    if warnings:
        rows += [("", ""), ("Предупреждения", "")] + [("", w) for w in warnings]
    rows += [
        ("", ""),
        ("Правила", "см. лист «Журнал решений» и docs/rules.md; решения принимает скрипт, модель только пересказывает"),
    ]
    for r in rows:
        ws.append(list(r))
    _style_sheet(ws, [48, 90], U)

    # --- Новые строки Cloud (ровно 12 колонок по форме реестра)
    ws = wb.create_sheet("Новые строки Cloud")
    ws.append([res.cloud_titles[f] for f in res.cloud_order])
    for r in res.new_rows:
        ws.append([r[f] for f in res.cloud_order])
    _style_sheet(ws, [12, 28, 16, 18, 12, 14, 12, 16, 16, 14, 14, 36], U)

    # --- Повторные отправки
    ws = wb.create_sheet("Повторные отправки")
    ws.append(
        [
            "Заказ",
            "Номер ФУЛ",
            "Дата ФУЛ",
            "Источник повтора",
            "Поставщик",
            "Логист (Cloud)",
            "Статус (Cloud)",
            "Строка Cloud",
            "Текущий комментарий",
            "Комментарий для Cloud (итоговый)",
            "Дописать",
        ]
    )
    for d in res.decisions:
        if d.outcome == E.REPEAT_CLOUD:
            rp = next(x for x in res.repeats if x["d"] is d)
            cr = rp["cloud_row"]
            ws.append(
                [
                    d.ful.order,
                    d.ful.number or U,
                    d.ful.date or U,
                    "Заказ уже в Cloud",
                    cr.supplier or U,
                    cr.logist_raw or U,
                    cr.status or U,
                    cr.row,
                    cr.comment or "",
                    rp["full_comment"],
                    rp["add"],
                ]
            )
        elif d.outcome == E.REPEAT_BATCH:
            first = d.first
            where = "Новые строки Cloud" if first.outcome == E.NEW else "Возможные пропуски"
            ws.append(
                [
                    d.ful.order,
                    d.ful.number or U,
                    d.ful.date or U,
                    f"Дубль в пачке (первый ФУЛ {first.ful.number or U})",
                    d.ful.supplier or U,
                    eng.short(first.logist) if first.logist else U,
                    "",
                    f"→ {where}",
                    "",
                    first.new_comment,
                    d.new_comment,
                ]
            )
    _style_sheet(ws, [12, 14, 16, 26, 26, 16, 12, 18, 30, 40, 32], U)

    # --- Возможные пропуски
    ws = wb.create_sheet("Возможные пропуски")
    ws.append(
        ["Номер ФУЛ", "Дата ФУЛ", "Последняя запись Cloud", "Причина"]
        + [res.cloud_titles[f] for f in res.cloud_order]
        + ["Обоснование предложения"]
    )
    for m in res.misses:
        d = m["_d"]
        ws.append(
            [
                d.ful.number or U,
                d.ful.date or U,
                res.last_cloud_date or U,
                "Заказа нет в Cloud, хотя ФУЛ пришёл раньше последней записи. Решите, вносить ли строку.",
            ]
            + [m[f] for f in res.cloud_order]
            + [d.reason]
        )
    _style_sheet(ws, [14, 16, 16, 34] + [14] * 12 + [60], U)

    # --- Требует внимания
    ws = wb.create_sheet("Требует внимания")
    ws.append(["№", "Приоритет", "Номер ФУЛ", "Заказ", "Поставщик", "Что не так", "Что сделать"])
    for n, a in enumerate(res.attention, start=1):
        ws.append([n, a.priority, a.ful_number or "—", a.order or "—", a.supplier or "—", a.problem, a.action])
    _style_sheet(ws, [5, 12, 14, 12, 26, 70, 50], U)
    block, check = PatternFill("solid", fgColor=BLOCK_FILL), PatternFill("solid", fgColor=CHECK_FILL)
    for row in ws.iter_rows(min_row=2):
        row[1].fill = block if row[1].value == E.BLOCK else check

    # --- Журнал решений
    ws = wb.create_sheet("Журнал решений")
    ws.append(
        [
            "№",
            "Строка ФУЛ в файле",
            "Дата ФУЛ",
            "Номер ФУЛ",
            "Заказ",
            "Поставщик (1С)",
            "Способ доставки",
            "Страна",
            "Статус обработки",
            "Группа",
            "Логист",
            "Правило",
            "Обоснование",
            "Кандидаты группы (загрузка / недоступность)",
            "Комментарий для Cloud",
        ]
    )
    for n, d in enumerate(res.decisions, start=1):
        f = d.ful
        group = eng.group_title(d.group) if d.outcome in (E.NEW, E.POSSIBLE_MISS) else ""
        if d.outcome in (E.NEW, E.POSSIBLE_MISS):
            logist = eng.short(d.logist) if d.logist else U
        elif d.outcome == E.REPEAT_CLOUD:
            logist = d.cloud_row.logist_raw or U
        elif d.first is not None and d.first.logist is not None:
            logist = eng.short(d.first.logist)
        else:
            logist = ""
        ws.append(
            [
                n,
                f.row,
                f.date or (f.date_raw or U),
                f.number or U,
                f.order or U,
                f.supplier or U,
                f.method_raw or U,
                f.country or U,
                d.outcome,
                group,
                logist,
                d.rule,
                d.reason,
                d.candidates,
                d.new_comment,
            ]
        )
    _style_sheet(ws, [5, 8, 16, 14, 12, 26, 12, 12, 22, 18, 16, 28, 70, 50, 34], U)
    bold = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        row[8].font = bold

    # --- Сотрудники (как их понял агент)
    ws = wb.create_sheet("Сотрудники")
    ws.append(
        [
            "ФИО (1С)",
            "Код",
            "Должность",
            "Группа",
            "Распределяет заказы",
            "Доступен",
            "Причина",
            "Имя в Cloud",
            "В работе (Cloud)",
            "Назначено в пачке",
        ]
    )
    for e in sorted(res.employees, key=lambda e: e.sort_key):
        ws.append(
            [
                e.fio,
                e.code or "",
                e.position,
                eng.group_title(e.group) if e.group else "—",
                "нет" if e.excluded else "да",
                "да" if e.available else "нет",
                e.excluded or e.unavailable_reason or "",
                eng.short(e),
                e.load_cloud,
                e.load_batch,
            ]
        )
    _style_sheet(ws, [34, 10, 40, 24, 12, 10, 40, 18, 12, 12], U)

    wb.save(path)
