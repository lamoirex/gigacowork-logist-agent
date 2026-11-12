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
