#!/usr/bin/env python3
"""Генератор синтетических данных и эталона для агента распределения ФУЛ.

Создаёт три набора в testdata/datasets/:

  ds1_base    — основной набор: 28 ФУЛ, 50 строк Cloud, 11 сотрудников, все ловушки из ТЗ;
  ds2_format  — те же данные в «чужом» формате: другие имена листов, шапка над таблицей,
                переставленные и лишние колонки, синонимы заголовков, даты строками,
                сотрудники в .xls (если доступен LibreOffice). Эталон тот же, что у ds1;
  ds3_edge    — дополнительные сложные случаи: однофамильцы, кириллическая «С» в номере,
                несколько номеров в комментарии, незнакомый способ доставки, вся авиагруппа
                недоступна, ничьи по загрузке, похожие названия поставщиков.

Эталон (expected.json / expected.xlsx) задан ВРУЧНУЮ в этом файле по каждому ФУЛ —
он не вычисляется движком агента, поэтому проверка не замкнута сама на себя.

Запуск:  python3 testdata/generate.py  [--out testdata/datasets]
Зависимости: openpyxl. Результат детерминирован.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile

import openpyxl
from openpyxl.styles import Font

U = "не определено"
D = dt.datetime

NEW, REP_C, REP_B, DUP, MISS, NOORD = (
    "Новый",
    "Повторная отправка (заказ есть в Cloud)",
    "Повторная отправка (дубль в пачке)",
    "Технический дубль строки",
    "Возможный пропуск",
    "Нет номера заказа",
)
AIR, VED, MULTI = "Авиаперевозки", "ВЭД", "Мультимодальные перевозки"

FUL_HEADERS = [
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
CLOUD_HEADERS = [
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
STAFF_HEADERS = ["Наименование", "Текущее состояние", "Должность", "Код"]

POS_AIR = "Специалист по авиаперевозкам"
POS_VED = "Специалист по работе с ВЭД документацией"
POS_MULTI = "Специалист по мультимодальным перевозкам"


def rep(date):
    return f"Повторная отправка ФУЛа, {date}"


# =====================================================================  DS1

DS1_STAFF = [
    ("Орлова Марина Викторовна", "Работает", POS_AIR, "1001"),
    ("Лебедев Артём Игоревич", "Работает", POS_AIR, "1002"),
    ("Захарова Ирина Олеговна", "Ежегодный оплачиваемый отпуск", POS_AIR, "1003"),
    ("Ким Денис Сергеевич", "Работает", POS_VED, "1004"),
    ("Белова Ольга Андреевна", "Уволен", POS_VED, "1005"),
    ("Тихонов Павел Романович", "Работает", POS_VED, "1006"),
    ("Соколов Игорь Петрович", "Отпуск по уходу за ребенком", POS_MULTI, "1007"),
    ("Миронова Анна Сергеевна", "Уволен", POS_MULTI, "1008"),
    ("Фёдорова Елена Ивановна", "Работает", "Ассистент отдела логистики", "1009"),
    ("Громов Виктор Алексеевич", "Работает", "Руководитель группы авиаперевозок", "1010"),
    ("Назарова Юлия Петровна", "Работает", "Руководитель группы мультимодальных перевозок", "1011"),
]

# Реестр Cloud: (поставщик, логист, статус, страна, способ, бренд, [дата УЛ])
_C = []


def _cl(supplier, logist, status, country, method, brand, n=1, date=None, order=None, comment=""):
    for _ in range(n):
        _C.append(
            dict(
                supplier=supplier,
                logist=logist,
                status=status,
                country=country,
                method=method,
                brand=brand,
                date=date,
                order=order,
                comment=comment,
            )
        )


_cl("Aurora Garments Co., Ltd", "Белова О.", "в работе", "Китай", "sea", "Северный ветер", 2)
_cl("Aurora Garments Co., Ltd", "Белова О.", "confirm", "Китай", "sea", "Северный ветер", 2)
_cl("Aurora Garments Co., Ltd", "Ким Д.", "confirm", "Китай", "sea", "Северный ветер", 1)
_cl("Silk Road Apparel", "Ким Д.", "в работе", "Китай", "sea", "Urban Fox", 1)
_cl("Silk Road Apparel", "Ким Д.", "confirm", "Китай", "railway", "Urban Fox", 2)
_cl("Nordwind Textil GmbH", "Захарова И.", "в работе", "Германия", "air", "Mona Bella", 2)
_cl("Nordwind Textil GmbH", "Захарова И.", "confirm", "Германия", "air", "Mona Bella", 1)
_cl("Nordwind Textil GmbH", "Лебедев А.", "confirm", "Германия", "air", "Mona Bella", 1)
_cl(
    "Lotus Fashion Ltd",
    "Орлова М.",
    "в работе",
    "Вьетнам",
    "air",
    "Lumi Kids",
    1,
    order="S0003050",
    date=D(2026, 10, 28),
)
_cl("Lotus Fashion Ltd", "Орлова М.", "confirm", "Вьетнам", "air", "Lumi Kids", 1)
_cl("Golden Thread Co., Ltd", "Лебедев А.", "в работе", "Китай", "air", "Terra Sport", 1)
_cl("Golden Thread Co., Ltd", "Лебедев А.", "confirm", "Китай", "air", "Terra Sport", 2)
_cl("Golden Thread Co., Ltd", "Громов В.", "confirm", "Китай", "air", "Terra Sport", 1)
_cl(
    "Tiber Moda S.r.l.", "Орлова М.", "confirm", "Италия", "air", "Mona Bella", 1, date=D(2026, 9, 15), order="S0003120"
)
_cl("Tiber Moda S.r.l.", "Лебедев А.", "confirm", "Италия", "air", "Mona Bella", 1, date=D(2026, 10, 20))
_cl("Bosphorus Moda", "Соколов И.", "в работе", "Турция", "truck", "Северный ветер", 2)
_cl("Ganges Knitwear Pvt", "Миронова А.", "в работе", "Индия", "sea", "Urban Fox", 1)
_cl("Ganges Knitwear Pvt", "Соколов И.", "confirm", "Индия", "sea", "Urban Fox", 1)
# Заполнители — другие поставщики, формируют загрузку «в работе»
_cl("Maple Leaf Outerwear", "Орлова М.", "в работе", "Канада", "air", "Terra Sport", 3)
_cl("Kashmir Looms", "Лебедев А.", "в работе", "Индия", "air", "Lumi Kids", 1)
_cl("Kashmir Looms", "Захарова И.", "в работе", "Индия", "air", "Lumi Kids", 1)
_cl("Dragon Pearl Silk", "Ким Д.", "в работе", "Китай", "sea", "Mona Bella", 1, order="S0003077")
_cl("Dragon Pearl Silk", "Ким Д.", "в работе", "Китай", "sea", "Mona Bella", 1)
_cl("Jade Mountain Knit", "Тихонов П.", "в работе", "Китай", "railway", "Urban Fox", 1)
_cl("Jade Mountain Knit", "Тихонов П.", "confirm", "Китай", "railway", "Urban Fox", 2)
_cl("Baltic Linen", "Соколов И.", "confirm", "Литва", "truck", "Северный ветер", 3)
_cl("Baltic Linen", "Миронова А.", "confirm", "Литва", "truck", "Северный ветер", 2)
_cl("Anatolia Denim", "Петров С.", "confirm", "Турция", "truck", "Urban Fox", 2)
_cl("Maple Leaf Outerwear", "Орлова М.", "confirm", "Канада", "air", "Terra Sport", 3)
_cl("Kashmir Looms", "Лебедев А.", "confirm", "Индия", "air", "Lumi Kids", 2)
_cl("Dragon Pearl Silk", "Тихонов П.", "confirm", "Китай", "sea", "Mona Bella", 2)
_cl("Saigon Stitch", "Орлова М.", "confirm", "Вьетнам", "air", "Lumi Kids", 2)
_cl("Saigon Stitch", "Лебедев А.", "confirm", "Вьетнам", "air", "Lumi Kids", 1, date=D(2026, 11, 6))
DS1_CLOUD = list(_C)

# ФУЛ: дата, номер УЛ, заказ-комментарий, способ, бренд, поставщик, страна, cut-off  + эталон
# Эталон: outcome, group, logist (как в Cloud) / U, comment, trap
F = []


def ful(
    date,
    num,
    comment,
    method,
    brand,
    supplier,
    country,
    cutoff,
    *,
    outcome,
    group="",
    logist="",
    comment_out="",
    trap="",
    status="Проведен",
    row_fields=None,
):
    F.append(
        dict(
            date=date,
            num=num,
            comment=comment,
            method=method,
            brand=brand,
            supplier=supplier,
            country=country,
            cutoff=cutoff,
            status=status,
            exp=dict(outcome=outcome, group=group, logist=logist, comment=comment_out, trap=trap, row=row_fields),
        )
    )


def n(order, d):
    return f"Номер: {order} от {d}"


CO = D(2026, 12, 15)
ful(
    D(2026, 11, 9, 8, 15),
    "УЛ-26-0101",
    n("S0004101", "02.11.2026"),
    "sea",
    "Urban Fox",
    "Silk Road Apparel",
    "Китай",
    CO,
    outcome=NEW,
    group=VED,
    logist="Ким Д.",
    comment_out=rep("10.11.2026"),
    trap="История: Ким вёл 3 из 3 заказов поставщика; повтор этого заказа ниже в пачке → комментарий",
)
ful(
    D(2026, 11, 9, 9, 0),
    "УЛ-26-0102",
    n("S0004102", "02.11.2026"),
    "sea",
    "Северный ветер",
    "Aurora Garments Co., Ltd",
    "China",
    CO,
    outcome=NEW,
    group=VED,
    logist="Тихонов П.",
    trap="Предпочтительный по истории (Белова, 4 из 5) уволена → наименее загруженный в ВЭД (Тихонов 1 < Ким 4)",
)
ful(
    D(2026, 11, 9, 10, 30),
    "УЛ-26-0103",
    n("S0004103", "03.11.2026"),
    "air",
    "Mona Bella",
    "Nordwind Textil GmbH",
    "Германия",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Предпочтительный (Захарова, 3 из 4) в отпуске → наименее загруженный в авиа (Лебедев 2 < Орлова 4)",
)
ful(
    D(2026, 11, 9, 11, 0),
    "УЛ-26-0104",
    n("S0003050", "20.10.2026"),
    "air",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=REP_C,
    logist="Орлова М.",
    comment_out=rep("09.11.2026"),
    trap="Заказ уже в Cloud → строка не создаётся, только комментарий о повторе",
)
ful(
    D(2026, 11, 9, 12, 0),
    "УЛ-26-0105",
    n("S0004105", "04.11.2026"),
    "truck",
    "Северный ветер",
    "Bosphorus Moda",
    "Турция",
    CO,
    outcome=NEW,
    group=MULTI,
    logist=U,
    trap="Вся мультимодальная группа недоступна (отпуск, уволена; руководитель не распределяет) → логист не определён, в другую группу не передаётся",
)
ful(
    D(2026, 11, 9, 13, 20),
    "УЛ-26-0106",
    n("S0004106", "05.11.2026"),
    "sea",
    "Lumi Kids",
    "Pearl River Textiles",
    "Гонконг",
    CO,
    outcome=NEW,
    group=VED,
    logist="Тихонов П.",
    trap="Новый поставщик; Гонконг = Китай → ВЭД; наименее загруженный (Тихонов 2 < Ким 4)",
)
ful(
    D(2026, 11, 9, 14, 0),
    "УЛ-26-0107",
    n("S0004107", "05.11.2026"),
    "air",
    "Terra Sport",
    "Andes Wool S.A.C.",
    "Перу",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Новый поставщик → наименее загруженный (Лебедев 3 < Орлова 4)",
)
ful(
    D(2026, 11, 9, 15, 10),
    "УЛ-26-0108",
    "Номер:  от 08.11.2026",
    "air",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=NOORD,
    trap="Нет номера заказа → строка Cloud не создаётся, «Требует внимания»",
)
ful(
    D(2026, 11, 9, 16, 0),
    "УЛ-26-0109",
    n("S0004109", "06.11.2026"),
    "air",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Орлова М.",
    trap="История: Орлова вела 2 из 2",
)
ful(
    D(2026, 11, 10, 8, 30),
    "УЛ-26-0110",
    n("S0004110", "06.11.2026"),
    "air",
    "Terra Sport",
    "GOLDEN  THREAD CO LTD",
    "China",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Поставщик записан иначе, чем в Cloud («Golden Thread Co., Ltd») → тот же поставщик, история Лебедева; руководитель Громов в истории не учитывается",
    row_fields={"supplier": "Golden Thread Co., Ltd"},
)
ful(
    D(2026, 11, 10, 9, 0),
    "УЛ-26-0111",
    n("S0004111", "07.11.2026"),
    "",
    "Urban Fox",
    "Silk Road Apparel",
    "Китай",
    CO,
    outcome=NEW,
    group=U,
    logist=U,
    trap="Нет способа доставки → группа и логист «не определено»",
)
ful(
    D(2026, 11, 10, 9, 30),
    "УЛ-26-0112",
    n("S0004112", "07.11.2026"),
    "sea",
    "Северный ветер",
    "Aurora Garments Co., Ltd",
    "",
    CO,
    outcome=NEW,
    group=U,
    logist=U,
    trap="Нет страны при sea → группа и логист «не определено»",
)
ful(
    D(2026, 11, 10, 10, 0),
    "УЛ-26-0113",
    n("S0004113", "07.11.2026"),
    "air",
    "Terra Sport",
    "Andes Wool S.A.C.",
    "",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Нет страны, но air → группа определяется; один поставщик — один логист (закреплён Лебедев; без закрепления выбрали бы Орлову — меньше назначений в пачке); страна «не определено»",
)
ful(
    D(2026, 11, 10, 10, 45),
    "УЛ-26-0114",
    n("S0004101", "02.11.2026"),
    "sea",
    "Urban Fox",
    "Silk Road Apparel",
    "Китай",
    CO,
    outcome=REP_B,
    logist="Ким Д.",
    comment_out=rep("10.11.2026"),
    trap="Повтор внутри пачки (тот же заказ, что УЛ-26-0101) → новая строка не создаётся",
)
ful(
    D(2026, 11, 10, 11, 30),
    "УЛ-26-0115",
    n("S0004115", "08.11.2026"),
    "sea",
    "Urban Fox",
    "Silk Road Apparel",
    "Китай",
    CO,
    outcome=NEW,
    group=VED,
    logist="Ким Д.",
    trap="Один поставщик — один логист в пачке (Ким)",
)
ful(
    D(2026, 11, 10, 12, 0),
    "УЛ-26-0116",
    n("S0003120", "10.09.2026"),
    "air",
    "Mona Bella",
    "Tiber Moda S.r.l.",
    "Италия",
    CO,
    outcome=REP_C,
    logist="Орлова М.",
    comment_out=rep("10.11.2026"),
    trap="Повтор по заказу со статусом confirm → комментарий + «Требует внимания»",
)
ful(
    D(2026, 11, 10, 13, 0),
    "УЛ-26-0117",
    n("S0004117", "08.11.2026"),
    "air",
    "Mona Bella",
    "Tiber Moda S.r.l.",
    "Италия",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Ничья по истории (Орлова 1, Лебедев 1) → у Лебедева последний заказ позже (20.10 > 15.09)",
)
ful(
    D(2026, 11, 10, 14, 0),
    "УЛ-26-0118",
    n("S0004118", "09.11.2026"),
    "sea",
    "Urban Fox",
    "Ganges Knitwear Pvt",
    "Индия",
    CO,
    outcome=NEW,
    group=MULTI,
    logist=U,
    trap="Мультимодальная группа недоступна",
)
ful(
    D(2026, 11, 5, 17, 40),
    "УЛ-26-0090",
    n("S0004090", "30.10.2026"),
    "air",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=MISS,
    group=AIR,
    logist="Орлова М.",
    trap="ФУЛ от 05.11 раньше последней записи Cloud (06.11), заказа в Cloud нет → возможный пропуск, в новые строки не попадает",
)
ful(
    D(2026, 11, 4, 9, 5),
    "УЛ-26-0086",
    n("S0003077", "25.10.2026"),
    "sea",
    "Mona Bella",
    "Dragon Pearl Silk",
    "Китай",
    CO,
    outcome=REP_C,
    logist="Ким Д.",
    comment_out=rep("04.11.2026"),
    trap="Старая дата, но заказ есть в Cloud → это повтор, а не пропуск",
)
ful(
    D(2026, 11, 10, 15, 0),
    "УЛ-26-0121",
    n("S0004121", "09.11.2026"),
    "railway",
    "Северный ветер",
    "Bosphorus Moda",
    "Турция",
    CO,
    outcome=NEW,
    group=MULTI,
    logist=U,
    trap="Мультимодальная группа недоступна",
)
ful(
    D(2026, 11, 11, 8, 0),
    "УЛ-26-0122",
    n("S0004122", "09.11.2026"),
    "air",
    "Mona Bella",
    "Nordwind Textil GmbH",
    "Германия",
    CO,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Один поставщик — один логист (Лебедев закреплён в пачке)",
)
ful(
    D(2026, 11, 11, 9, 0),
    "УЛ-26-0123",
    n("S0004123", "10.11.2026"),
    "railway",
    "Lumi Kids",
    "Pearl River Textiles",
    "Китай",
    CO,
    outcome=NEW,
    group=VED,
    logist="Тихонов П.",
    trap="Один поставщик — один логист (Тихонов)",
)
ful(
    D(2026, 11, 11, 9, 0),
    "УЛ-26-0123",
    n("S0004123", "10.11.2026"),
    "railway",
    "Lumi Kids",
    "Pearl River Textiles",
    "Китай",
    CO,
    outcome=DUP,
    logist="Тихонов П.",
    trap="Точная копия строки выгрузки → учитывается один раз, не повтор",
)
ful(
    D(2026, 11, 11, 10, 0),
    "УЛ-26-0125",
    n("S0004125", "10.11.2026"),
    "sea",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=NEW,
    group=MULTI,
    logist=U,
    trap="Поставщик с заказами разных групп в пачке (air и sea) → группа по правилу, «Требует внимания»",
)
ful(
    D(2026, 11, 11, 11, 0),
    "УЛ-26-0126",
    n("S0004126", "10.11.2026"),
    "direct truck",
    "Urban Fox",
    "silk road  apparel ",
    "КНР",
    CO,
    outcome=NEW,
    group=VED,
    logist="Ким Д.",
    trap="Регистр и пробелы в названии поставщика; КНР = Китай; direct truck → не air",
    row_fields={"supplier": "Silk Road Apparel"},
)
ful(
    D(2026, 11, 11, 12, 0),
    "УЛ-26-0127",
    n("S0003050", "20.10.2026"),
    "air",
    "Lumi Kids",
    "Lotus Fashion Ltd",
    "Вьетнам",
    CO,
    outcome=REP_C,
    logist="Орлова М.",
    comment_out=rep("11.11.2026"),
    trap="Второй повтор того же заказа Cloud → итоговый комментарий содержит обе даты",
)
ful(
    D(2026, 11, 11, 13, 0),
    "УЛ-26-0128",
    n("S0004128", "10.11.2026"),
    "air",
    "Terra Sport",
    "Andes Wool S.A.C.",
    "Перу",
    None,
    outcome=NEW,
    group=AIR,
    logist="Лебедев А.",
    trap="Нет cut-off → «не определено» в строке; логист по закреплению",
)
DS1_FUL = list(F)


# =====================================================================  DS3 (сложные случаи)

DS3_STAFF = [
    ("Иванова Елена Петровна", "Работает", POS_VED, "2001"),
    ("Иванова Екатерина Андреевна", "Работает", POS_VED, "2002"),
    ("Смирнов Олег Валерьевич", "Работает", POS_VED, "2003"),
    ("Кузнецова Дарья Ильинична", "Больничный", POS_MULTI, "2004"),
    ("Ахмедов Руслан Тимурович", "Работает", POS_MULTI, "2005"),
    ("Борисов Глеб Андреевич", "Работает", POS_MULTI, "2006"),
    ("Волкова Ксения Юрьевна", "Отпуск без сохранения заработной платы", POS_AIR, "2007"),
    ("Ершов Николай Петрович", "Уволен", POS_AIR, "2008"),
    ("Попова Алина Сергеевна", "Работает", "Помощник специалиста по авиаперевозкам", "2009"),
    ("Сергеев Максим Олегович", "Работает", "Начальник отдела логистики", "2010"),
]
_C.clear()
_cl("Harbin Wool Factory", "Иванова Е.", "confirm", "Китай", "railway", "Северный ветер", 3)  # неоднозначно
_cl("Harbin Wool Factory", "Смирнов О.", "confirm", "Китай", "railway", "Северный ветер", 1)
_cl("Shenzhen Smart Knit", "Иванова Е.П.", "в работе", "Китай", "sea", "Urban Fox", 2)  # однозначно
_cl("Shenzhen Smart Knit", "Е. Иванова", "confirm", "Китай", "sea", "Urban Fox", 1)  # неоднозначно
_cl("Ningbo Basic Tee", "Иванова Е.А.", "в работе", "Китай", "sea", "Lumi Kids", 1)
_cl("Ningbo Basic Tee", "Смирнов О.", "в работе", "Китай", "sea", "Lumi Kids", 2)
_cl("Istanbul Triko", "Кузнецова Д.", "в работе", "Турция", "truck", "Mona Bella", 3)
_cl("Istanbul Triko", "Ахмедов Р.", "confirm", "Турция", "truck", "Mona Bella", 1)
_cl("Tashkent Cotton", "Ахмедов Р.", "в работе", "Узбекистан", "railway", "Terra Sport", 2)
_cl("Tashkent Cotton", "Борисов Г.", "в работе", "Узбекистан", "railway", "Terra Sport", 1)
_cl("Bishkek Sew", "Борисов Г.", "в работе", "Киргизия", "truck", "Urban Fox", 1)
_cl("Milano Pelle", "Волкова К.", "в работе", "Италия", "air", "Mona Bella", 2)
_cl("Milano Pelle", "Ершов Н.", "confirm", "Италия", "air", "Mona Bella", 2)
_cl("Aurora Garments Co., Ltd", "Смирнов О.", "confirm", "Китай", "sea", "Северный ветер", 2, date=D(2026, 11, 2))
DS3_CLOUD = list(_C)
# Загрузка «в работе»: Иванова Е.П. 2, Иванова Е.А. 1, Смирнов 2, Кузнецова 3, Ахмедов 2, Борисов 2

F.clear()
CO3 = D(2027, 1, 20)
ful(
    D(2026, 11, 10, 9, 0),
    "PL-3001",
    n("S0005001", "05.11.2026"),
    "railway",
    "Северный ветер",
    "Harbin Wool Factory",
    "Китай",
    CO3,
    outcome=NEW,
    group=VED,
    logist="Смирнов О.",
    trap="«Иванова Е.» в Cloud подходит двум Ивановым → история по ней не засчитывается; остаётся Смирнов (1)",
)
ful(
    D(2026, 11, 10, 9, 30),
    "PL-3002",
    n("S0005002", "05.11.2026"),
    "sea",
    "Urban Fox",
    "Shenzhen Smart Knit",
    "Китай",
    CO3,
    outcome=NEW,
    group=VED,
    logist="Иванова Е.П.",
    trap="«Иванова Е.П.» сопоставлена однозначно (2), «Е. Иванова» — неоднозначно и не учтена",
)
ful(
    D(2026, 11, 10, 10, 0),
    "PL-3003",
    "Номер: С0005003 от 06.11.2026",
    "sea",
    "Lumi Kids",
    "Ningbo Basic Tee",
    "Китай",
    CO3,
    outcome=NEW,
    group=VED,
    logist="Смирнов О.",
    trap="Кириллическая «С» в номере заказа → S0005003; история: Смирнов 2 > Иванова Е.А. 1",
)
ful(
    D(2026, 11, 10, 10, 30),
    "PL-3004",
    "Номер: S0005004 от 06.11.2026; Номер: S0005005 от 06.11.2026",
    "sea",
    "Lumi Kids",
    "Ningbo Basic Tee",
    "Китай",
    CO3,
    outcome=NOORD,
    trap="Два разных номера заказа в комментарии → не угадываем, строка не создаётся",
)
ful(
    D(2026, 11, 10, 11, 0),
    "PL-3006",
    n("S0005006", "07.11.2026"),
    "courier",
    "Mona Bella",
    "Milano Pelle",
    "Италия",
    CO3,
    outcome=NEW,
    group=U,
    logist=U,
    trap="Незнакомый способ доставки «courier» → группа не определена",
)
ful(
    D(2026, 11, 10, 11, 30),
    "PL-3007",
    n("S0005007", "07.11.2026"),
    "air",
    "Mona Bella",
    "Milano Pelle",
    "Италия",
    CO3,
    outcome=NEW,
    group=AIR,
    logist=U,
    trap="Вся авиагруппа недоступна (отпуск без сохранения, уволен); помощник специалиста и начальник не распределяют",
)
ful(
    D(2026, 11, 10, 12, 0),
    "PL-3008",
    n("S0005008", "07.11.2026"),
    "truck",
    "Mona Bella",
    "Istanbul Triko",
    "Турция",
    CO3,
    outcome=NEW,
    group=MULTI,
    logist="Ахмедов Р.",
    trap="Предпочтительная Кузнецова (3) на больничном (незнакомое состояние = недоступна) → ничья по загрузке Ахмедов 2 = Борисов 2 → по алфавиту Ахмедов",
)
ful(
    D(2026, 11, 10, 12, 30),
    "PL-3009",
    n("S0005009", "08.11.2026"),
    "railway",
    "Terra Sport",
    "Samarkand Silk",
    "Узбекистан",
    CO3,
    outcome=NEW,
    group=MULTI,
    logist="Борисов Г.",
    trap="Новый поставщик; загрузка с учётом пачки: Ахмедов 3 > Борисов 2",
)
ful(
    D(2026, 11, 10, 13, 0),
    "PL-3010",
    n("S0005010", "08.11.2026"),
    "sea",
    "Северный ветер",
    "Aurora Garment",
    "Hong Kong SAR",
    CO3,
    outcome=NEW,
    group=VED,
    logist="Иванова Е.А.",
    trap="Похоже на «Aurora Garments Co., Ltd», но не совпадает → обрабатывается как новый, «Требует внимания»; загрузка Иванова Е.А. 1 < Смирнов 4 < Иванова Е.П. 3",
)
ful(
    D(2026, 11, 10, 13, 30),
    "PL-3011",
    n("S0005011", "08.11.2026"),
    "Sea",
    "Terra Sport",
    "Tashkent Cotton",
    "Uzbekistan",
    CO3,
    outcome=NEW,
    group=MULTI,
    logist="Ахмедов Р.",
    trap="История: Ахмедов 2 > Борисов 1; регистр способа доставки",
)
ful(
    D(2026, 11, 10, 14, 0),
    "PL-3012",
    n("S0005012", "09.11.2026"),
    "truck",
    "Urban Fox",
    "Bishkek Sew",
    "-",
    CO3,
    outcome=NEW,
    group=U,
    logist=U,
    trap="Страна «-» = пусто → для не-air группа не определена",
)
ful(
    D(2026, 11, 10, 14, 30),
    "PL-3013",
    n("S0005013", "09.11.2026"),
    "railway",
    "Urban Fox",
    "Bishkek Sew",
    "Киргизия",
    CO3,
    outcome=NEW,
    group=MULTI,
    logist="Борисов Г.",
    trap="История: Борисов 1",
)
ful(
    D(2026, 11, 1, 10, 0),
    "PL-2990",
    n("S0004990", "28.10.2026"),
    "sea",
    "Urban Fox",
    "Shenzhen Smart Knit",
    "Китай",
    CO3,
    outcome=MISS,
    group=VED,
    logist="Иванова Е.П.",
    trap="Возможный пропуск (01.11 < 02.11); предлагаемый логист = закреплённый в пачке",
)
ful(
    D(2026, 11, 10, 15, 0),
    "PL-3014",
    n("S0005014", "09.11.2026"),
    "railway",
    "Северный ветер",
    "Harbin Wool Factory",
    "Китай",
    CO3,
    outcome=NEW,
    group=VED,
    logist="Смирнов О.",
    trap="Один поставщик — один логист",
)
ful(
    D(2026, 11, 10, 15, 30),
    "PL-3015",
    n("S0005001", "05.11.2026"),
    "railway",
    "Северный ветер",
    "Harbin Wool Factory",
    "Китай",
    CO3,
    outcome=REP_B,
    logist="Смирнов О.",
    comment_out=rep("10.11.2026"),
    trap="Повтор внутри пачки",
)
ful(
    D(2026, 11, 10, 16, 0),
    "PL-3016",
    n("S0005016", "09.11.2026"),
    "air",
    "Mona Bella",
    "Lyon Soie",
    "Франция",
    CO3,
    outcome=NEW,
    group=AIR,
    logist=U,
    trap="Новый поставщик, авиагруппа недоступна",
)
DS3_FUL = list(F)
# Комментарий новой строки PL-3001 получает повтор PL-3015
for x in DS3_FUL:
    if x["num"] == "PL-3001":
        x["exp"]["comment"] = rep("10.11.2026")


# =====================================================================  запись


def build_cloud_rows(spec, start_order, start_date, step_days=2):
    rows = []
    base = start_date
    explicit = {c["order"] for c in spec if c["order"]}
    counter = start_order
    for i, c in enumerate(spec):
        order = c["order"]
        if not order:
            while f"S{counter:07d}" in explicit:
                counter += 1
            order = f"S{counter:07d}"
            counter += 1
        date = c["date"] or (base + dt.timedelta(days=(i * step_days) % 90))
        confirm = date + dt.timedelta(days=3) if c["status"] == "confirm" else None
        docs = date + dt.timedelta(days=10) if c["status"] == "confirm" else None
        rows.append(
            {
                "Заказ": order,
                "Поставщик": c["supplier"],
                "Бренд": c["brand"],
                "Логист": c["logist"],
                "статус": c["status"],
                "дата подтверждения": confirm,
                "cut-off": date + dt.timedelta(days=40),
                "Дата получения УЛ": date,
                "Страна отправления": c["country"],
                "Способ доставки": c["method"],
                "Документы получены": docs,
                "Комментарий": c["comment"] or None,
            }
        )
    return rows


def ful_rows(spec):
    rows = []
    for x in spec:
        rows.append(
            {
                "Дата": x["date"],
                "Cut-off date": x["cutoff"],
                "Номер": x["num"],
                "Статус": x["status"],
                "Комментарий": x["comment"],
                "Способ доставки": x["method"] or None,
                "Бизнес единица": x["brand"],
                "Логист": None,
                "Контрагент": x["supplier"],
                "Страна": x["country"] or None,
            }
        )
    return rows


def write_table(path, sheet, headers, rows, title_rows=(), extra_cols=None, order=None, date_as_text=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    hdr = list(order or headers)
    if extra_cols:
        for pos, name, fn in extra_cols:
            hdr.insert(pos, name)
    for t in title_rows:
        ws.append([t])
    ws.append(hdr)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i, r in enumerate(rows):
        line = []
        for h in hdr:
            fn = next((e[2] for e in (extra_cols or []) if e[1] == h), None)
            if fn:
                line.append(fn(i, r))
                continue
            key = h if h in r else _alias_back(h)
            v = r.get(key)
            if date_as_text and isinstance(v, dt.datetime):
                v = v.strftime("%d.%m.%Y %H:%M") if (v.hour or v.minute) else v.strftime("%d.%m.%Y")
            line.append(v)
        ws.append(line)
        for c in ws[ws.max_row]:
            if isinstance(c.value, dt.datetime):
                c.number_format = "DD.MM.YYYY HH:MM" if (c.value.hour or c.value.minute) else "DD.MM.YYYY"
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    wb.properties.created = wb.properties.modified = D(2026, 11, 11)
    wb.save(path)


_ALIASES = {
    "Поставщик (контрагент)": "Контрагент",
    "Cut off date": "Cut-off date",
    "Номер ФУЛ": "Номер",
    "ФИО": "Наименование",
    "Таб. №": "Код",
    "Тип доставки": "Способ доставки",
    "Статус заказа": "статус",
    "Дата подтверждения": "дата подтверждения",
}


def _alias_back(h):
    return _ALIASES.get(h, h)


def expected_payload(name, ful_spec):
    items = []
    seen = {}
    for x in ful_spec:
        k = x["num"]
        seen[k] = seen.get(k, 0) + 1
        e = x["exp"]
        items.append(
            {
                "key": f"{k}#{seen[k]}",
                "ful_number": k,
                "order_comment": x["comment"],
                "supplier": x["supplier"],
                "outcome": e["outcome"],
                "group": e["group"],
                "logist": e["logist"],
                "comment": e["comment"],
                "trap": e["trap"],
            }
        )
    new_rows = []
    for x in ful_spec:
        e = x["exp"]
        if e["outcome"] != NEW:
            continue
        import re

        order = re.search(r"[SС](\d{7})", x["comment"]).group(1)
        row = {
            "Заказ": "S" + order,
            "Поставщик": x["supplier"].strip(),
            "Бренд": x["brand"] or U,
            "Логист": e["logist"],
            "статус": "в работе",
            "дата подтверждения": None,
            "cut-off": x["cutoff"].strftime("%d.%m.%Y") if x["cutoff"] else U,
            "Дата получения УЛ": x["date"].strftime("%d.%m.%Y %H:%M"),
            "Страна отправления": x["country"] if x["country"] not in ("", "-") else U,
            "Способ доставки": x["method"] or U,
            "Документы получены": None,
            "Комментарий": e["comment"] or None,
        }
        if e.get("row"):
            for k, v in e["row"].items():
                row[{"supplier": "Поставщик"}[k]] = v
        new_rows.append(row)
    return {"dataset": name, "undefined": U, "cloud_columns": CLOUD_HEADERS, "fuls": items, "new_rows": new_rows}


def write_expected(dirpath, payload):
    with open(os.path.join(dirpath, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Эталон по ФУЛ"
    ws.append(
        [
            "Ключ",
            "Номер ФУЛ",
            "Поставщик",
            "Ожидаемый статус",
            "Группа",
            "Логист",
            "Комментарий для Cloud",
            "Ловушка / что проверяется",
        ]
    )
    for it in payload["fuls"]:
        ws.append(
            [
                it["key"],
                it["ful_number"],
                it["supplier"],
                it["outcome"],
                it["group"],
                it["logist"],
                it["comment"],
                it["trap"],
            ]
        )
    ws2 = wb.create_sheet("Эталон новых строк Cloud")
    ws2.append(payload["cloud_columns"])
    for r in payload["new_rows"]:
        ws2.append([r[c] for c in payload["cloud_columns"]])
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True)
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = 20
    ws.column_dimensions["H"].width = 90
    wb.properties.created = wb.properties.modified = D(2026, 11, 11)
    wb.save(os.path.join(dirpath, "expected.xlsx"))


def to_xls(xlsx_path):
    """Сконвертировать в .xls через LibreOffice, если он есть. Возвращает путь итогового файла."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return xlsx_path
    outdir = os.path.dirname(xlsx_path)
    with tempfile.TemporaryDirectory() as profile:
        r = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--convert-to",
                "xls",
                "--outdir",
                outdir,
                xlsx_path,
            ],
            capture_output=True,
            timeout=120,
        )
    xls = os.path.splitext(xlsx_path)[0] + ".xls"
    if r.returncode == 0 and os.path.exists(xls):
        os.remove(xlsx_path)
        return xls
    return xlsx_path


def _fresh(path):
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        os.remove(os.path.join(path, f))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets"))
    ap.add_argument("--no-xls", action="store_true", help="не конвертировать сотрудников ds2 в .xls")
    a = ap.parse_args()

    # ---- ds1
    d1 = os.path.join(a.out, "ds1_base")
    i1 = _fresh(os.path.join(d1, "input"))
    cloud1 = build_cloud_rows(DS1_CLOUD, 3001, D(2026, 8, 10))
    write_table(os.path.join(i1, "1c_ful.xlsx"), "ФУЛ", FUL_HEADERS, ful_rows(DS1_FUL))
    write_table(os.path.join(i1, "cloud_registry.xlsx"), "Cloud", CLOUD_HEADERS, cloud1)
    write_table(
        os.path.join(i1, "1c_staff.xlsx"), "Сотрудники", STAFF_HEADERS, [dict(zip(STAFF_HEADERS, s)) for s in DS1_STAFF]
    )
    p1 = expected_payload("ds1_base", DS1_FUL)
    write_expected(d1, p1)

    # ---- ds2: те же данные, другой формат
    d2 = os.path.join(a.out, "ds2_format")
    i2 = _fresh(os.path.join(d2, "input"))
    ful_order = [
        "Номер ФУЛ",
        "Поставщик (контрагент)",
        "Страна",
        "Тип доставки",
        "Комментарий",
        "Дата",
        "Cut off date",
        "Бизнес единица",
        "Статус",
        "Логист",
    ]
    write_table(
        os.path.join(i2, "Выгрузка_ФУЛ_ноябрь.xlsx"),
        "Отчёт 1С",
        None,
        [{**r} for r in ful_rows(DS1_FUL)],
        title_rows=("Отчёт: Упаковочные листы поставщиков", "Период: 01.11.2026 – 11.11.2026"),
        extra_cols=[(3, "Склад назначения", lambda i, r: "Подольск"), (7, "Вес брутто, кг", lambda i, r: 100 + i * 7)],
        order=ful_order,
        date_as_text=True,
    )
    cloud_order = [
        "Заказ",
        "Поставщик",
        "Бренд",
        "Логист",
        "Статус заказа",
        "Дата подтверждения",
        "cut-off",
        "Дата получения УЛ",
        "Страна отправления",
        "Способ доставки",
        "Документы получены",
        "Комментарий",
    ]
    write_table(
        os.path.join(i2, "Реестр заказов.xlsx"),
        "Реестр 2026",
        None,
        cloud1,
        extra_cols=[(4, "Менеджер бренда", lambda i, r: "Алексеева Т."), (13, "Склад", lambda i, r: "Подольск")],
        order=cloud_order,
    )
    staff_path = os.path.join(i2, "Сотрудники.xlsx")
    write_table(
        staff_path,
        "Лист1",
        None,
        [dict(zip(STAFF_HEADERS, s)) for s in DS1_STAFF],
        title_rows=("Список сотрудников отдела логистики",),
        extra_cols=[(2, "Подразделение", lambda i, r: "Логистика")],
        order=["Таб. №", "ФИО", "Должность", "Текущее состояние"],
    )
    if not a.no_xls:
        to_xls(staff_path)
    p2 = dict(p1, dataset="ds2_format", cloud_columns=cloud_order)
    p2["new_rows"] = [
        {
            ("Статус заказа" if k == "статус" else "Дата подтверждения" if k == "дата подтверждения" else k): v
            for k, v in r.items()
        }
        for r in p1["new_rows"]
    ]
    write_expected(d2, p2)

    # ---- ds3
    d3 = os.path.join(a.out, "ds3_edge")
    i3 = _fresh(os.path.join(d3, "input"))
    cloud3 = build_cloud_rows(DS3_CLOUD, 4001, D(2026, 9, 1), step_days=2)
    write_table(os.path.join(i3, "ful.xlsx"), "Sheet1", FUL_HEADERS, ful_rows(DS3_FUL))
    write_table(os.path.join(i3, "cloud.xlsx"), "Sheet1", CLOUD_HEADERS, cloud3)
    write_table(
        os.path.join(i3, "staff.xlsx"), "Sheet1", STAFF_HEADERS, [dict(zip(STAFF_HEADERS, s)) for s in DS3_STAFF]
    )
    write_expected(d3, expected_payload("ds3_edge", DS3_FUL))

    for d in (d1, d2, d3):
        print(d, sorted(os.listdir(d)), sorted(os.listdir(os.path.join(d, "input"))))


if __name__ == "__main__":
    main()
