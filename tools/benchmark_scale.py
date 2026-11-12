#!/usr/bin/env python3
"""Нагрузочный тест навыка: время и память на пачках разного размера.

    python3 tools/benchmark_scale.py                 # сценарии по умолчанию
    python3 tools/benchmark_scale.py --ful 65 --cloud 2000

Данные генерируются детерминированно (seed): поставщики, сотрудники трёх групп, история Cloud
со статусами «в работе» и confirm, ФУЛ с повторами и пропусками. Время — полный запуск навыка
(чтение Excel → правила → запись результата), память — пик процесса (ru_maxrss).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import resource
import subprocess
import sys
import tempfile
import time

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "agent", "skill", "logist-distribution", "scripts", "distribute.py")

SCENARIOS = [  # (ФУЛ в пачке, строк в реестре Cloud, сотрудников)
    (65, 2_000, 30),  # типичная пачка из БТ
    (500, 20_000, 60),
    (5_000, 100_000, 120),
]
GROUPS = [
    ("Специалист по авиаперевозкам", "air", ["Германия", "Италия", "Вьетнам"]),
    ("Специалист по работе с ВЭД документацией", "sea", ["Китай", "Гонконг"]),
    ("Специалист по мультимодальным перевозкам", "truck", ["Турция", "Индия", "Узбекистан"]),
]
SURNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Орлов", "Лебедев", "Ким", "Волков", "Зайцев"]


def write(path, header, rows):
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def generate(folder, n_ful, n_cloud, n_staff, seed=7):
    rnd = random.Random(seed)
    staff, short = [], []
    for i in range(n_staff):
        pos, _, _ = GROUPS[i % 3]
        state = rnd.choices(["Работает", "Ежегодный отпуск", "Уволен"], [8, 1, 1])[0]
        fio = f"{SURNAMES[i % 10]}{i} Иван Петрович"
        staff.append([fio, state, pos, str(1000 + i)])
        short.append((f"{SURNAMES[i % 10]}{i} И.", i % 3))
    write(os.path.join(folder, "staff.xlsx"), ["Наименование", "Текущее состояние", "Должность", "Код"], staff)

    suppliers = [(f"Supplier {k} Co., Ltd", k % 3) for k in range(max(30, n_cloud // 40))]
    base = dt.datetime(2026, 1, 1)
    cloud = []
    for i in range(n_cloud):
        sup, g = rnd.choice(suppliers)
        logist = rnd.choice([s for s, sg in short if sg == g])
        _, method, countries = GROUPS[g]
        date = base + dt.timedelta(minutes=i * (300_000 // max(n_cloud, 1)))
        status = "в работе" if rnd.random() < 0.15 else "confirm"
        cloud.append(
            [
                f"S{1_000_000 + i:07d}",
                sup,
                "Brand",
                logist,
                status,
                None,
                None,
                date,
                rnd.choice(countries),
                method,
                None,
                None,
            ]
        )
    last = cloud[-1][7]
    write(
        os.path.join(folder, "cloud.xlsx"),
        [
            "Заказ",
            "Поставщик",
            "Бренд",
            "Логист",
            "статус",
            "дата подтверждения",
            "cut-off",
            "Дата получения УЛ",
            "Страна отправления",
            "Способ доставки",
            "Документы получены",
            "Комментарий",
        ],
        cloud,
    )

    ful = []
    for i in range(n_ful):
        sup, g = rnd.choice(suppliers)
        _, method, countries = GROUPS[g]
        r = rnd.random()
        order = rnd.choice(cloud)[0] if r < 0.05 else f"S{5_000_000 + i:07d}"  # 5% — повторная отправка
        date = last + dt.timedelta(hours=1 + i % 48) if r > 0.02 else last - dt.timedelta(days=3)
        ful.append(
            [
                date,
                last + dt.timedelta(days=30),
                f"УЛ-{i:06d}",
                "Проведен",
                f"Номер: {order} от 01.11.2026",
                method,
                "Brand",
                None,
                sup.upper(),
                rnd.choice(countries),
            ]
        )
    write(
        os.path.join(folder, "ful.xlsx"),
        [
            "Дата",
            "Cut-off date",
            "Номер",
            "Статус",
            "Комментарий",
            "Способ доставки",
            "Бизнес единица",
            "Логист",
            "Контрагент",
            "Страна",
        ],
        ful,
    )


def run(n_ful, n_cloud, n_staff):
    with tempfile.TemporaryDirectory() as d:
        inp, out = os.path.join(d, "in"), os.path.join(d, "out")
        os.makedirs(inp)
        generate(inp, n_ful, n_cloud, n_staff)
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        t0 = time.perf_counter()
        r = subprocess.run([sys.executable, SCRIPT, inp, "--out", out], capture_output=True, text=True)
        elapsed = time.perf_counter() - t0
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        scale = 1024 if sys.platform != "darwin" else 1024 * 1024  # ru_maxrss: КБ в Linux, байты в macOS
        return elapsed, max(peak, before) / scale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ful", type=int)
    ap.add_argument("--cloud", type=int, default=2000)
    ap.add_argument("--staff", type=int, default=30)
    a = ap.parse_args()
    scenarios = [(a.ful, a.cloud, a.staff)] if a.ful else SCENARIOS
    print("| ФУЛ в пачке | Строк Cloud | Сотрудников | Время, с | Пик памяти, МБ |")
    print("|---:|---:|---:|---:|---:|")
    for n_ful, n_cloud, n_staff in scenarios:
        elapsed, mem = run(n_ful, n_cloud, n_staff)
        print(f"| {n_ful:,} | {n_cloud:,} | {n_staff} | {elapsed:.2f} | {mem:.0f} |".replace(",", " "), flush=True)


if __name__ == "__main__":
    main()
