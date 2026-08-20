FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/data /app/logs \
    && chown -R botuser:botuser /app

USER botuser

CMD ["arbitrage-bot"]