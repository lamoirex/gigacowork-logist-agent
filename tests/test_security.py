"""Защита от Excel-инъекций: значения из выгрузки не должны превращаться в формулы в результате."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import openpyxl

from helpers import DATASETS, N  # задаёт sys.path

import distribute

PAYLOAD = '=HYPERLINK("http://evil.example","Silk Road Apparel")'


def make_poisoned_inputs(folder):
    src = os.path.join(DATASETS, "ds1_base", "input")
    for name in os.listdir(src):
        shutil.copy(os.path.join(src, name), folder)
    path = os.path.join(folder, "1c_ful.xlsx")
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    for col, value in (("Контрагент", PAYLOAD), ("Бизнес единица", "+cmd|' /C calc'!A0")):
        cell = ws.cell(2, headers.index(col) + 1)
        cell.value = value
        cell.data_type = "s"  # в файле 1С это обычный текст
    wb.save(path)


class TestFormulaInjection(unittest.TestCase):
    def test_looks_like_formula(self):
        for v in (PAYLOAD, "=1+1", "@SUM(A1)", "+cmd|x", "-cmd"):
            self.assertTrue(N.looks_like_formula(v), v)
        for v in ("Silk Road", "-5", "+7 999 123-45-67", "", "=", None, "S0001234"):
            self.assertFalse(N.looks_like_formula(v), v)

    def test_no_formulas_in_result_and_value_kept(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            make_poisoned_inputs(d)
            self.assertEqual(distribute.main([d, "--out", out]), 0)
            wb = openpyxl.load_workbook(Path(out, "distribution_result.xlsx"))
            formulas = [
                (ws.title, c.coordinate)
                for ws in wb.worksheets
                for row in ws.iter_rows()
                for c in row
                if c.data_type == "f"
            ]
            self.assertEqual(formulas, [])
            values = [c.value for row in wb["Журнал решений"].iter_rows() for c in row]
            self.assertIn(PAYLOAD, values)  # значение сохранено как есть, только как текст
            attention = [c.value for row in wb["Требует внимания"].iter_rows() for c in row]
            self.assertTrue(any("похоже на формулу" in str(v) for v in attention))


if __name__ == "__main__":
    unittest.main()
