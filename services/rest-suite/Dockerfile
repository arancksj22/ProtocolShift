FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all three service files — docker-compose sets the CMD to pick the right one
COPY postgres_service.py .
COPY mongo_service.py .
COPY redis_service.py .

EXPOSE 8001 8002 8003
