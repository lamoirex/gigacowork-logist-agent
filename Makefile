PY ?= python3

.PHONY: help install test data runs metrics build pdf all clean

help:            ## список команд
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

install:         ## зависимости
	$(PY) -m pip install -r requirements-dev.txt

test:            ## юнит- и сквозные тесты
	$(PY) -m unittest discover -s tests -v

data:            ## пересоздать синтетические данные и эталон
	$(PY) testdata/generate.py

runs:            ## контрольные прогоны скрипта (3 × каждый набор) + сводка метрик
	$(PY) tools/run_control.py script --runs 3

metrics:         ## пересчитать runs/REPORT.md по всем прогонам
	$(PY) tools/run_control.py report

build:           ## dist/agent.zip, dist/testdata.zip
	$(PY) tools/build.py

pdf:             ## dist/report.pdf (нужны Chrome/Chromium и graphviz)
	$(PY) tools/build_report.py

all: test build pdf  ## всё для сдачи

clean:           ## удалить dist/ и временные файлы
	rm -rf dist result __pycache__ */__pycache__ */*/__pycache__ */*/*/*/__pycache__
