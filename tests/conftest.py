"""Настройки pytest.

Тесты написаны на unittest, поэтому запускаются и без pytest (`python -m unittest discover -s tests`).
Здесь только разметка: сквозные тесты, которые запускают скрипт навыка на наборах данных,
получают маркер `e2e` — быстрые проверки: `pytest -m "not e2e"`.
"""

import pytest

import helpers  # noqa: F401  — добавляет в sys.path папки навыка и tools до сбора тестов

E2E_MODULES = {"test_e2e.py", "test_report.py"}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name in E2E_MODULES:
            item.add_marker(pytest.mark.e2e)
