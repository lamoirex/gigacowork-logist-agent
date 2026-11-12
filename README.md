# Агент распределения ФУЛ между логистами (GigaCowork)

Тестовое задание «Фабрики решений»: AI-агент для GigaCowork, который по трём выгрузкам Excel
(ФУЛ из 1С, реестр Cloud, сотрудники из 1С) определяет новые упаковочные листы, отделяет
повторные отправки, подбирает группу и логиста и готовит строки реестра Cloud строго по его форме.

> **Решения принимает детерминированный скрипт, модель только запускает его и пересказывает результат.**
> Поэтому результат воспроизводим, объясним и не содержит выдуманных значений.

| Метрика из БТ | Порог | Результат (3 набора × 3 прогона, скрипт и агент) |
|---|---|---|
| Точность (статус, группа, логист, комментарий) | ≥ 95% | **100%** |
| Форма Cloud: 12 колонок, без старых ФУЛ и дублей | 100% прогонов | **100%** |
| Выдуманные значения на ловушках | 0 | **0** |
| Воспроизводимость трёх запусков | одинаково | **одинаково** (совпадают хеши содержимого) |
| Обоснование у каждого назначения | да | **да** |
| Время ответа агента | ≤ 60 с | **6–8 с** (скрипт 0,2–0,3 с) |

Подробно — в [runs/REPORT.md](runs/REPORT.md) и [отчёте](docs/report.md).

## Быстрый старт

```bash
pip install -r requirements.txt     # openpyxl; xlrd — только для .xls
python3 agent/skill/logist-distribution/scripts/distribute.py \
        testdata/datasets/ds1_base/input --out ./result
python3 tools/evaluate.py --expected testdata/datasets/ds1_base/expected.json \
        --result result/distribution_result.xlsx
```

Скрипту можно передать файлы в любом порядке или папку с ними. Роль каждого файла
определяется по заголовкам, имена файлов и листов не важны.

## Как пользоваться в GigaCowork

1. Загрузить навык `logist-distribution.zip` и системный промпт `system_prompt.txt`
   (оба — в `dist/agent.zip`, см. [agent/INSTALL.md](agent/INSTALL.md)).
2. Выбрать агента, приложить три выгрузки и отправить **пустое** сообщение.
3. Получить `distribution_result.xlsx` и короткий ответ в чат:

```
Готово: distribution_result.xlsx
• Новых строк для Cloud: 20 (с логистом — 14, без логиста — 6)
• Повторных отправок: 5 (заказ уже в Cloud — 4, дубль в пачке — 1)
• Возможных пропусков: 1
• Вопросов, требующих решения: 13 (срочных — 7)
Срочно:
  – нет номера заказа — строка не создана: УЛ-26-0108
  – группа не определена (нет или незнакомый способ доставки / страна): УЛ-26-0111, УЛ-26-0112
  – в группе нет доступных логистов: УЛ-26-0105, УЛ-26-0118, УЛ-26-0121, УЛ-26-0125
Подробности — на листах «Требует внимания» и «Журнал решений».
```

Листы результата: «Сводка», «Новые строки Cloud» (12 колонок в порядке реестра), «Повторные
отправки», «Возможные пропуски», «Требует внимания», «Журнал решений» (правило и обоснование по
каждому ФУЛ), «Сотрудники».

## Структура репозитория

```
agent/
  system_prompt.txt                  системный промпт агента (без привязки к инструменту)
  INSTALL.md                         установка и запуск
  skill/logist-distribution/         навык в формате GigaCowork
    SKILL.md                         когда применять, как запустить, как ответить
    scripts/                         Python 3: distribute.py (CLI), xlreader, normalize, engine, report
    references/                      rules.json, countries.json, rules.md
docs/
  rules.md                           правила распределения и допущения
  questions.md                       16 открытых вопросов заказчику: кому, зачем, допущение
  algorithm.dot / .svg               схема алгоритма
  report.md                          отчёт (из него собирается PDF)
  motivation.md                      ответы на мотивационные вопросы
testdata/
  generate.py                        генератор синтетики; эталон задан вручную
  datasets/{ds1_base,ds2_format,ds3_edge}/   input/ (3 файла) + expected.json/.xlsx
tests/                               30 тестов: нормализация, чтение, правила, CLI, ответ в чат, сквозные
tools/
  evaluate.py                        метрики относительно эталона
  run_control.py                     контрольные прогоны и сводный отчёт
  gigachat_agent.py                  агентный harness на GigaChat API (function calling)
  import_harness_runs.py             импорт агентных прогонов из внешнего harness
  build.py / build_report.py         сборка архивов и PDF
runs/                                результаты всех контрольных прогонов + REPORT.md, metrics.json
Dockerfile                           образы runtime (навык) и test (ruff + pytest)
.github/workflows/                   ci.yml (проверки на каждый push), release.yml (релиз по тегу)
pyproject.toml · Makefile · requirements*.txt · .pre-commit-config.yaml · CHANGELOG.md
```

## Команды

Короткие варианты — через `make` (`make help`): `make check` повторяет проверки CI,
`make test`, `make lint`, `make runs`, `make build`, `make pdf`, `make docker-build`, `make docker-run`.

| Что | Команда |
|---|---|
| Тесты | `python3 -m pytest` (или без pytest: `python3 -m unittest discover -s tests`) |
| Быстрые тесты без сквозных | `python3 -m pytest -m "not e2e"` |
| Покрытие ядра (порог 85%) | `python3 -m pytest --cov --cov-fail-under=85` |
| Линтер и форматирование | `ruff check . && ruff format --check .` |
| Пересоздать синтетику | `python3 testdata/generate.py` |
| Контрольные прогоны скрипта | `python3 tools/run_control.py script --runs 3` |
| Прогоны на GigaChat | `GIGACHAT_CREDENTIALS=… python3 tools/gigachat_agent.py testdata/datasets/ds1_base/input --runs 3 --expected testdata/datasets/ds1_base/expected.json --ca-bundle russian_trusted_root_ca.cer` |
| Сводка метрик | `python3 tools/run_control.py report` |
| Архивы для сдачи | `python3 tools/build.py` → `dist/agent.zip`, `dist/testdata.zip` |
| PDF-отчёт | `python3 tools/build_report.py` → `dist/report.pdf` (нужен Chrome/Chromium, graphviz, `markdown`) |

## Docker

```bash
docker build -t logist-agent .                       # образ навыка (stage runtime)
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/testdata/datasets/ds1_base/input:/data/in:ro" \
  -v "$PWD/result:/data/out" logist-agent              # результат — в ./result
docker build --target test -t logist-agent:test . && docker run --rm logist-agent:test   # ruff + pytest
```

Навык в контейнере запускается не от root. Для корпоративной сети базовый образ и индекс пакетов
подменяются без правки Dockerfile:
`--build-arg PYTHON_IMAGE=<зеркало>/python:3.12-slim --build-arg PIP_INDEX_URL=<nexus>/simple`.

## CI/CD

| Workflow | Когда | Что делает |
|---|---|---|
| `ci.yml` | push в `main`, pull request | ruff → pytest на Python 3.9 / 3.11 / 3.12 / 3.13 с покрытием ≥ 85% → контрольные прогоны с порогами из БТ (`run_control.py --strict`, метрики — в сводке запуска) → сборка образа, запуск на наборе «чужой формат» со сверкой с эталоном, проверка ошибок пустой папки и прав → `agent.zip` и `testdata.zip` в артефактах |
| `release.yml` | тег `vX.Y.Z` | проверка, что тег = версия в `pyproject.toml` = версия навыка → тесты и контрольные прогоны → GitHub Release с `agent.zip`, `testdata.zip`, `report.pdf` и заметками из CHANGELOG → Docker-образ в `ghcr.io` |

Выпуск версии: `git tag v1.1.1 && git push origin v1.1.1`.
Зависимости обновляет Dependabot, перед коммитом ruff запускает pre-commit (`make install`).

## Ключевые решения

- **Устойчивое чтение входных данных.** Колонки ищутся по синонимам заголовков, строка заголовков —
  в первых 30 строках, лист и роль файла — по содержимому. Если колонки нет, выводится понятная
  ошибка со списком найденных колонок.
- **«Не определено» вместо догадок.** Нет номера заказа → строка не создаётся. Нет способа доставки
  или страны (для не-air) → группа и логист «не определено». Для air без страны логист назначается.
- **Справедливая загрузка.** Учитываются заказы «в работе» в Cloud и назначенные в текущей пачке,
  поэтому вся пачка новых поставщиков не уходит одному человеку.
- **Один поставщик — один логист** внутри группы. Поставщик с заказами разных групп попадает
  в «Требует внимания».
- **Сопоставление имён** «Иванова Е.», «Е. Иванова», «Иванова Е.П.» с ФИО. Однофамильцы не угадываются.
- **История изменений** — в [CHANGELOG.md](CHANGELOG.md).
- **Ответ в чат готовит скрипт.** Модель копирует его дословно, совпадение с шаблоном проверяется
  метрикой. Первый раунд агентных прогонов выявил искажения при пересказе длинного ответа,
  после исправления все 9 ответов совпали дословно.
