#!/usr/bin/env python3
"""Импорт агентных прогонов из внешнего harness (Claude) в runs/.

Каждая сессия harness — папка <sessions>/<набор>_<N>/ с файлами:
  attachments/ (вложения), outputs/distribution_result.xlsx, stdout.txt (вывод скрипта навыка),
  answer.md (финальный ответ агента в чат), t_start / t_end (время начала и конца, unix-секунды),
  и, необязательно, <sessions>/durations.json — полное время ответа по данным harness, с.

    python3 tools/import_harness_runs.py /tmp/harness/sessions --runner claude

Создаёт runs/<набор>/<runner>_<N>/ с результатом, транскриптом и log.json, где есть
время ответа и признак «ответ в чате совпал с шаблоном скрипта» (модель ничего не исказила).
"""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START, END = "=== ОТВЕТ ДЛЯ ЧАТА ===", "=== КОНЕЦ ОТВЕТА ==="


def squash(s):
    return " ".join(s.split())


def template_from_stdout(stdout):
    if START not in stdout:
        return ""
    return stdout.split(START, 1)[1].split(END, 1)[0].strip()


def check_answer(answer, template):
    """-> (совпадает ли шаблон дословно, есть ли посторонние служебные строки)."""
    exact = bool(template) and squash(template) in squash(answer)
    leaked = START in answer or END in answer
    return exact, leaked


def _read(folder, name):
    """Текст файла сессии или пустая строка, если файла нет."""
    path = Path(folder, name)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions")
    ap.add_argument("--runner", default="claude")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    # Полное время ответа агента по данным harness (от запуска до финального ответа), если есть
    dpath = os.path.join(a.sessions, "durations.json")
    durations = json.loads(Path(dpath).read_text(encoding="utf-8")) if os.path.exists(dpath) else {}
    for name in sorted(os.listdir(a.sessions)):
        m = re.fullmatch(r"(.+)_(\d+)", name)
        src = os.path.join(a.sessions, name)
        if not m or not os.path.isdir(src):
            continue
        ds, k = m.groups()
        dst = os.path.join(ROOT, "runs", ds, f"{a.runner}_{k}")
        os.makedirs(dst, exist_ok=True)
        res = os.path.join(src, "outputs", "distribution_result.xlsx")
        if os.path.exists(res):
            shutil.copyfile(res, os.path.join(dst, "distribution_result.xlsx"))
        stdout, answer = _read(src, "stdout.txt"), _read(src, "answer.md").strip()
        t0, t1 = _read(src, "t_start").strip(), _read(src, "t_end").strip()
        total = durations.get(name) or (round(float(t1) - float(t0), 2) if t0 and t1 else None)
        exact, leaked = check_answer(answer, template_from_stdout(stdout))
        log = {
            "runner": a.runner,
            "model": a.model,
            "time_total_s": total,
            "answer_matches_template": exact,
            "answer_has_service_markers": leaked,
            "attachments": sorted(os.listdir(os.path.join(src, "attachments"))),
            "final_answer": answer,
            "skill_stdout": stdout,
        }
        Path(dst, "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(os.path.join(dst, "transcript.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# Агентный прогон ({a.runner} {a.model}) — {ds}, прогон {k}\n\n")
            fh.write(
                "**Пользователь:** пустое сообщение + вложения: "
                + ", ".join(f"`{x}`" for x in log["attachments"])
                + "\n\n"
            )
            fh.write(
                f"**Вывод скрипта навыка:**\n\n```\n{stdout.strip()}\n```\n\n**Ответ агента в чат:**\n\n```text\n{answer}\n```\n\n"
            )
            fh.write(
                f"---\nВремя ответа: {total} с. Ответ совпал с шаблоном: {'да' if exact else 'НЕТ'}; "
                f"служебные маркеры в ответе: {'ДА' if leaked else 'нет'}.\n"
            )
        print(
            f"{ds}/{a.runner}_{k}: {total} с, шаблон {'ок' if exact else 'ИСКАЖЁН'}, маркеры {'ДА' if leaked else 'нет'}"
        )


if __name__ == "__main__":
    main()
