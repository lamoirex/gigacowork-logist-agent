"""CLI навыка: вызов main() в том же процессе — коды выхода, файлы результата, понятные ошибки."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import CLOUD_H, DATASETS, STAFF, write_wb  # задаёт sys.path

import distribute


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = distribute.main(argv)
    return code, out.getvalue()


class TestCli(unittest.TestCase):
    def test_folder_input_produces_all_outputs(self):
        with tempfile.TemporaryDirectory() as out:
            code, stdout = run_main([os.path.join(DATASETS, "ds1_base", "input"), "--out", out])
            self.assertEqual(code, 0, stdout)
            for name in ("distribution_result.xlsx", "summary.json", "chat_message.txt"):
                self.assertTrue(Path(out, name).exists(), name)
            summary = json.loads(Path(out, "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["stats"]["new_rows"], 20)
            self.assertEqual(len(summary["assignments"]), summary["stats"]["ful_total"])
            self.assertIn("=== ОТВЕТ ДЛЯ ЧАТА ===", stdout)

    def test_named_flags_and_any_file_order(self):
        src = os.path.join(DATASETS, "ds3_edge", "input")
        with tempfile.TemporaryDirectory() as out:
            code, _ = run_main(
                [
                    "--staff",
                    os.path.join(src, "ful.xlsx"),  # роль определяется по заголовкам, а не по флагу
                    "--ful",
                    os.path.join(src, "staff.xlsx"),
                    "--cloud",
                    os.path.join(src, "cloud.xlsx"),
                    "--out",
                    out,
                ]
            )
            self.assertEqual(code, 0)

    def test_no_inputs(self):
        code, stdout = run_main([])
        self.assertEqual(code, 2)
        self.assertTrue(stdout.startswith("ОШИБКА"))

    def test_missing_file_role_is_explained(self):
        with tempfile.TemporaryDirectory() as d:
            write_wb(os.path.join(d, "cloud.xlsx"), {"x": [CLOUD_H]})
            write_wb(os.path.join(d, "staff.xlsx"), {"x": STAFF})
            code, stdout = run_main([d, "--out", os.path.join(d, "out")])
            self.assertEqual(code, 2)
            self.assertIn("Выгрузка ФУЛ", stdout)

    def test_previous_result_in_folder_is_skipped(self):
        src = os.path.join(DATASETS, "ds1_base", "input")
        with tempfile.TemporaryDirectory() as d:
            for name in os.listdir(src):
                Path(d, name).write_bytes(Path(src, name).read_bytes())
            self.assertEqual(run_main([d, "--out", d])[0], 0)  # результат кладётся рядом со входом
            code, stdout = run_main([d, "--out", d])  # и повторный запуск его не подхватывает
            self.assertEqual(code, 0, stdout)
            self.assertNotIn("Предупреждение", stdout)


if __name__ == "__main__":
    unittest.main()
