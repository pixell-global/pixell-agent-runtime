FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml .

RUN pip install -e .

ENV PYTHONUNBUFFERED=1

EXPOSE 8080 9090 50051

CMD ["python", "-m", "pixell_runtime"]