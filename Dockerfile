FROM python:3.11-slim

WORKDIR /app

ARG CACHE_BUST=2
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scout.py .

CMD ["python", "scout.py"]
