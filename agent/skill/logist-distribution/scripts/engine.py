"""Детерминированный движок распределения ФУЛ между логистами.

Вход — три распознанные таблицы (xlreader.Table), выход — Result со всеми
решениями. Правила описаны в docs/rules.md, параметры — в references/rules.json.
"""

from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import normalize as N

# Статусы обработки ФУЛ
NEW = "Новый"
REPEAT_CLOUD = "Повторная отправка (заказ есть в Cloud)"
REPEAT_BATCH = "Повторная отправка (дубль в пачке)"
TECH_DUP = "Технический дубль строки"
POSSIBLE_MISS = "Возможный пропуск"
NO_ORDER = "Нет номера заказа"

BLOCK = "Срочно"
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
    first: Decision | None = None  # для повтора в пачке — первый ФУЛ заказа
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
    kind: str = ""


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

    def _attn(self, priority, ful_number, order, supplier, problem, action, kind=""):
        self.attention.append(Attention(priority, ful_number or "", order or "", supplier or "", problem, action, kind))

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
        self._flag_formula_like(fuls)
        return fuls

    def _flag_formula_like(self, fuls):
        """Значения, похожие на формулу Excel, — на проверку человеку до переноса в Cloud."""
        fields = (
            ("поставщик", "supplier"),
            ("бренд", "brand"),
            ("страна", "country"),
            ("способ доставки", "method_raw"),
        )
        for f in fuls:
            bad = [title for title, attr in fields if N.looks_like_formula(getattr(f, attr))]
            if bad:
                self._attn(
                    CHECK,
                    f.number,
                    f.order,
                    f.supplier,
                    f"Значение похоже на формулу Excel ({', '.join(bad)}). В результате записано как текст.",
                    "Проверить данные в 1С перед переносом в Cloud: так может выглядеть ошибка ввода или вредоносная ссылка.",
                )

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
        return sorted(pool, key=lambda e: (e.load, e.load_batch, *e.sort_key))[0]

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
        total = unmatched = 0
        if f.supplier_key:
            for cr in cloud:
                if cr.supplier_key != f.supplier_key:
                    continue
                total += 1
                if cr.match in ("ambiguous", "unknown"):
                    unmatched += 1
                if cr.employee and cr.employee in members:
                    hist[cr.employee.fio] += 1
                    d = cr.ul_date or N.parse_datetime("01.01.1900")
                    if cr.employee.fio not in last or d > last[cr.employee.fio]:
                        last[cr.employee.fio] = d
        pref = None
        if hist:
            by_fio = {e.fio: e for e in members}
            ranked = sorted(
                hist, key=lambda fio: (-hist[fio], -last[fio].timestamp(), by_fio[fio].load, *by_fio[fio].sort_key)
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
            elif unmatched:
                why = (
                    f"у поставщика {total} заказ(ов) в Cloud, но их логисты не сопоставлены однозначно "
                    f"с сотрудниками группы «{self.group_title(group)}» — история не учитывается"
                )
                rule = "Наименее загруженный: нет истории в группе"
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

        if commit and e is not None and f.supplier_key:
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

    # ---------- основной проход
    def run(self, tables) -> Result:
        emps = self.load_staff(tables["staff"])
        cloud = self.load_cloud(tables["cloud"], emps)
        fuls = self.load_ful(tables["ful"])
        self.prepare_short_names(emps, cloud)

        cloud_by_order = {}
        for cr in cloud:
            if cr.order and cr.order not in cloud_by_order:
                cloud_by_order[cr.order] = cr
        cloud_dates = [cr.ul_date for cr in cloud if cr.ul_date]
        last_cloud = max(cloud_dates) if cloud_dates else None
        cloud_supplier_names = {}
        for cr in cloud:
            if cr.supplier_key and cr.supplier_key not in cloud_supplier_names:
                cloud_supplier_names[cr.supplier_key] = cr.supplier
        self.cloud_supplier_names = cloud_supplier_names

        far = N.parse_datetime("31.12.2999")
        order_idx = sorted(
            range(len(fuls)), key=lambda i: (fuls[i].date or far, N.norm_text(fuls[i].number), fuls[i].row)
        )
        decisions = {}
        first_by_order = {}
        seen_lines = set()

        # Проход 1: статус каждого ФУЛ
        for i in order_idx:
            f = fuls[i]
            d = Decision(ful=f)
            decisions[i] = d
            if not f.order:
                d.outcome = NO_ORDER
                if f.orders_found:
                    d.reason = f"в комментарии несколько разных номеров заказа: {', '.join(f.orders_found)}"
                else:
                    d.reason = f"в комментарии нет номера заказа (комментарий: «{f.comment or 'пусто'}»)"
                self._attn(
                    BLOCK,
                    f.number,
                    "",
                    f.supplier,
                    f"ФУЛ без однозначного номера заказа: {d.reason}. Строка Cloud не создана.",
                    "Уточнить номер заказа в 1С и внести вручную.",
                    kind="no_order",
                )
                continue
            line_key = (N.norm_text(f.number), f.order)
            if f.number and line_key in seen_lines:
                d.outcome = TECH_DUP
                d.first = first_by_order.get(f.order)
                d.reason = f"строка ФУЛ {f.number} по заказу {f.order} повторяется в выгрузке — учтена один раз"
                self._attn(
                    CHECK,
                    f.number,
                    f.order,
                    f.supplier,
                    "Одна и та же строка ФУЛ дважды в выгрузке 1С.",
                    "Проверить выгрузку; агент учёл её один раз.",
                )
                continue
            seen_lines.add(line_key)
            date_txt = f.date.strftime(self.r["date_format"]) if f.date else self.U
            if f.order in cloud_by_order:
                cr = cloud_by_order[f.order]
                d.outcome = REPEAT_CLOUD
                d.cloud_row = cr
                d.reason = f"заказ {f.order} уже есть в Cloud (строка {cr.row}, логист {cr.logist_raw or self.U}, статус {cr.status or self.U})"
                if N.norm_text(cr.status) not in {N.norm_text(s) for s in self.r["load_status_values"]}:
                    self._attn(
                        CHECK,
                        f.number,
                        f.order,
                        f.supplier,
                        f"Повторная отправка ФУЛа по заказу со статусом «{cr.status or 'пусто'}» в Cloud.",
                        "Проверить, не нужно ли вернуть заказ в работу логисту.",
                    )
                continue
            if f.order in first_by_order:
                d.outcome = REPEAT_BATCH
                d.first = first_by_order[f.order]
                d.first.repeats.append(d)
                d.reason = f"заказ {f.order} уже встречался в этой пачке (ФУЛ {d.first.ful.number or self.U})"
                continue
            first_by_order[f.order] = d
            if f.date is None:
                d.outcome = NEW
                self._attn(
                    CHECK,
                    f.number,
                    f.order,
                    f.supplier,
                    f"Не распознана дата ФУЛ («{f.date_raw or 'пусто'}»).",
                    "Проверить дату в 1С; «Дата получения УЛ» оставлена «не определено».",
                )
            elif last_cloud and (
                f.date.date() < last_cloud.date() if self.r["possible_miss_compare_by_day"] else f.date < last_cloud
            ):
                d.outcome = POSSIBLE_MISS
                d.reason = (
                    f"заказа нет в Cloud, но ФУЛ от {date_txt} раньше последней записи Cloud "
                    f"({last_cloud.strftime(self.r['date_format'])})"
                )
            else:
                d.outcome = NEW

        # Проход 2: группа и логист для новых, затем предложения для пропусков
        pins = {}
        supplier_groups = defaultdict(set)
        for phase in (NEW, POSSIBLE_MISS):
            for i in order_idx:
                d = decisions[i]
                if d.outcome != phase:
                    continue
                f = d.ful
                d.group, d.group_reason = self.determine_group(f)
                if d.group is None:
                    d.rule = "Группа не определена"
                    d.reason = (
                        d.reason + "; " if d.reason else ""
                    ) + f"группа не определена: {d.group_reason}; логист не назначается"
                    if phase == NEW:
                        self._attn(
                            BLOCK,
                            f.number,
                            f.order,
                            f.supplier,
                            f"Группа не определена: {d.group_reason}. Логист не назначен.",
                            "Уточнить способ доставки / страну в 1С и назначить логиста вручную.",
                            kind="no_group",
                        )
                    continue
                if phase == NEW:
                    supplier_groups[f.supplier_key].add(d.group)
                e, rule, reason = self.choose_logist(f, d.group, emps, cloud, pins, commit=(phase == NEW))
                d.logist, d.rule = e, rule
                d.reason = (d.reason + "; " if d.reason else "") + reason
                d.candidates = self._candidates_text(d.group, emps)
                if phase == NEW and e is not None:
                    e.load_batch += 1 if self.r["count_batch_assignments_in_load"] else 0
                if phase == NEW and e is None:
                    self._attn(
                        BLOCK,
                        f.number,
                        f.order,
                        f.supplier,
                        f"В группе «{self.group_title(d.group)}» нет доступных логистов ({d.candidates or 'нет сотрудников'}).",
                        "Назначить логиста вручную; в другую группу агент заказ не передаёт.",
                        kind="no_logist",
                    )
                if phase == NEW and f.method == self.r["air_method"] and f.is_china is None:
                    self._attn(
                        CHECK,
                        f.number,
                        f.order,
                        f.supplier,
                        "Не указана страна отправления (для air на группу не влияет).",
                        "Дополнить страну в Cloud.",
                    )

        for key, groups in sorted(supplier_groups.items()):
            if key and len(groups) > 1:
                name = next(
                    (self.sup_name(decisions[i].ful) for i in order_idx if decisions[i].ful.supplier_key == key), key
                )
                titles = ", ".join(sorted(self.group_title(g) for g in groups))
                self._attn(
                    CHECK,
                    "",
                    "",
                    name,
                    f"У поставщика в пачке заказы разных групп ({titles}). Правило «один поставщик — один логист» применено внутри каждой группы.",
                    "Решить, нужен ли один логист на поставщика во всех группах.",
                )

        # Похожие, но не совпавшие названия поставщиков
        known = sorted(cloud_supplier_names)
        flagged = set()
        for i in order_idx:
            f = decisions[i].ful
            if not f.supplier_key or f.supplier_key in cloud_supplier_names or f.supplier_key in flagged:
                continue
            for k in known:
                if difflib.SequenceMatcher(None, f.supplier_key, k).ratio() >= self.r["supplier_similarity_threshold"]:
                    flagged.add(f.supplier_key)
                    self._attn(
                        CHECK,
                        f.number,
                        f.order,
                        f.supplier,
                        f"Поставщик «{f.supplier}» не найден в Cloud, но похож на «{cloud_supplier_names[k]}». Обработан как новый.",
                        "Проверить, один ли это поставщик; если да — переназначить логиста по истории.",
                    )
                    break

        # Пустые поля новой строки
        for i in order_idx:
            d = decisions[i]
            if d.outcome != NEW:
                continue
            f = d.ful
            gaps = [
                n
                for n, v in (
                    ("поставщик", f.supplier),
                    ("бренд", f.brand),
                    ("cut-off", N.to_str(f.cutoff_raw) if not N.is_empty(f.cutoff_raw, self.markers) else ""),
                )
                if not v
            ]
            if gaps:
                self._attn(
                    CHECK,
                    f.number,
                    f.order,
                    f.supplier,
                    f"В 1С не заполнено: {', '.join(gaps)}. В строке Cloud — «не определено».",
                    "Дополнить данные в Cloud вручную.",
                )

        return self._build_result(fuls, order_idx, decisions, emps, cloud, cloud_supplier_names, last_cloud, tables)

    # ---------- результат
    def _comment_for_repeats(self, reps):
        dates = []
        for r in reps:
            dtxt = r.ful.date.strftime(self.r["date_format"]) if r.ful.date else self.U
            dates.append(self.r["repeat_comment_template"].format(date=dtxt))
        return self.r["comment_separator"].join(dates)

    def _cell(self, v):
        return v if v not in (None, "") else self.U

    def _build_result(self, fuls, order_idx, decisions, emps, cloud, cloud_supplier_names, last_cloud, tables):
        cloud_tbl = tables["cloud"]
        order = list(self.r["cloud_columns_order"])
        # порядок колонок как в реестре заказчика
        present = [f for f in cloud_tbl.header_order if f in order]
        cloud_order = present + [f for f in order if f not in present]
        titles = {f: cloud_tbl.titles.get(f) or self.r["cloud_default_titles"][f] for f in order}

        def make_row(d):
            f = d.ful
            cut = f.cutoff_raw
            cut_v = None if N.is_empty(cut, self.markers) else (N.parse_datetime(cut) or N.to_str(cut))
            return {
                "order": f.order,
                "supplier": cloud_supplier_names.get(f.supplier_key) or f.supplier or self.U,
                "brand": self._cell(f.brand),
                "logist": self.short(d.logist) if d.logist else self.U,
                "status": self.r["new_row_status"],
                "confirm_date": None,
                "cutoff": cut_v if cut_v not in (None, "") else self.U,
                "ul_date": f.date if f.date else self.U,
                "country": self._cell(f.country),
                "method": self._cell(f.method_raw if f.method_state != "empty" else ""),
                "docs_date": None,
                "comment": self._comment_for_repeats(d.repeats) if d.repeats else None,
            }

        new_rows, repeats, misses = [], [], []
        for i in order_idx:
            d = decisions[i]
            f = d.ful
            if d.outcome == NEW:
                row = make_row(d)
                d.new_comment = row["comment"] or ""
                new_rows.append({"_ful": f.number, **row})
            elif d.outcome == POSSIBLE_MISS:
                row = make_row(d)
                d.new_comment = row["comment"] or ""
                misses.append({"_d": d, **row})
            elif d.outcome == REPEAT_CLOUD:
                cr = d.cloud_row
                add = self._comment_for_repeats([d])
                d.new_comment = add
                repeats.append({"d": d, "cloud_row": cr, "add": add})
            elif d.outcome in (REPEAT_BATCH,):
                d.new_comment = self._comment_for_repeats([d])

        # Повторы одного заказа из Cloud — комментарий накапливается
        by_order = defaultdict(list)
        for rp in repeats:
            by_order[rp["cloud_row"].order].append(rp)
        sep = self.r["comment_separator"]
        for items in by_order.values():
            cr = items[0]["cloud_row"]
            acc = cr.comment
            for rp in items:
                acc = (acc + sep if acc else "") + rp["add"]
                rp["full_comment"] = acc

        stats = Counter(decisions[i].outcome for i in order_idx)
        assigned = sum(1 for r in new_rows if r["logist"] != self.U)
        blockers = sum(1 for a in self.attention if a.priority == BLOCK)
        return Result(
            decisions=[decisions[i] for i in order_idx],
            new_rows=new_rows,
            repeats=repeats,
            misses=misses,
            attention=sorted(self.attention, key=lambda a: (a.priority != BLOCK,)),
            employees=emps,
            cloud_titles=titles,
            cloud_order=cloud_order,
            last_cloud_date=last_cloud,
            stats={
                "ful_total": len(fuls),
                "new_rows": len(new_rows),
                "new_rows_with_logist": assigned,
                "new_rows_without_logist": len(new_rows) - assigned,
                "repeats_cloud": stats.get(REPEAT_CLOUD, 0),
                "repeats_batch": stats.get(REPEAT_BATCH, 0),
                "tech_duplicates": stats.get(TECH_DUP, 0),
                "possible_misses": stats.get(POSSIBLE_MISS, 0),
                "no_order": stats.get(NO_ORDER, 0),
                "attention_total": len(self.attention),
                "attention_blocking": blockers,
            },
        )
