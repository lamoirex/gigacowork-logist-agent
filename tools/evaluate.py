#!/usr/bin/env python3
"""Сравнение результата агента с эталоном и расчёт метрик приёмки.

    python3 tools/evaluate.py --expected testdata/datasets/ds1_base/expected.json \
                              --result runs/ds1/distribution_result.xlsx [--json metrics.json]

Метрики (см. БТ, «Метрики оценки качества»):
  * full_match_rate      — доля ФУЛ, где совпали все поля (статус, группа, логист, комментарий);
  * field_accuracy       — точность по каждому полю;
  * cloud_form_ok        — 12 колонок в порядке реестра, без старых ФУЛ и дублей;
  * new_rows_cell_acc    — точность по ячейкам новых строк Cloud;
  * invented_values      — сколько раз агент подставил значение там, где в эталоне «не определено»;
  * reasons_ok           — у каждого назначения логиста есть обоснование;
  * content_hash         — хеш содержимого всех листов (для проверки воспроизводимости).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys

import openpyxl

FIELDS = ("outcome", "group", "logist", "comment")
J_COLS = {
    "row": "Строка ФУЛ в файле",
    "num": "Номер ФУЛ",
    "outcome": "Статус обработки",
    "group": "Группа",
    "logist": "Логист",
    "comment": "Комментарий для Cloud",
    "reason": "Обоснование",
    "rule": "Правило",
}


def norm(v):
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y %H:%M") if (v.hour or v.minute) else v.strftime("%d.%m.%Y")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def sheet_rows(ws):
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        return [], []
    return [norm(h) for h in rows[0]], rows[1:]


def content_hash(wb):
    h = hashlib.sha256()
    for ws in wb.worksheets:
        h.update(ws.title.encode())
        for r in ws.iter_rows(values_only=True):
            h.update(("\x1f".join(norm(v) for v in r) + "\n").encode())
    return h.hexdigest()[:16]


def evaluate(expected_path, result_path):
    exp = json.load(open(expected_path, encoding="utf-8"))
    U = exp["undefined"]
    wb = openpyxl.load_workbook(result_path, data_only=True)
    out = {"dataset": exp["dataset"], "result": result_path, "errors": []}

    # ---------- журнал по ФУЛ
    hdr, rows = sheet_rows(wb["Журнал решений"])
    ix = {k: hdr.index(v) for k, v in J_COLS.items()}
    journal = sorted(rows, key=lambda r: int(r[ix["row"]]))
    got, seen = {}, {}
    for r in journal:
        num = norm(r[ix["num"]])
        seen[num] = seen.get(num, 0) + 1
        got[f"{num}#{seen[num]}"] = {k: norm(r[ix[k]]) for k in (*FIELDS, "reason", "rule")}

    per_field = {f: 0 for f in FIELDS}
    full, mismatches, invented = 0, [], []
    for e in exp["fuls"]:
        g = got.get(e["key"])
        if g is None:
            mismatches.append({"key": e["key"], "field": "*", "expected": "ФУЛ в журнале", "got": "нет"})
            continue
        ok_all = True
        for f in FIELDS:
            ev = norm(e[f])
            if g[f] == ev:
                per_field[f] += 1
            else:
                ok_all = False
                mismatches.append({"key": e["key"], "field": f, "expected": ev, "got": g[f], "trap": e["trap"]})
                if ev == U and g[f] not in ("", U):
                    invented.append({"key": e["key"], "field": f, "got": g[f]})
        full += ok_all
    n = len(exp["fuls"])
    out["ful_total"] = n
    out["full_match_rate"] = round(full / n, 4) if n else 0
    out["field_accuracy"] = {f: round(per_field[f] / n, 4) for f in FIELDS}

    # обоснование у каждого назначения
    no_reason = [
        k for k, g in got.items() if g["logist"] not in ("", U) and g["outcome"].startswith("Новый") and not g["reason"]
    ]
    out["reasons_ok"] = not no_reason

    # ---------- новые строки Cloud
    hdr, rows = sheet_rows(wb["Новые строки Cloud"])
    exp_cols = [norm(c) for c in exp["cloud_columns"]]
    form_cols_ok = hdr == exp_cols
    if not form_cols_ok:
        out["errors"].append(f"Колонки Cloud: ожидалось {exp_cols}, получено {hdr}")
    got_rows = [dict(zip(hdr, (norm(v) for v in r))) for r in rows]
    order_col = exp_cols[0]
    got_orders = [r.get(order_col, "") for r in got_rows]
    exp_rows = {norm(r[exp["cloud_columns"][0]]): r for r in exp["new_rows"]}
    dup = len(got_orders) != len(set(got_orders))
    extra = sorted(set(got_orders) - set(exp_rows))
    missing = sorted(set(exp_rows) - set(got_orders))
    out["cloud_form_ok"] = form_cols_ok and len(hdr) == 12 and not dup and not extra and not missing
    out["cloud_rows"] = {
        "expected": len(exp_rows),
        "got": len(got_rows),
        "extra": extra,
        "missing": missing,
        "duplicates": dup,
    }
    cells = cells_ok = 0
    cell_mism = []
    for o, er in exp_rows.items():
        gr = next((r for r in got_rows if r.get(order_col) == o), None)
        if gr is None:
            continue
        for c in exp["cloud_columns"]:
            cells += 1
            ev, gv = norm(er[c]), gr.get(norm(c), "")
            if ev == gv:
                cells_ok += 1
            else:
                cell_mism.append({"order": o, "column": c, "expected": ev, "got": gv})
                if ev == U and gv not in ("", U):
                    invented.append({"key": o, "field": c, "got": gv})
    out["new_rows_cell_acc"] = round(cells_ok / cells, 4) if cells else 1.0
    out["invented_values"] = len(invented)
    out["invented_details"] = invented
    out["mismatches"] = mismatches
    out["cell_mismatches"] = cell_mism
    out["content_hash"] = content_hash(wb)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    m = evaluate(a.expected, a.result)
    if a.json:
        json.dump(m, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(
        f"{m['dataset']}: full_match={m['full_match_rate']:.1%} fields={m['field_accuracy']} "
        f"cloud_form_ok={m['cloud_form_ok']} cells={m['new_rows_cell_acc']:.1%} invented={m['invented_values']} "
        f"reasons_ok={m['reasons_ok']} hash={m['content_hash']}"
    )
    for x in m["mismatches"] + m["cell_mismatches"]:
        print("  MISMATCH", x)
    for e in m["errors"]:
        print("  ERROR", e)
    ok = m["full_match_rate"] >= 0.95 and m["cloud_form_ok"] and m["invented_values"] == 0 and m["reasons_ok"]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
