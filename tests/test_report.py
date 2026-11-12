"""Ответ в чат по шаблону и файлы результата."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import DATASETS, SCRIPTS


class TestChatAnswer(unittest.TestCase):
    def run_ds(self, ds, out):
        r = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "distribute.py"), os.path.join(DATASETS, ds, "input"), "--out", out],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_chat_message_file_matches_stdout_block(self):
        with tempfile.TemporaryDirectory() as out:
            stdout = self.run_ds("ds1_base", out)
            msg = Path(out, "chat_message.txt").read_text(encoding="utf-8").strip()
            block = stdout.split("=== ОТВЕТ ДЛЯ ЧАТА ===")[1].split("=== КОНЕЦ ОТВЕТА ===")[0].strip()
            self.assertEqual(msg, block)
            for f in ("distribution_result.xlsx", "summary.json"):
                self.assertTrue(os.path.exists(os.path.join(out, f)), f)

    def test_chat_message_is_short_and_grouped(self):
        with tempfile.TemporaryDirectory() as out:
            self.run_ds("ds1_base", out)
            msg = Path(out, "chat_message.txt").read_text(encoding="utf-8")
        lines = msg.strip().splitlines()
        self.assertLessEqual(len(lines), 12)
        self.assertIn("• Новых строк для Cloud: 20 (с логистом — 14, без логиста — 6)", lines)
        self.assertIn("Срочно:", lines)
        self.assertTrue(any(line.strip().startswith("– нет номера заказа") and "УЛ-26-0108" in line for line in lines))
        self.assertNotIn("===", msg)


if __name__ == "__main__":
    unittest.main()
