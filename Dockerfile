FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Build from the repo root:
#   docker build -t saudi-jobs .
# Secrets come from .env at runtime, and data/ is mounted so output is kept:
#   docker run --rm --env-file .env -v "${PWD}/data:/app/data" saudi-jobs
# Pass run_pipeline flags after the image name, e.g. --no-upload or --sources bayt
ENTRYPOINT ["python", "-m", "src.pipelines.run_pipeline"]
