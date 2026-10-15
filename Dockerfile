FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system bot && adduser --system --ingroup bot bot
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data && chown -R bot:bot /app
USER bot
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import sqlite3,os; sqlite3.connect(os.getenv('DATABASE_PATH','data/arbitrage.sqlite3')).execute('select 1')"
STOPSIGNAL SIGTERM
CMD ["python","-m","arbitrage_terminal.main"]
