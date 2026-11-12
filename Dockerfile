# Образ навыка logist-distribution.
#   runtime — только скрипт навыка: распределяет ФУЛ из /data/in, кладёт результат в /data/out
#   test    — окружение разработки: pytest, ruff, контрольные прогоны
#
#   docker build -t logist-agent .
#   docker run --rm -v "$PWD/input:/data/in:ro" -v "$PWD/result:/data/out" logist-agent
#
# Базовый образ и индекс пакетов можно подменить на корпоративные зеркала:
#   docker build --build-arg PYTHON_IMAGE=registry.local/python:3.12-slim \
#                --build-arg PIP_INDEX_URL=https://nexus.local/repository/pypi/simple .

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS base
ARG PIP_INDEX_URL
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /app

# ---------------------------------------------------------------- runtime
FROM base AS runtime
COPY requirements.txt .
RUN pip install ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements.txt
COPY agent/skill/logist-distribution /app/skill
RUN useradd --create-home --uid 10001 agent \
 && mkdir -p /data/in /data/out \
 && chown -R agent /data
USER agent
VOLUME ["/data"]
LABEL org.opencontainers.image.title="logist-distribution" \
      org.opencontainers.image.description="Навык GigaCowork: распределение ФУЛ между логистами и строки реестра Cloud" \
      org.opencontainers.image.version="1.1.0"
ENTRYPOINT ["python", "/app/skill/scripts/distribute.py"]
CMD ["/data/in", "--out", "/data/out"]

# ---------------------------------------------------------------- test
FROM base AS test
COPY requirements.txt requirements-dev.txt ./
RUN pip install ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements-dev.txt
COPY . .
CMD ["sh", "-c", "ruff check . && ruff format --check . && python -m pytest -q"]
