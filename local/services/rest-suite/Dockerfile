FROM python:3.12-slim

WORKDIR /app

COPY rest-suite/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rest-suite/postgres_service.py .
COPY rest-suite/mongo_service.py .
COPY rest-suite/redis_service.py .

EXPOSE 8001 8002 8003
