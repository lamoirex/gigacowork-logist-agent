"""Детерминированный движок распределения ФУЛ между логистами.

Вход — три распознанные таблицы (xlreader.Table), выход — Result со всеми
решениями. Правила описаны в docs/rules.md, параметры — в references/rules.json.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import normalize as N

# Статусы обработки ФУЛ
NEW = "Новый"
REPEAT_CLOUD = "Повторная отправка (заказ есть в Cloud)"
REPEAT_BATCH = "Повторная отправка (дубль в пачке)"
TECH_DUP = "Технический дубль строки"
POSSIBLE_MISS = "Возможный пропуск"
NO_ORDER = "Нет номера заказа"

BLOCK = "Блокирует"
CHECK = "Проверить"


# ---------------------------------------------------------------- модели


@dataclass(eq=False)
class Employee:
    fio: str
    code: str
    state: str
    position: str
    row: int
    group: str | None = None  # AIR / VED / MULTI
    excluded: str = ""  # причина, по которой не распределяет заказы
    available: bool = False
    unavailable_reason: str = ""
    cloud_names: Counter = field(default_factory=Counter)
    load_cloud: int = 0
    load_batch: int = 0

    @property
    def load(self) -> int:
        return self.load_cloud + self.load_batch

    @property
    def sort_key(self):
        return (N.norm_text(self.fio), N.norm_text(self.code))


@dataclass
class CloudRow:
    row: int
    order: str | None
    order_raw: str
    supplier: str
    supplier_key: str
    logist_raw: str
    employee: Employee | None
    match: str  # ok / ambiguous / unknown / empty
    status: str
    ul_date: object
    comment: str


@dataclass
class Ful:
    row: int
    date: object
    date_raw: str
    number: str
    status: str
    comment: str
    order: str | None
    orders_found: list
    method: str | None
    method_state: str
    method_raw: str
    brand: str
    logist_1c: str
    supplier: str
    supplier_key: str
    country: str | None
    is_china: bool | None
    cutoff_raw: object


@dataclass
class Decision:
    ful: Ful
    outcome: str = ""
    group: str | None = None
    group_reason: str = ""
    logist: Employee | None = None
    rule: str = ""
    reason: str = ""
    candidates: str = ""
    cloud_row: CloudRow | None = None
    first: "Decision | None" = None  # для повтора в пачке — первый ФУЛ заказа
    repeats: list = field(default_factory=list)
    new_comment: str = ""


@dataclass
class Attention:
    priority: str
    ful_number: str
    order: str
    supplier: str
    problem: str
    action: str


@dataclass
class Result:
    decisions: list
    new_rows: list  # list[dict field->value]
    repeats: list  # list[dict]
    misses: list  # list[dict]
    attention: list  # list[Attention]
    employees: list
    cloud_titles: dict
    cloud_order: list
    last_cloud_date: object
    stats: dict


# ---------------------------------------------------------------- движок


class Engine:
    def __init__(self, rules: dict, countries: dict):
        self.r = rules
        self.c = countries
        self.U = rules["undefined"]
        self.markers = {N.norm_text(m) for m in rules["empty_markers"]}
        self.attention: list[Attention] = []

    # ---------- утилиты
    def _val(self, v) -> str:
        return "" if N.is_empty(v, self.markers) else N.to_str(v)

    def _attn(self, priority, ful_number, order, supplier, problem, action):
        self.attention.append(Attention(priority, ful_number or "", order or "", supplier or "", problem, action))

    def group_title(self, g):
        return self.r["groups"][g]["title"] if g else self.U

    # ---------- сотрудники
    def load_staff(self, table):
        emps = []
        av_states = {N.norm_text(s) for s in self.r["available_states"]}
        for rec in table.records:
            fio = self._val(rec.get("name"))
            if not fio:
                continue
            e = Employee(
                fio=fio,
                code=self._val(rec.get("code")),
                state=self._val(rec.get("state")),
                position=self._val(rec.get("position")),
                row=rec["_row"],
            )
            pos = N.norm_text(e.position)
            if any(k in pos for k in self.r["position_excluded_keywords"]):
                e.excluded = f"должность «{e.position}» не распределяет заказы"
            else:
                for g, kws in self.r["position_group_keywords"].items():
                    if any(k in pos for k in kws):
                        e.group = g
                        break
                if not e.group:
                    e.excluded = f"должность «{e.position or 'пусто'}» не относится ни к одной группе"
            st = N.norm_text(e.state)
            if st in av_states:
                e.available = True
            elif "уволен" in st:
                e.unavailable_reason = "уволен"
            elif "отпуск" in st:
                e.unavailable_reason = f"в отпуске ({e.state})"
            elif not st:
                e.unavailable_reason = "состояние не указано"
            else:
                e.unavailable_reason = f"состояние «{e.state}»"
            emps.append(e)
        return emps

    def match_employee(self, short, emps):
        if not short:
            return None, "empty"
        hits = [e for e in emps if N.name_matches(short, e.fio)]
        if len(hits) == 1:
            return hits[0], "ok"
        if len(hits) > 1:
            return None, "ambiguous"
        return None, "unknown"

    # ---------- Cloud
    def load_cloud(self, table, emps):
        rows = []
        pat, pref = self.r["order_number_regex"], self.r["order_number_prefix"]
        load_statuses = {N.norm_text(s) for s in self.r["load_status_values"]}
        for rec in table.records:
            logist_raw = self._val(rec.get("logist"))
            emp, match = self.match_employee(logist_raw, emps)
            cr = CloudRow(
                row=rec["_row"],
                order=N.norm_order(rec.get("order"), pat, pref),
                order_raw=N.to_str(rec.get("order")),
                supplier=self._val(rec.get("supplier")),
                supplier_key=N.supplier_key(rec.get("supplier"), self.r["supplier_legal_forms"]),
                logist_raw=logist_raw,
                employee=emp,
                match=match,
                status=self._val(rec.get("status")),
                ul_date=N.parse_datetime(rec.get("ul_date")),
                comment=self._val(rec.get("comment")),
            )
            if emp:
                emp.cloud_names[logist_raw] += 1
                if N.norm_text(cr.status) in load_statuses:
                    emp.load_cloud += 1
            rows.append(cr)
        # Проблемы сопоставления — по одной записи на имя
        seen = set()
        for cr in rows:
            if cr.match in ("ambiguous", "unknown") and cr.logist_raw not in seen:
                seen.add(cr.logist_raw)
                n = sum(1 for x in rows if x.logist_raw == cr.logist_raw)
                if cr.match == "ambiguous":
                    cands = ", ".join(e.fio for e in emps if N.name_matches(cr.logist_raw, e.fio))
                    self._attn(
                        CHECK,
                        "",
                        "",
                        "",
                        f"Логист «{cr.logist_raw}» в Cloud ({n} строк) подходит нескольким сотрудникам: {cands}. История и загрузка по этим строкам не учтены.",
                        "Уточнить в Cloud, кто ведёт эти заказы (ФИО полностью или табельный номер).",
                    )
                else:
                    self._attn(
                        CHECK,
                        "",
                        "",
                        "",
                        f"Логист «{cr.logist_raw}» из Cloud ({n} строк) не найден в списке сотрудников 1С. Его заказы не учтены в загрузке и истории.",
                        "Проверить, работает ли сотрудник; при необходимости добавить в выгрузку сотрудников.",
                    )
        orders = Counter(cr.order for cr in rows if cr.order)
        for o, n in sorted(orders.items()):
            if n > 1:
                self._attn(
                    CHECK, "", o, "", f"Заказ {o} встречается в Cloud {n} раз(а).", "Проверить дубли в реестре Cloud."
                )
        return rows

    # ---------- ФУЛ
    def load_ful(self, table):
        fuls = []
        pat, pref = self.r["order_number_regex"], self.r["order_number_prefix"]
        for rec in table.records:
            comment = N.to_str(rec.get("comment"))
            order, found = N.parse_order(comment, pat, pref)
            method, mstate = N.norm_method(rec.get("method"), self.r["delivery_methods"], self.markers)
            country, is_china = N.norm_country(rec.get("country"), self.c, self.markers)
            supplier = self._val(rec.get("supplier"))
            fuls.append(
                Ful(
                    row=rec["_row"],
                    date=N.parse_datetime(rec.get("date")),
                    date_raw=N.to_str(rec.get("date")),
                    number=self._val(rec.get("number")),
                    status=self._val(rec.get("status")),
                    comment=comment,
                    order=order,
                    orders_found=found,
                    method=method,
                    method_state=mstate,
                    method_raw=self._val(rec.get("method")),
                    brand=self._val(rec.get("brand")),
                    logist_1c=self._val(rec.get("logist")),
                    supplier=supplier,
                    supplier_key=N.supplier_key(supplier, self.r["supplier_legal_forms"]) if supplier else "",
                    country=country,
                    is_china=is_china,
                    cutoff_raw=rec.get("cutoff"),
                )
            )
        return fuls

    # ---------- группа
    def determine_group(self, f: Ful):
        if f.method_state == "empty":
            return None, "не указан способ доставки"
        if f.method_state == "unknown":
            return None, f"незнакомый способ доставки «{f.method_raw}»"
        if f.method == self.r["air_method"]:
            return "AIR", "способ доставки air → авиаперевозки"
        if f.is_china is None:
            return (
                None,
                f"способ доставки {f.method}, страна отправления не указана — для не-air группа зависит от страны",
            )
        if f.is_china:
            return "VED", f"{f.method}, страна «{f.country}» (Китай) → ВЭД"
        return "MULTI", f"{f.method}, страна «{f.country}» (не Китай) → мультимодальные перевозки"

    # ---------- логист
    def _candidates_text(self, group, emps):
        parts = []
        for e in sorted((e for e in emps if e.group == group and not e.excluded), key=lambda e: e.sort_key):
            st = f"в работе {e.load}" if e.available else e.unavailable_reason
            parts.append(f"{self.short(e)}: {st}")
        return "; ".join(parts)

    def least_loaded(self, group, emps):
        pool = [e for e in emps if e.group == group and not e.excluded and e.available]
        if not pool:
            return None
        return sorted(pool, key=lambda e: (e.load, e.load_batch) + e.sort_key)[0]

    def choose_logist(self, f: Ful, group, emps, cloud, pins, commit=True):
        """-> (employee | None, rule, reason). При commit=False ничего не меняет (для пропусков)."""
        pin_key = (f.supplier_key, group)
        if f.supplier_key and pin_key in pins:
            e = pins[pin_key]
            return (
                e,
                "Один поставщик — один логист",
                (
                    f"{self.short(e)}: поставщик «{self.sup_name(f)}» в этой пачке уже закреплён за логистом "
                    f"{self.short(e)} (один поставщик — один логист); в работе {e.load}"
                ),
            )

        members = [e for e in emps if e.group == group and not e.excluded]
        hist = Counter()
        last = {}
        total = 0
        if f.supplier_key:
            for cr in cloud:
                if cr.supplier_key != f.supplier_key:
                    continue
                total += 1
                if cr.employee and cr.employee in members:
                    hist[cr.employee.fio] += 1
                    d = cr.ul_date or N.parse_datetime("01.01.1900")
                    if cr.employee.fio not in last or d > last[cr.employee.fio]:
                        last[cr.employee.fio] = d
        pref = None
        if hist:
            by_fio = {e.fio: e for e in members}
            ranked = sorted(
                hist, key=lambda fio: (-hist[fio], -last[fio].timestamp(), by_fio[fio].load) + by_fio[fio].sort_key
            )
            pref = by_fio[ranked[0]]
            hist_txt = ", ".join(f"{self.short(by_fio[x])} — {hist[x]}" for x in ranked)

        if pref and pref.available:
            e, rule = pref, "История работы с поставщиком"
            reason = (
                f"{self.short(e)}: вёл(а) {hist[e.fio]} из {total} заказов поставщика «{self.sup_name(f)}» в Cloud "
                f"(история в группе: {hist_txt}); работает, в работе {e.load}"
            )
        else:
            e = self.least_loaded(group, emps)
            if pref:
                why = f"предпочтительный по истории {self.short(pref)} ({hist[pref.fio]} из {total} заказов) недоступен: {pref.unavailable_reason}"
                rule = "Наименее загруженный: предпочтительный недоступен"
            elif total:
                why = f"заказы поставщика в Cloud вели не сотрудники группы «{self.group_title(group)}»"
                rule = "Наименее загруженный: нет истории в группе"
            elif not f.supplier_key:
                why = "поставщик не указан, история неизвестна"
                rule = "Наименее загруженный: поставщик не указан"
            else:
                why = "новый поставщик — в Cloud нет его заказов"
                rule = "Наименее загруженный: новый поставщик"
            if e is None:
                return (
                    None,
                    "Нет доступных логистов в группе",
                    (
                        f"{why}; в группе «{self.group_title(group)}» нет доступных логистов — заказ остаётся без логиста, "
                        f"в другую группу не передаётся"
                    ),
                )
            reason = f"{why}; назначен наименее загруженный в группе: {self.short(e)} (в работе {e.load})"

        if commit and e is not None:
            if f.supplier_key:
                pins[pin_key] = e
        return e, rule, reason

    # ---------- формат имени для Cloud
    def prepare_short_names(self, emps, cloud):
        styles = Counter(N.short_style(cr.logist_raw) for cr in cloud if cr.logist_raw)
        styles.pop("other", None)
        self.style = styles.most_common(1)[0][0] if styles else "surname_i"
        for e in emps:
            if e.cloud_names:
                # самое частое написание; при равенстве — по алфавиту
                e.short_name = sorted(e.cloud_names.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            else:
                e.short_name = N.format_short(e.fio, self.style)

    def sup_name(self, f):
        return self.cloud_supplier_names.get(f.supplier_key) or f.supplier or self.U

    def short(self, e):
        return getattr(e, "short_name", None) or e.fio
