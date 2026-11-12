#!/usr/bin/env python3
"""Контрольные прогоны и сводный отчёт по метрикам приёмки.

    python3 tools/run_control.py script --runs 3   # прогнать скрипт навыка 3 раза на каждом наборе
    python3 tools/run_control.py report            # собрать метрики по всем прогонам в runs/

Прогоны лежат в runs/<набор>/<исполнитель>_<N>/ (distribution_result.xlsx + log.json).
Исполнители: script (скрипт напрямую), gigachat_agent / gigachat_simple (tools/gigachat_agent.py),
claude (агентный прогон в Claude). Отчёт: runs/REPORT.md и runs/metrics.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from evaluate import FIELDS, evaluate

DATASETS = os.path.join(ROOT, "testdata", "datasets")
RUNS = os.path.join(ROOT, "runs")
SCRIPT = os.path.join(ROOT, "agent", "skill", "logist-distribution", "scripts", "distribute.py")
THRESH = {"full_match_rate": 0.95, "invented_values": 0, "time_total_s": 60}


def run_script(n):
    for ds in sorted(os.listdir(DATASETS)):
        for i in range(1, n + 1):
            out = os.path.join(RUNS, ds, f"script_{i}")
            os.makedirs(out, exist_ok=True)
            t0 = time.perf_counter()
            r = subprocess.run(
                [sys.executable, SCRIPT, os.path.join(DATASETS, ds, "input"), "--out", out],
                capture_output=True,
                text=True,
            )
            dt_ = time.perf_counter() - t0
            with open(os.path.join(out, "log.json"), "w", encoding="utf-8") as fh:
                json.dump(
                    {"runner": "script", "exit_code": r.returncode, "time_total_s": round(dt_, 2), "stdout": r.stdout},
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
            print(f"{ds} script_{i}: код {r.returncode}, {dt_:.2f} с")


def collect():
    groups = defaultdict(list)
    for ds in sorted(os.listdir(RUNS)):
        dsdir = os.path.join(RUNS, ds)
        exp = os.path.join(DATASETS, ds, "expected.json")
        if not os.path.isdir(dsdir) or not os.path.exists(exp):
            continue
        for run in sorted(os.listdir(dsdir)):
            rdir = os.path.join(dsdir, run)
            res = os.path.join(rdir, "distribution_result.xlsx")
            m = re.fullmatch(r"(.+)_(\d+)", run)
            if not (os.path.isdir(rdir) and m and os.path.exists(res)):
                continue
            log = {}
            if os.path.exists(os.path.join(rdir, "log.json")):
                log = json.loads(Path(rdir, "log.json").read_text(encoding="utf-8"))
            ev = evaluate(exp, res)
            ev["time_total_s"] = log.get("time_total_s")
            ev["answer_matches_template"] = log.get("answer_matches_template")
            ev["answer_has_service_markers"] = log.get("answer_has_service_markers")
            ev["run"] = run
            groups[(ds, m.group(1))].append(ev)
    return groups


def report():
    groups = collect()
    rows, out = [], {}
    for (ds, runner), evs in sorted(groups.items()):
        times = [e["time_total_s"] for e in evs if e["time_total_s"] is not None]
        agg = {
            "dataset": ds,
            "runner": runner,
            "runs": len(evs),
            "full_match_rate_min": min(e["full_match_rate"] for e in evs),
            "field_accuracy_min": {f: min(e["field_accuracy"][f] for e in evs) for f in FIELDS},
            "cloud_form_ok_all": all(e["cloud_form_ok"] for e in evs),
            "new_rows_cell_acc_min": min(e["new_rows_cell_acc"] for e in evs),
            "invented_values_total": sum(e["invented_values"] for e in evs),
            "reasons_ok_all": all(e["reasons_ok"] for e in evs),
            "reproducible": len({e["content_hash"] for e in evs}) == 1,
            "hashes": sorted({e["content_hash"] for e in evs}),
            "time_avg_s": round(sum(times) / len(times), 2) if times else None,
            "time_max_s": max(times) if times else None,
            "answer_ok": [
                None
                if e["answer_matches_template"] is None
                else bool(e["answer_matches_template"]) and not e.get("answer_has_service_markers")
                for e in evs
            ],
            "mismatches": [x for e in evs for x in e["mismatches"] + e["cell_mismatches"]],
        }
        agg["passed"] = (
            agg["runs"] >= 3
            and agg["full_match_rate_min"] >= THRESH["full_match_rate"]
            and agg["cloud_form_ok_all"]
            and agg["invented_values_total"] == 0
            and agg["reproducible"]
            and agg["reasons_ok_all"]
            and all(x is not False for x in agg["answer_ok"])
            and (agg["time_max_s"] is None or agg["time_max_s"] <= THRESH["time_total_s"])
        )
        out[f"{ds}/{runner}"] = agg
        rows.append(agg)

    with open(os.path.join(RUNS, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    pct = lambda v: f"{v:.1%}"  # noqa: E731
    lines = [
        "# Результаты контрольных прогонов",
        "",
        "Пороги из БТ: точность ≥ 95%, форма Cloud 100% прогонов, 0 выдуманных значений, "
        "одинаковый результат во всех прогонах, обоснование у каждого назначения, ответ ≤ 60 с. "
        "Дополнительно: ответ модели в чат дословно совпадает с шаблоном скрипта, без служебных строк.",
        "",
        "Исполнители: `script` — скрипт навыка напрямую; `claude` — агентный прогон (Claude Sonnet, навык v1.1); "
        "`claude-v1` — первый раунд на навыке v1.0, по итогам которого исправлен ответ в чат; "
        "`gigachat_agent` — tools/gigachat_agent.py.",
        "",
        "| Набор | Исполнитель | Прогонов | Совпадение ФУЛ целиком (мин.) | Статус | Группа | Логист | Комментарий | "
        "Ячейки новых строк | Форма Cloud | Выдумано | Воспроизводимо | Ответ в чат = шаблон | Время ср./макс., с | Итог |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in rows:
        fa = a["field_accuracy_min"]
        t = f"{a['time_avg_s']} / {a['time_max_s']}" if a["time_avg_s"] is not None else "—"
        ao = [x for x in a["answer_ok"] if x is not None]
        ans = f"{sum(ao)}/{len(ao)}" if ao else "— (без модели)"
        lines.append(
            f"| {a['dataset']} | {a['runner']} | {a['runs']} | {pct(a['full_match_rate_min'])} | "
            f"{pct(fa['outcome'])} | {pct(fa['group'])} | {pct(fa['logist'])} | {pct(fa['comment'])} | "
            f"{pct(a['new_rows_cell_acc_min'])} | {'да' if a['cloud_form_ok_all'] else 'НЕТ'} | "
            f"{a['invented_values_total']} | {'да' if a['reproducible'] else 'НЕТ'} | {ans} | {t} | "
            f"{'✅' if a['passed'] else '❌'} |"
        )
    bad = [a for a in rows if a["mismatches"]]
    if bad:
        lines += ["", "## Расхождения с эталоном", ""]
        for a in bad:
            for x in a["mismatches"][:30]:
                lines.append(f"- {a['dataset']}/{a['runner']}: {x}")
    with open(os.path.join(RUNS, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("script")
    s.add_argument("--runs", type=int, default=3)
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "script":
        run_script(a.runs)
    report()


if __name__ == "__main__":
    main()
