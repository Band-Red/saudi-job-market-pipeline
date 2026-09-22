FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Bronze output goes to data/bronze/bayt/ — mount it as a volume to keep it:
#   docker run -v "$PWD/data:/app/data" bayt-scraper
CMD ["python", "-m", "src.scrapers.bayt_scraper"]
