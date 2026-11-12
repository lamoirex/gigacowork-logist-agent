PY ?= python3
IMAGE ?= logist-agent
IN ?= testdata/datasets/ds1_base/input
OUT ?= result

.PHONY: help install test test-fast cov lint format check data runs bench metrics build pdf \
        docker-build docker-run docker-test all clean

help:            ## список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-13s %s\n", $$1, $$2}'

install:         ## зависимости для разработки + pre-commit
	$(PY) -m pip install -r requirements-dev.txt
	-pre-commit install

test:            ## все тесты (pytest)
	$(PY) -m pytest

test-fast:       ## быстрые тесты без сквозных (e2e)
	$(PY) -m pytest -m "not e2e"

cov:             ## тесты с покрытием ядра (порог 85%)
	$(PY) -m pytest --cov --cov-report=term --cov-fail-under=85

lint:            ## ruff: линтер и проверка форматирования
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:          ## ruff: автоисправления и форматирование
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

check: lint cov  ## то же, что проверяет CI (кроме Docker)
	$(PY) tools/run_control.py --strict report

data:            ## пересоздать синтетические данные и эталон
	$(PY) testdata/generate.py

runs:            ## контрольные прогоны скрипта (3 × каждый набор), падает при регрессии
	$(PY) tools/run_control.py --strict script --runs 3

bench:           ## нагрузочный тест: время и память на пачках 65 / 500 / 5 000 ФУЛ
	$(PY) tools/benchmark_scale.py

metrics:         ## пересчитать runs/REPORT.md по всем прогонам
	$(PY) tools/run_control.py report

build:           ## dist/agent.zip, dist/testdata.zip
	$(PY) tools/build.py

pdf:             ## dist/report.pdf (нужны Chrome/Chromium и graphviz)
	$(PY) tools/build_report.py

docker-build:    ## образы runtime и test
	docker build --target runtime -t $(IMAGE) .
	docker build --target test -t $(IMAGE):test .

docker-run:      ## запустить навык в контейнере: make docker-run IN=папка OUT=папка
	mkdir -p $(OUT)
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(abspath $(IN)):/data/in:ro" -v "$(abspath $(OUT)):/data/out" $(IMAGE)

docker-test:     ## ruff + pytest внутри контейнера
	docker run --rm $(IMAGE):test

all: check build pdf  ## всё для сдачи

clean:           ## удалить dist/, result/ и кэши
	rm -rf dist result htmlcov .coverage .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
