"""Нормализация значений: номера заказов, поставщики, способы доставки, страны, даты, имена."""

import datetime as dt
import unittest

from helpers import COUNTRIES, MARKERS, RULES, N


class TestNormalize(unittest.TestCase):
    def test_order_variants(self):
        rx = RULES["order_number_regex"]
        self.assertEqual(N.parse_order("Номер: S0001234 от 01.02.2026", rx)[0], "S0001234")
        self.assertEqual(N.parse_order("Номер: С0001234 от 01.02.2026", rx)[0], "S0001234")  # кириллица
        self.assertEqual(N.parse_order("номер s 0001234", rx)[0], "S0001234")
        self.assertIsNone(N.parse_order("Номер:  от 01.02.2026", rx)[0])
        self.assertIsNone(N.parse_order("S0001234; S0001235", rx)[0])  # два номера
        self.assertEqual(N.parse_order("S0001234 и снова S0001234", rx)[0], "S0001234")  # один и тот же
        self.assertIsNone(N.parse_order("S00012345", rx)[0])  # 8 цифр

    def test_supplier_key(self):
        lf = RULES["supplier_legal_forms"]
        k = N.supplier_key("Golden Thread Co., Ltd", lf)
        for v in ("GOLDEN  THREAD CO LTD", "golden-thread", " Golden Thread ", "Golden Thread Co.,Ltd."):
            self.assertEqual(N.supplier_key(v, lf), k, v)
        self.assertNotEqual(N.supplier_key("Golden Threads", lf), k)
        self.assertEqual(N.supplier_key("ООО «Альфа»", lf), N.supplier_key("альфа", lf))

    def test_method(self):
        m = RULES["delivery_methods"]
        self.assertEqual(N.norm_method("AIR", m, MARKERS), ("air", "ok"))
        self.assertEqual(N.norm_method("Direct-Truck", m, MARKERS), ("direct truck", "ok"))
        self.assertEqual(N.norm_method("ж/д", m, MARKERS), ("railway", "ok"))
        self.assertEqual(N.norm_method("courier", m, MARKERS), (None, "unknown"))
        self.assertEqual(N.norm_method(None, m, MARKERS), (None, "empty"))
        self.assertEqual(N.norm_method(" - ", m, MARKERS), (None, "empty"))

    def test_china(self):
        for v in ("Китай", "КНР", "china", "CN", "Гонконг", "Hong Kong", "HK", "Hong Kong SAR"):
            self.assertTrue(N.norm_country(v, COUNTRIES, MARKERS)[1], v)
        for v in ("Турция", "Тайвань", "Макао", "Вьетнам", "Перу"):
            self.assertFalse(N.norm_country(v, COUNTRIES, MARKERS)[1], v)
        self.assertIsNone(N.norm_country("", COUNTRIES, MARKERS)[1])
        self.assertIsNone(N.norm_country("н/д", COUNTRIES, MARKERS)[1])

    def test_dates(self):
        self.assertEqual(N.parse_datetime("09.11.2026 08:15"), dt.datetime(2026, 11, 9, 8, 15))
        self.assertEqual(N.parse_datetime("2026-11-09"), dt.datetime(2026, 11, 9))
        self.assertEqual(N.parse_datetime(dt.date(2026, 11, 9)), dt.datetime(2026, 11, 9))
        self.assertIsNone(N.parse_datetime("вчера"))

    def test_names(self):
        fio = "Иванова Елена Петровна"
        for s in ("Иванова Е.", "Иванова Е.П.", "Е. Иванова", "иванова", "Иванова Елена", "ИВАНОВА Е. П."):
            self.assertTrue(N.name_matches(s, fio), s)
        for s in ("Иванова А.", "Иванова Е.А.", "Иванов Е."):
            self.assertFalse(N.name_matches(s, fio), s)
        self.assertTrue(N.name_matches("Фёдорова Е.", "Федорова Елена Ивановна"))


if __name__ == "__main__":
    unittest.main()
