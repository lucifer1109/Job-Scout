FROM python:3.11-slim

WORKDIR /app

ARG CACHE_BUST=2
COPY requirements-render.txt .
RUN pip install --no-cache-dir -r requirements-render.txt

COPY scout.py .

CMD ["python", "scout.py"]
