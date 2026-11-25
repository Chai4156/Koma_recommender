FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/catalog_v1.sqlite \
    CACHE_DB_PATH=/data/state_v1.sqlite \
    TAXONOMY_PATH=/data/taxonomy_v1.json \
    CORS_ORIGINS="*" \
    GEMINI_DAILY_CAP=500 \
    RATE_LIMIT_PER_MIN=20

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
