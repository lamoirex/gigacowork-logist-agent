#!/usr/bin/env python3
"""Сборка архивов для сдачи.

    python3 tools/build.py

Создаёт в dist/:
  logist-distribution.zip — навык (папка logist-distribution/ с SKILL.md, scripts/, references/);
  agent.zip               — навык.zip + system_prompt.txt + INSTALL.md (то, что загружается в GigaCowork);
  testdata.zip            — генератор, три набора данных и эталоны.
Архивы детерминированы (фиксированные даты и порядок файлов).
"""

import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
SKILL = os.path.join(ROOT, "agent", "skill", "logist-distribution")
FIXED = (2026, 11, 12, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def add(zf, path, arcname):
    info = zipfile.ZipInfo(arcname, FIXED)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(path, "rb") as fh:
        zf.writestr(info, fh.read())


def add_tree(zf, root, prefix):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".pyc") or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            add(zf, full, os.path.join(prefix, os.path.relpath(full, root)).replace(os.sep, "/"))


def main():
    os.makedirs(DIST, exist_ok=True)
    # правила для модели лежат и в навыке
    shutil.copyfile(os.path.join(ROOT, "docs", "rules.md"), os.path.join(SKILL, "references", "rules.md"))

    skill_zip = os.path.join(DIST, "logist-distribution.zip")
    with zipfile.ZipFile(skill_zip, "w") as zf:
        add_tree(zf, SKILL, "logist-distribution")

    with zipfile.ZipFile(os.path.join(DIST, "agent.zip"), "w") as zf:
        add(zf, skill_zip, "logist-distribution.zip")
        add(zf, os.path.join(ROOT, "agent", "system_prompt.txt"), "system_prompt.txt")
        add(zf, os.path.join(ROOT, "agent", "INSTALL.md"), "INSTALL.md")

    with zipfile.ZipFile(os.path.join(DIST, "testdata.zip"), "w") as zf:
        add(zf, os.path.join(ROOT, "testdata", "README.md"), "testdata/README.md")
        add(zf, os.path.join(ROOT, "testdata", "generate.py"), "testdata/generate.py")
        add_tree(zf, os.path.join(ROOT, "testdata", "datasets"), "testdata/datasets")
        add(zf, os.path.join(ROOT, "tools", "evaluate.py"), "tools/evaluate.py")

    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if name.endswith(".zip"):
            with zipfile.ZipFile(p) as zf:
                print(f"{name}: {os.path.getsize(p)} байт, {len(zf.namelist())} файлов")


if __name__ == "__main__":
    main()
