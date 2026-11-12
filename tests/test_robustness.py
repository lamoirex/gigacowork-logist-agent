"""Fuzz-тест устойчивости к формату входных файлов.

Для каждого набора данных строятся десятки случайных (но воспроизводимых по seed) вариантов
тех же данных: переставленные колонки и строки, заголовки-синонимы в любом регистре,
шапка отчёта над таблицей, лишние колонки, другие имена листов и файлов, даты текстом,
одна книга с тремя листами вместо трёх файлов. Решения агента обязаны совпасть с исходными:
те же статусы, группы, логисты и комментарии по каждому ФУЛ, те же новые строки Cloud
(колонки — в порядке реестра из входного файла) и тот же ответ в чат.
"""

import datetime as dt
import os
import random
import tempfile
import unittest
from pathlib import Path

import openpyxl

from helpers import DATASETS, RULES, N  # задаёт sys.path

import distribute

VARIANTS_PER_DATASET = 25
ROLE_BY_NAME = {
    "ful": "ful",
    "cloud": "cloud",
    "staff": "staff",
    "1c_ful": "ful",
    "cloud_registry": "cloud",
    "1c_staff": "staff",
}


def read_table(path):
    ws = openpyxl.load_workbook(path).active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    return rows[0], rows[1:]


def header_to_field(role, header):
    for field, spec in RULES["headers"][role].items():
        if header in spec["synonyms"]:
            return field
    return None


def mutate(role, header, rows, rnd):
    """Случайный, но эквивалентный по смыслу вариант таблицы."""
    header = list(header)
    rows = [list(r) for r in rows]
    # заголовки — случайный синоним в случайном регистре
    new_header = []
    for h in header:
        field = header_to_field(role, h)
        syn = rnd.choice(RULES["headers"][role][field]["synonyms"]) if field else h
        new_header.append(rnd.choice([syn, syn.upper(), syn.lower(), f"  {syn} "]))
    # синонимы не должны совпасть друг с другом
    if len({h.strip().lower() for h in new_header}) != len(new_header):
        new_header = header
    # лишние колонки
    for i in range(rnd.randint(0, 3)):
        pos = rnd.randint(0, len(new_header))
        new_header.insert(pos, f"Служебное поле {i}")
        for r in rows:
            r.insert(pos, rnd.choice([None, "x", 42]))
    # перестановка колонок и строк
    order = list(range(len(new_header)))
    rnd.shuffle(order)
    new_header = [new_header[i] for i in order]
    rows = [[r[i] for i in order] for r in rows]
    rnd.shuffle(rows)
    # даты текстом (как в части выгрузок 1С)
    if role == "ful" and rnd.random() < 0.5:
        rows = [[v.strftime("%d.%m.%Y %H:%M") if isinstance(v, dt.datetime) else v for v in r] for r in rows]
    # шапка отчёта над таблицей
    title = [[f"Отчёт {role}"], ["Сформирован автоматически"], []][: rnd.randint(0, 3)]
    return [*title, new_header, *rows]


def write_variant(src_dir, out_dir, seed):
    rnd = random.Random(seed)
    tables = {}
    for name in sorted(os.listdir(src_dir)):
        role = ROLE_BY_NAME[Path(name).stem]
        header, rows = read_table(os.path.join(src_dir, name))
        tables[role] = mutate(role, header, rows, rnd)
    roles = list(tables)
    rnd.shuffle(roles)
    if rnd.random() < 0.3:  # одна книга с тремя листами
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for i, role in enumerate(roles):
            ws = wb.create_sheet(f"Лист{i + 1}")
            for r in tables[role]:
                ws.append(r)
        wb.save(os.path.join(out_dir, "выгрузка.xlsx"))
        return
    for i, role in enumerate(roles):
        wb = openpyxl.Workbook()
        wb.active.title = f"Sheet{rnd.randint(1, 9)}"
        for r in tables[role]:
            wb.active.append(r)
        wb.save(os.path.join(out_dir, f"файл_{i}_{rnd.randint(100, 999)}.xlsx"))


def cloud_field(title):
    """Заголовок колонки результата → поле реестра (заголовки повторяют написание из входного реестра)."""
    key = N.norm_header(title)
    for field, spec in RULES["headers"]["cloud"].items():
        if key in {N.norm_header(s) for s in spec["synonyms"]}:
            return field
    raise AssertionError(f"неизвестная колонка Cloud: {title!r}")


def decisions(out_dir):
    """Смысл результата без технических деталей (номера строк в файлах, порядок колонок)."""
    wb = openpyxl.load_workbook(os.path.join(out_dir, "distribution_result.xlsx"))
    rows = list(wb["Журнал решений"].iter_rows(values_only=True))
    h = rows[0]
    idx = [h.index(c) for c in ("Номер ФУЛ", "Заказ", "Статус обработки", "Группа", "Логист", "Комментарий для Cloud")]
    journal = sorted(tuple(str(r[i]) for i in idx) for r in rows[1:])
    new_rows = list(wb["Новые строки Cloud"].iter_rows(values_only=True))
    fields = [cloud_field(t) for t in new_rows[0]]
    assert len(fields) == 12 and len(set(fields)) == 12, fields  # ровно 12 колонок формы реестра
    cloud = sorted(tuple(sorted((f, str(v)) for f, v in zip(fields, r))) for r in new_rows[1:])
    chat = Path(out_dir, "chat_message.txt").read_text(encoding="utf-8")
    return journal, cloud, chat


class TestFormatRobustness(unittest.TestCase):
    def check_dataset(self, ds):
        src = os.path.join(DATASETS, ds, "input")
        with tempfile.TemporaryDirectory() as base_out:
            self.assertEqual(distribute.main([src, "--out", base_out]), 0)
            baseline = decisions(base_out)
        for seed in range(VARIANTS_PER_DATASET):
            with self.subTest(dataset=ds, seed=seed), tempfile.TemporaryDirectory() as d:
                inp, out = os.path.join(d, "in"), os.path.join(d, "out")
                os.makedirs(inp)
                write_variant(src, inp, seed=1000 * len(ds) + seed)
                self.assertEqual(distribute.main([inp, "--out", out]), 0)
                self.assertEqual(decisions(out), baseline)

    def test_base_dataset_variants(self):
        self.check_dataset("ds1_base")

    def test_edge_dataset_variants(self):
        self.check_dataset("ds3_edge")


if __name__ == "__main__":
    unittest.main()
