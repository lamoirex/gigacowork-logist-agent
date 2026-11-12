"""Harness GigaChat: поиск решения в журнале и проверка ответов на уточняющие вопросы (без сети)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import DATASETS  # задаёт sys.path (в том числе tools/)

import distribute
import gigachat_agent as G


class TestFollowups(unittest.TestCase):
    def test_check_followup(self):
        must = [["Захаров"], ["отпуск"], ["наименее загруж", "наименьш"]]
        good = "Основной логист Захарова И. в отпуске, поэтому назначен наименее загруженный — Лебедев А."
        self.assertEqual(G.check_followup(good, must), [])
        self.assertEqual(
            G.check_followup("Назначен Лебедев А.", must), ["Захаров", "отпуск", "наименее загруж | наименьш"]
        )
        self.assertEqual(G.check_followup(None, ["x"]), ["x"])
        # формы слова и ё/е
        self.assertEqual(G.check_followup("Миронова уволилась", [["уволен", "уволил"]]), [])
        self.assertEqual(G.check_followup("ранее вёл", [["ранее вел"]]), [])
        # неверная причина проваливает даже ответ с нужными фактами
        wrong = "Смирнов вёл 2 из 3 заказов поставщика и у него наименьшая загрузка"
        self.assertEqual(
            G.check_followup(wrong, [["Смирнов"], ["2 из 3"]], ["наименьш"]), ["неверное утверждение: «наименьш»"]
        )

    def test_get_decision(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertIn("error", G.get_decision("S0004103", out))  # распределения ещё не было
            distribute.main([os.path.join(DATASETS, "ds1_base", "input"), "--out", out])
            d = G.get_decision("Почему заказ S0004103 отдан Лебедеву?", out)["decisions"]
            self.assertEqual([x["logist"] for x in d], ["Лебедев А."])
            self.assertIn("Захарова", d[0]["reason"])
            self.assertEqual(G.get_decision("ФУЛ УЛ-26-0090", out)["decisions"][0]["outcome"], "Возможный пропуск")
            self.assertEqual(G.get_decision("S9999999", out)["decisions"], [])

    def test_followup_sets_reference_existing_decisions(self):
        for ds in ("ds1_base", "ds2_format", "ds3_edge"):
            questions = json.loads(Path(DATASETS, ds, "followups.json").read_text(encoding="utf-8"))["questions"]
            with tempfile.TemporaryDirectory() as out:
                distribute.main([os.path.join(DATASETS, ds, "input"), "--out", out])
                for q in questions:
                    found = G.get_decision(q["question"], out)["decisions"]
                    self.assertTrue(found, (ds, q["question"]))
                    # эталонные факты вопроса действительно есть в журнале решений
                    journal = " ".join(f"{x['logist']} {x['group']} {x['outcome']} {x['reason']}" for x in found)
                    self.assertEqual(G.check_followup(journal, q["must_contain"]), [], (ds, q["question"]))
                    # и запрещённых утверждений в самом журнале нет
                    self.assertEqual(
                        G.check_followup(journal, [], q.get("must_not_contain", ())), [], (ds, q["question"])
                    )


if __name__ == "__main__":
    unittest.main()
