"""Общие константы и построители тестовых книг Excel."""

import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "agent", "skill", "logist-distribution")
SCRIPTS = os.path.join(SKILL, "scripts")
DATASETS = os.path.join(ROOT, "testdata", "datasets")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from pathlib import Path

import openpyxl

import normalize as N

RULES = json.loads(Path(SKILL, "references", "rules.json").read_text(encoding="utf-8"))
COUNTRIES = json.loads(Path(SKILL, "references", "countries.json").read_text(encoding="utf-8"))
MARKERS = {N.norm_text(m) for m in RULES["empty_markers"]}


def write_wb(path, sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


STAFF = [
    ["Наименование", "Текущее состояние", "Должность", "Код"],
    ["Орлова Марина Викторовна", "Работает", "Специалист по авиаперевозкам", "1"],
    ["Лебедев Артём Игоревич", "Работает", "Специалист по авиаперевозкам", "2"],
    ["Захарова Ирина Олеговна", "Отпуск", "Специалист по авиаперевозкам", "3"],
    ["Ким Денис Сергеевич", "Работает", "Специалист по работе с ВЭД документацией", "4"],
    ["Громов Виктор", "Работает", "Руководитель группы авиаперевозок", "5"],
    ["Соколов Игорь", "Уволен", "Специалист по мультимодальным перевозкам", "6"],
]
CLOUD_H = [
    "Заказ",
    "Поставщик",
    "Бренд",
    "Логист",
    "статус",
    "дата подтверждения",
    "cut-off",
    "Дата получения УЛ",
    "Страна отправления",
    "Способ доставки",
    "Документы получены",
    "Комментарий",
]
FUL_H = [
    "Дата",
    "Cut-off date",
    "Номер",
    "Статус",
    "Комментарий",
    "Способ доставки",
    "Бизнес единица",
    "Логист",
    "Контрагент",
    "Страна",
]


def cloud_row(order, supplier, logist, status="в работе", date=dt.datetime(2026, 11, 1)):
    return [order, supplier, "B", logist, status, None, None, date, "Китай", "air", None, None]


def ful_row(num, order, supplier, method="air", country="Китай", date=dt.datetime(2026, 11, 10, 9)):
    return [
        date,
        dt.datetime(2026, 12, 1),
        num,
        "Проведен",
        f"Номер: {order} от 01.11.2026",
        method,
        "B",
        None,
        supplier,
        country,
    ]
