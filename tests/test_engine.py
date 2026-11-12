"""Правила распределения: статус ФУЛ, группа, логист."""

import datetime as dt
import os
import tempfile
import unittest

from helpers import CLOUD_H, COUNTRIES, FUL_H, RULES, STAFF, cloud_row, ful_row, write_wb  # задаёт sys.path

import engine as E  # noqa: E402
from xlreader import detect_tables  # noqa: E402


class TestEngine(unittest.TestCase):
    def run_case(self, cloud, ful, staff=STAFF):
        with tempfile.TemporaryDirectory() as d:
            write_wb(os.path.join(d, "a.xlsx"), {"x": [CLOUD_H] + cloud})
            write_wb(os.path.join(d, "b.xlsx"), {"y": [FUL_H] + ful})
            write_wb(os.path.join(d, "c.xlsx"), {"z": staff})
            tables, _ = detect_tables([os.path.join(d, f) for f in ("a.xlsx", "b.xlsx", "c.xlsx")], RULES["headers"])
            res = E.Engine(RULES, COUNTRIES).run(tables)
        return {d.ful.number: d for d in res.decisions}, res

    def test_history_then_vacation_then_load(self):
        cloud = [
            cloud_row("S0000001", "Acme", "Захарова И."),
            cloud_row("S0000002", "Acme", "Захарова И."),
            cloud_row("S0000003", "Acme", "Лебедев А."),
            cloud_row("S0000004", "Other", "Лебедев А."),
        ]
        dec, _ = self.run_case(cloud, [ful_row("1", "S0000010", "ACME")])
        d = dec["1"]
        self.assertEqual((d.outcome, d.group, d.logist.fio), (E.NEW, "AIR", "Орлова Марина Викторовна"))
        self.assertIn("Захарова", d.reason)

    def test_one_supplier_one_logist(self):
        dec, _ = self.run_case(
            [],
            [
                ful_row("1", "S0000010", "New Co"),
                ful_row("2", "S0000011", "New co.", date=dt.datetime(2026, 11, 10, 10)),
            ],
        )
        self.assertIs(dec["1"].logist, dec["2"].logist)
        self.assertEqual(dec["2"].rule, "Один поставщик — один логист")

    def test_batch_load_balancing(self):
        dec, _ = self.run_case(
            [], [ful_row("1", "S0000010", "A"), ful_row("2", "S0000011", "B", date=dt.datetime(2026, 11, 10, 10))]
        )
        self.assertIsNot(dec["1"].logist, dec["2"].logist)

    def test_head_never_assigned_and_group_never_switched(self):
        dec, _ = self.run_case([], [ful_row("1", "S0000010", "A", method="sea", country="Турция")])
        self.assertEqual(dec["1"].group, "MULTI")
        self.assertIsNone(dec["1"].logist)

    def test_repeat_and_miss(self):
        cloud = [cloud_row("S0000001", "A", "Ким Д.", date=dt.datetime(2026, 11, 8))]
        dec, res = self.run_case(
            cloud,
            [
                ful_row("1", "S0000001", "A"),
                ful_row("2", "S0000002", "A", date=dt.datetime(2026, 11, 5)),
                ful_row("3", "S0000003", "A", date=dt.datetime(2026, 11, 8, 23)),
            ],
        )
        self.assertEqual(dec["1"].outcome, E.REPEAT_CLOUD)
        self.assertEqual(dec["1"].new_comment, "Повторная отправка ФУЛа, 10.11.2026")
        self.assertEqual(dec["2"].outcome, E.POSSIBLE_MISS)
        self.assertEqual(dec["3"].outcome, E.NEW)  # тот же день — не пропуск
        self.assertEqual([r["order"] for r in res.new_rows], ["S0000003"])

    def test_missing_data_is_undefined(self):
        dec, res = self.run_case(
            [],
            [
                ful_row("1", "S0000010", "A", method=None),
                ful_row("2", "S0000011", "B", method="sea", country=None),
                ful_row("3", "S0000012", "C", method="air", country=None),
            ],
        )
        self.assertIsNone(dec["1"].group)
        self.assertIsNone(dec["2"].group)
        self.assertEqual(dec["3"].group, "AIR")
        rows = {r["order"]: r for r in res.new_rows}
        self.assertEqual(rows["S0000010"]["logist"], "не определено")
        self.assertEqual(rows["S0000010"]["method"], "не определено")
        self.assertEqual(rows["S0000012"]["country"], "не определено")
        self.assertNotEqual(rows["S0000012"]["logist"], "не определено")


if __name__ == "__main__":
    unittest.main()
