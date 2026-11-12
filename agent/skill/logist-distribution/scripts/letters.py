"""Черновики писем логистам по результатам распределения.

Каждому логисту, которому в пачке назначены новые заказы, — одно письмо: какие заказы и почему
именно он (по правилу распределения), плюс повторные отправки ФУЛ по заказам, которые он уже ведёт.
Письма — черновики: агент их не отправляет, их проверяет и отправляет человек.
"""

from __future__ import annotations

from collections import defaultdict

import engine as E

SIGNATURE_PLACEHOLDER = "[подпись]"

RULE_EXPLAINED = {
    "История работы с поставщиком": "вы чаще всех вели этого поставщика",
    "Один поставщик — один логист": "поставщик уже закреплён за вами в этой пачке",
}


LEAST_LOADED_REASONS = {
    "предпочтительный недоступен": "основной логист поставщика сейчас недоступен",
    "новый поставщик": "это новый поставщик",
    "нет истории в группе": "у поставщика нет истории в вашей группе",
    "поставщик не указан": "поставщик в ФУЛ не указан",
}


def _why(rule: str) -> str:
    if rule in RULE_EXPLAINED:
        return RULE_EXPLAINED[rule]
    if rule.startswith("Наименее загруженный"):
        cause = LEAST_LOADED_REASONS.get(rule.split(":", 1)[-1].strip())
        return f"{cause}, а у вас наименьшая загрузка в группе" if cause else "у вас наименьшая загрузка в группе"
    return rule.lower()


def _greeting(fio: str) -> str:
    parts = fio.split()
    return " ".join(parts[1:3]) if len(parts) >= 2 else fio


def _fmt_date(d, fmt, undefined):
    return d.strftime(fmt) if d else undefined


def build_letters(res: E.Result, eng: E.Engine) -> list[dict]:
    fmt = eng.r["date_format"]
    U = eng.U
    new_by_emp = defaultdict(list)
    repeats_by_emp = defaultdict(list)
    for d in res.decisions:
        if d.outcome == E.NEW and d.logist is not None:
            new_by_emp[d.logist].append(d)
        elif d.outcome == E.REPEAT_CLOUD and d.cloud_row and d.cloud_row.employee and d.cloud_row.employee.available:
            repeats_by_emp[d.cloud_row.employee].append(d)

    dates = [d.ful.date for d in res.decisions if d.outcome == E.NEW and d.ful.date]
    period = ""
    if dates:
        lo, hi = min(dates).strftime(fmt), max(dates).strftime(fmt)
        period = lo if lo == hi else f"{lo}–{hi}"

    letters = []
    for emp in sorted(set(new_by_emp) | set(repeats_by_emp), key=lambda e: e.sort_key):
        new, reps = new_by_emp.get(emp, []), repeats_by_emp.get(emp, [])
        subject_parts = []
        if new:
            subject_parts.append(f"новые заказы на сопровождение: {len(new)}")
        if reps:
            subject_parts.append(f"повторные ФУЛ: {len(reps)}")
        subject = "Распределение ФУЛ" + (f" за {period}" if period else "") + " — " + ", ".join(subject_parts)

        lines = [f"{_greeting(emp.fio)}, добрый день!", ""]
        if new:
            lines.append(
                f"За вами закреплены новые заказы ({len(new)}). Строки добавлены в реестр Cloud со статусом «{eng.r['new_row_status']}»."
            )
            for i, d in enumerate(new, start=1):
                f = d.ful
                cut = f.cutoff_raw
                cut_txt = cut.strftime(fmt) if hasattr(cut, "strftime") else (str(cut) if cut not in (None, "") else U)
                details = ", ".join(
                    x
                    for x in (
                        eng.sup_name(f),
                        f.brand or None,
                        f.method_raw or None,
                        f.country or "страна не указана",
                        f"cut-off {cut_txt}",
                    )
                    if x
                )
                lines.append(f"{i}. {f.order} — {details}. Почему вы: {_why(d.rule)}.")
                if d.new_comment:
                    lines.append(f"   {d.new_comment}.")
        if reps:
            if new:
                lines.append("")
            lines.append("Повторно пришли упаковочные листы по заказам, которые вы ведёте:")
            for d in reps:
                lines.append(
                    f"– {d.ful.order}: ФУЛ {d.ful.number or U} от {_fmt_date(d.ful.date, fmt, U)} (статус в Cloud: {d.cloud_row.status or U})."
                )
            lines.append("Проверьте, есть ли изменения относительно предыдущего ФУЛ.")
        lines += [
            "",
            "Обоснование по каждому заказу — в файле распределения, лист «Журнал решений».",
            "",
            "С уважением,",
            SIGNATURE_PLACEHOLDER,
        ]

        letters.append(
            {
                "logist": eng.short(emp),
                "fio": emp.fio,
                "new_orders": [d.ful.order for d in new],
                "repeat_orders": [d.ful.order for d in reps],
                "subject": subject,
                "body": "\n".join(lines),
            }
        )
    return letters
