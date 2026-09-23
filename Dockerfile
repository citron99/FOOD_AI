# FOOD_AI / SmartKitchen Family — контейнер отчёта о сроках годности.
# Проект использует только стандартную библиотеку Python, зависимостей нет.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Копируем только код агента и демо-инвентарь (секреты и мусор исключены .dockerignore).
COPY agent ./agent
COPY data ./data
COPY README.md ./

RUN mkdir -p /app/reports \
    && useradd --system --uid 10001 --home /app appuser \
    && chown -R appuser:appuser /app

USER appuser

# По умолчанию — отчёт по демо-инвентарю на текущую дату.
# Примеры:
#   docker run --rm food_ai --as-of 2026-08-18 --warning-days 3
#   docker run --rm -v "$PWD/reports:/app/reports" food_ai --html-out reports/expiry_report.html
#   docker run --rm -e IIKO_API_TOKEN=... food_ai --source iiko --warning-days 3
ENTRYPOINT ["python", "-m", "agent.cli"]
CMD ["--inventory", "data/inventory.json", "--warning-days", "3", "--out", "reports/expiry_report.md"]
