"""Черновики писем логистам."""

import os
import unittest

from helpers import COUNTRIES, DATASETS, RULES  # задаёт sys.path

import engine as E
import letters as L
from xlreader import detect_tables


def run_dataset(ds):
    src = os.path.join(DATASETS, ds, "input")
    tables, _ = detect_tables([os.path.join(src, f) for f in sorted(os.listdir(src))], RULES["headers"])
    eng = E.Engine(RULES, COUNTRIES)
    return eng.run(tables), eng


class TestLetters(unittest.TestCase):
    def test_every_assigned_order_is_in_exactly_one_letter(self):
        res, eng = run_dataset("ds1_base")
        letters = L.build_letters(res, eng)
        assigned = sorted(d.ful.order for d in res.decisions if d.outcome == E.NEW and d.logist)
        in_letters = sorted(o for lt in letters for o in lt["new_orders"])
        self.assertEqual(in_letters, assigned)
        self.assertEqual([lt["logist"] for lt in letters], ["Ким Д.", "Лебедев А.", "Орлова М.", "Тихонов П."])

    def test_letter_content(self):
        res, eng = run_dataset("ds1_base")
        by_logist = {lt["logist"]: lt for lt in L.build_letters(res, eng)}
        lebedev = by_logist["Лебедев А."]
        self.assertEqual(len(lebedev["new_orders"]), 7)
        self.assertTrue(lebedev["body"].startswith("Артём Игоревич, добрый день!"))
        self.assertIn("основной логист поставщика сейчас недоступен", lebedev["body"])  # Захарова в отпуске
        self.assertIn("страна не указана", lebedev["body"])  # S0004113 без страны
        self.assertTrue(lebedev["body"].endswith(L.SIGNATURE_PLACEHOLDER))
        # повторные ФУЛ — тому, кто ведёт заказ в Cloud
        self.assertEqual(sorted(by_logist["Орлова М."]["repeat_orders"]), ["S0003050", "S0003050", "S0003120"])
        self.assertEqual(by_logist["Ким Д."]["repeat_orders"], ["S0003077"])

    def test_no_letters_to_unavailable_or_without_logist(self):
        res, eng = run_dataset("ds3_edge")
        letters = L.build_letters(res, eng)
        names = {lt["fio"] for lt in letters}
        unavailable = {e.fio for e in res.employees if not e.available}
        self.assertFalse(names & unavailable)
        self.assertNotIn("не определено", " ".join(lt["logist"] for lt in letters))


if __name__ == "__main__":
    unittest.main()
