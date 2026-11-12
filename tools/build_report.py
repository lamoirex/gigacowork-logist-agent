#!/usr/bin/env python3
"""Сборка отчёта: обновляет вставки в docs/report.md из файлов проекта и печатает dist/report.pdf.

    python3 tools/build_report.py [--no-pdf]

Вставки между <!-- BEGIN:x --> и <!-- END:x -->: вопросы (docs/questions.md), покрытие ловушек
(testdata/README.md), метрики (runs/REPORT.md), лог прогона и фрагмент журнала (runs/ds1_base/gigachat_agent_1),
мотивация (docs/motivation.local.md — личный файл, в git не попадает; если его нет, раздел пуст). PDF печатается через headless Chrome/Chromium.
"""

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import markdown
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
RUN = os.path.join(ROOT, "runs", "ds1_base", "gigachat_agent_1")


def read(*p):
    return Path(ROOT, *p).read_text(encoding="utf-8")


def first_table_after(text, heading=None):
    if heading:
        text = text[text.index(heading) + len(heading) :]
    lines, out, started = text.splitlines(), [], False
    for ln in lines:
        if ln.startswith("|"):
            out.append(ln)
            started = True
        elif started:
            break
    return "\n".join(out)


def journal_excerpt(n=12):
    wb = openpyxl.load_workbook(os.path.join(RUN, "distribution_result.xlsx"), read_only=True)
    rows = list(wb["Журнал решений"].iter_rows(values_only=True))
    hdr = rows[0]
    cols = ["Номер ФУЛ", "Заказ", "Статус обработки", "Группа", "Логист", "Обоснование"]
    idx = [hdr.index(c) for c in cols]
    esc = lambda v: ("" if v is None else str(v)).replace("|", "/").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[1 : n + 1]:
        out.append("| " + " | ".join(esc(r[i]) for i in idx) + " |")
    return "\n".join(out) + f"\n\n*Показаны первые {n} из {len(rows) - 1} ФУЛ.*"


def transcript():
    t = Path(RUN, "transcript.md").read_text(encoding="utf-8")
    t = re.sub(r"^# ", "### ", t, flags=re.M)
    # многострочный ответ агента — блоком, иначе в PDF строки склеиваются в абзац
    t = re.sub(
        r"(\*\*Ответ агента:\*\*\n\n)(?!```)(.*?)(\n\n---)",
        lambda m: f"{m.group(1)}```text\n{m.group(2)}\n```{m.group(3)}",
        t,
        flags=re.S,
    )
    return t.replace("---\n", "\n")


def motivation():
    """Ответы на мотивационные вопросы — только в PDF, в репозитории их нет."""
    path = Path(ROOT, "docs", "motivation.local.md")
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def blocks():
    runs = read("runs", "REPORT.md").split("\n", 2)[2].strip()
    return {
        "questions": first_table_after(read("docs", "questions.md")),
        "coverage": first_table_after(read("testdata", "README.md"), "## Покрытие требований ТЗ"),
        "runs": runs,
        "transcript": transcript(),
        "journal": journal_excerpt(),
        "motivation": motivation(),
    }


def fill(md, b):
    for k, v in b.items():
        md = re.sub(
            rf"<!-- BEGIN:{k} -->.*?<!-- END:{k} -->",
            lambda _m, k=k, v=v: f"<!-- BEGIN:{k} -->\n{v}\n<!-- END:{k} -->",
            md,
            flags=re.S,
        )
    return md


CSS = """
@page { size: A4; margin: 14mm 13mm 14mm 13mm; }
body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 9.6pt; line-height: 1.42; color: #1d2733; }
h1 { font-size: 17pt; color: #12324f; border-bottom: 2px solid #12324f; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 13pt; color: #12324f; margin-top: 18px; page-break-after: avoid; }
h3 { font-size: 11pt; color: #12324f; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.1pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { overflow-wrap: break-word; background: #12324f; color: #fff; text-align: left; padding: 3px 5px; }
td { overflow-wrap: break-word; border: 1px solid #cfd8e3; padding: 3px 5px; vertical-align: top; }
tr:nth-child(even) td { background: #f5f8fb; }
code { overflow-wrap: anywhere; font-family: 'DejaVu Sans Mono', monospace; font-size: 8.3pt; background: #eef2f6; padding: 0 2px; }
pre { overflow-wrap: anywhere; word-break: break-word; background: #f3f6f9; border: 1px solid #d5dde6; padding: 7px; font-size: 7.6pt; white-space: pre-wrap; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
hr { border: 0; border-top: 1px solid #d5dde6; margin: 14px 0; }
img, svg { max-width: 100%; }
table.wide { font-size: 6.8pt; }
table.wide td, table.wide th { overflow-wrap: anywhere; padding: 2px 3px; }
.diagram { text-align: center; page-break-before: always; page-break-inside: avoid; page-break-after: always; }
.diagram img, .diagram svg { height: 250mm; width: auto; max-width: 100%; }
blockquote { color: #555; border-left: 3px solid #c9a227; margin: 6px 0; padding: 2px 10px; background: #fffbea; }
"""


def diagram_png_b64():
    """PNG схемы высокого разрешения (graphviz), чтобы размер в PDF был предсказуемым."""
    import base64

    dot = shutil.which("dot")
    if not dot:
        return None
    png = subprocess.run(
        [dot, "-Tpng", "-Gdpi=220", os.path.join(DOCS, "algorithm.dot")], capture_output=True, check=True
    ).stdout
    return base64.b64encode(png).decode()


def to_html(md):
    html = markdown.markdown(md, extensions=["tables", "fenced_code"])

    def mark_wide(m):
        t = m.group(0)
        return t.replace("<table>", '<table class="wide">', 1) if t.count("<th") > 9 else t

    html = re.sub(r"<table>.*?</table>", mark_wide, html, flags=re.S)
    b64 = diagram_png_b64()
    if b64:
        img = f'<div class="diagram"><p><b>Схема алгоритма</b></p><img src="data:image/png;base64,{b64}"></div>'
    else:
        svg = Path(DOCS, "algorithm.svg").read_text(encoding="utf-8")
        img = f'<div class="diagram"><p><b>Схема алгоритма</b></p>{svg[svg.index("<svg") :]}</div>'
    html = re.sub(r'<p><img alt="Алгоритм" src="algorithm.svg" ?/?></p>', img, html)
    return f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html}</body></html>"


def print_pdf(html, out):
    chrome = next((shutil.which(c) for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None)
    if not chrome:
        raise SystemExit("Нужен Chrome/Chromium для печати PDF (или запустите с --no-pdf).")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "report.html")
        Path(p).write_text(html, encoding="utf-8")
        subprocess.run(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--user-data-dir={d}/profile",
                f"--print-to-pdf={out}",
                f"file://{p}",
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    path = os.path.join(DOCS, "report.md")
    md = fill(Path(path).read_text(encoding="utf-8"), blocks())
    Path(path).write_text(md, encoding="utf-8")
    if not a.no_pdf:
        os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
        out = os.path.join(ROOT, "dist", "report.pdf")
        print_pdf(to_html(md), out)
        print(out, os.path.getsize(out), "байт")


if __name__ == "__main__":
    main()
