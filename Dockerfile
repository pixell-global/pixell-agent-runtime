FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install uv for faster package installation (10-100x faster than pip)
RUN pip install --no-cache-dir uv

COPY src/ ./src/
COPY pyproject.toml setup.py MANIFEST.in ./

RUN pip install -e .

# Create wheelhouse with common agent dependencies for faster cold starts
RUN mkdir -p /wheelhouse && \
    pip download -d /wheelhouse \
        anthropic \
        openai \
        langchain \
        langchain-anthropic \
        langchain-openai \
        langchain-community \
        pydantic \
        httpx \
        aiofiles \
        python-dotenv \
        structlog \
        tiktoken \
        chromadb \
        faiss-cpu \
        redis \
        sqlalchemy \
        asyncpg \
        psycopg2-binary \
        pymongo \
        requests \
        beautifulsoup4 \
        lxml \
        pillow \
        numpy \
        pandas \
        matplotlib \
        plotly \
        scikit-learn \
        tenacity \
        jinja2 \
        markdown \
        pyyaml \
        toml \
        click || true

ENV PYTHONUNBUFFERED=1
ENV WHEELHOUSE_DIR=/wheelhouse

EXPOSE 8080 50051 3000

CMD ["python", "-m", "pixell_runtime"]