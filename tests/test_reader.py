"""Распознавание входных файлов по заголовкам."""

import os
import tempfile
import unittest

from helpers import CLOUD_H, FUL_H, RULES, STAFF, ful_row, write_wb  # задаёт sys.path

from xlreader import InputError, detect_tables


class TestReaderRobustness(unittest.TestCase):
    def test_shuffled_columns_title_rows_extra_columns(self):
        with tempfile.TemporaryDirectory() as d:
            hdr = ["Склад", *list(reversed(FUL_H)), "Вес"]
            row = ["X", *list(reversed(ful_row("1", "S0000010", "A"))), 5]
            write_wb(os.path.join(d, "ful.xlsx"), {"Отчёт": [["Шапка отчёта"], [], hdr, row]})
            write_wb(os.path.join(d, "cl.xlsx"), {"Лист1": [CLOUD_H]})
            write_wb(os.path.join(d, "st.xlsx"), {"Лист1": STAFF})
            t, _ = detect_tables([os.path.join(d, f) for f in ("st.xlsx", "ful.xlsx", "cl.xlsx")], RULES["headers"])
            self.assertEqual(t["ful"].header_row, 3)
            self.assertEqual(t["ful"].records[0]["number"], "1")

    def test_single_workbook_three_sheets(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "all.xlsx")
            write_wb(p, {"a": STAFF, "b": [FUL_H, ful_row("1", "S0000010", "A")], "c": [CLOUD_H]})
            t, _ = detect_tables([p], RULES["headers"])
            self.assertEqual(set(t), {"ful", "cloud", "staff"})

    def test_previous_result_is_ignored(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as d:
            write_wb(os.path.join(d, "ful.xlsx"), {"x": [FUL_H, ful_row("1", "S0000010", "A")]})
            write_wb(os.path.join(d, "cl.xlsx"), {"x": [CLOUD_H]})
            write_wb(os.path.join(d, "st.xlsx"), {"x": STAFF})
            old = os.path.join(d, "old_result.xlsx")
            wb = openpyxl.Workbook()
            wb.active.append(CLOUD_H)
            wb.properties.creator = "logist-distribution"
            wb.save(old)
            t, warnings = detect_tables(
                [old] + [os.path.join(d, f) for f in ("ful.xlsx", "cl.xlsx", "st.xlsx")], RULES["headers"]
            )
            self.assertTrue(t["cloud"].file.endswith("cl.xlsx"))
            self.assertEqual(warnings, [])

    def test_missing_column_is_explained(self):
        with tempfile.TemporaryDirectory() as d:
            write_wb(os.path.join(d, "ful.xlsx"), {"x": [[h for h in FUL_H if h != "Контрагент"]]})
            write_wb(os.path.join(d, "cl.xlsx"), {"x": [CLOUD_H]})
            write_wb(os.path.join(d, "st.xlsx"), {"x": STAFF})
            with self.assertRaises(InputError) as cm:
                detect_tables([os.path.join(d, f) for f in ("ful.xlsx", "cl.xlsx", "st.xlsx")], RULES["headers"])
            self.assertIn("Контрагент", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
