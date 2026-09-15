FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOLOQA_STATE=/data/.holoqa/state.db

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && .venv/bin/python -m playwright install --with-deps chromium

RUN mkdir -p /data/.holoqa
EXPOSE 8000 8765
CMD [".venv/bin/holoqa-dashboard"]
