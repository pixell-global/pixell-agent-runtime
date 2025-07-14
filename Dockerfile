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
ENV PAR_HOST=0.0.0.0
ENV PAR_PORT=8000

EXPOSE 8000 9090

CMD ["uvicorn", "pixell_runtime.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]