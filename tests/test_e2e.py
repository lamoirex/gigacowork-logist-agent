"""Сквозные проверки: все наборы данных против эталона, воспроизводимость, коды выхода."""

import os
import subprocess
import sys
import tempfile
import unittest

from helpers import DATASETS, SCRIPTS


class TestEndToEnd(unittest.TestCase):
    def test_datasets_match_reference_and_are_reproducible(self):
        from evaluate import evaluate

        for ds in sorted(os.listdir(DATASETS)):
            hashes = set()
            for _ in range(2):
                with tempfile.TemporaryDirectory() as out:
                    r = subprocess.run(
                        [
                            sys.executable,
                            os.path.join(SCRIPTS, "distribute.py"),
                            os.path.join(DATASETS, ds, "input"),
                            "--out",
                            out,
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    m = evaluate(
                        os.path.join(DATASETS, ds, "expected.json"), os.path.join(out, "distribution_result.xlsx")
                    )
                    self.assertEqual(m["full_match_rate"], 1.0, (ds, m["mismatches"]))
                    self.assertTrue(m["cloud_form_ok"], ds)
                    self.assertEqual(m["invented_values"], 0, ds)
                    hashes.add(m["content_hash"])
            self.assertEqual(len(hashes), 1, ds)

    def test_no_inputs_exit_code(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "distribute.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ОШИБКА", r.stdout)


if __name__ == "__main__":
    unittest.main()
