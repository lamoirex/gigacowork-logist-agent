#!/usr/bin/env python3
"""Распределение новых ФУЛ между логистами и подготовка строк реестра Cloud.

Примеры:
    python3 distribute.py ful.xlsx cloud.xlsx staff.xlsx --out ./result
    python3 distribute.py /path/to/attachments --out ./result
    python3 distribute.py --ful 1c.xlsx --cloud cloud.xlsx --staff emp.xlsx --out ./result

Файлы можно передать в любом порядке: роль каждого определяется по заголовкам.
Результат: <out>/distribution_result.xlsx и <out>/summary.json.
В stdout печатается готовый ответ для чата (между маркерами) и путь к файлам.

Коды выхода: 0 — успех, 2 — ошибка входных данных (текст ошибки в stdout).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import engine as E
import report as R
from xlreader import ROLE_TITLES, InputError, detect_tables

RESULT_NAME = "distribution_result.xlsx"
SUMMARY_NAME = "summary.json"
EXCEL_EXT = (".xlsx", ".xlsm", ".xls")


def _expand(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if (
                    os.path.isfile(full)
                    and name.lower().endswith(EXCEL_EXT)
                    and not name.startswith("~$")
                    and not name.startswith(RESULT_NAME.split(".")[0])
                ):
                    out.append(full)
        else:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(p)
    return uniq


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Распределение ФУЛ между логистами (GigaCowork skill)")
    ap.add_argument("inputs", nargs="*", help="Excel-файлы или папка с ними, в любом порядке")
    ap.add_argument("--ful", help="выгрузка ФУЛ из 1С")
    ap.add_argument("--cloud", help="реестр Cloud")
    ap.add_argument("--staff", help="сотрудники из 1С")
    ap.add_argument("--out", default="result", help="папка для результата (по умолчанию ./result)")
    ap.add_argument("--rules", default=os.path.join(HERE, "..", "references", "rules.json"))
    ap.add_argument("--countries", default=os.path.join(HERE, "..", "references", "countries.json"))
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    paths = _expand([p for p in (args.ful, args.cloud, args.staff) if p] + list(args.inputs))
    if not paths:
        print("ОШИБКА: не переданы входные файлы. Нужны три выгрузки Excel: ФУЛ из 1С, реестр Cloud, сотрудники из 1С.")
        return 2
    with open(args.rules, encoding="utf-8") as fh:
        rules = json.load(fh)
    with open(args.countries, encoding="utf-8") as fh:
        countries = json.load(fh)

    try:
        tables, warnings = detect_tables(paths, rules["headers"])
    except InputError as exc:
        print(f"ОШИБКА: {exc}")
        return 2

    eng = E.Engine(rules, countries)
    res = eng.run(tables)

    os.makedirs(args.out, exist_ok=True)
    out_xlsx = os.path.abspath(os.path.join(args.out, RESULT_NAME))
    out_json = os.path.abspath(os.path.join(args.out, SUMMARY_NAME))
    inputs_desc = [(ROLE_TITLES[r], tables[r].describe()) for r in ("ful", "cloud", "staff")]
    R.write_excel(res, out_xlsx, eng, inputs_desc, warnings)
    data = R.write_summary(res, out_json, out_xlsx, eng, inputs_desc, warnings)

    print("=== ОТВЕТ ДЛЯ ЧАТА ===")
    print(data["chat_message"])
    print("=== КОНЕЦ ОТВЕТА ===")
    for w in warnings:
        print(f"Предупреждение: {w}")
    print(f"Файл результата: {out_xlsx}")
    print(f"Сводка JSON: {out_json}")
    print(f"Время работы скрипта: {time.perf_counter() - t0:.2f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
