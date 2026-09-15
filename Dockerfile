FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv && uv sync --frozen

EXPOSE 8000
CMD [".venv/bin/holoqa-dashboard"]
