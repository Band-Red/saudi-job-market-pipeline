# Saudi Job Market Pipeline

A multi-source Python ELT pipeline for collecting and transforming Saudi job-market data from:

- Bayt.com
- JSearch API
- Careerjet API

The pipeline produces three data layers:

1. **Bronze** — raw or source-level collected data
2. **Silver** — cleaned, normalized, unified, and deduplicated job data
3. **Gold** — analytical summary tables

## Project structure

```text
Dockerfile                     Image that runs the full pipeline
config/
  config.yaml                  Pipeline, source, path, and Azure settings

src/
  scrapers/
    careerjet.py               Careerjet scraper
    JSearch.py                 JSearch API scraper
    bayt_scraper.py            Bayt.com scraper

  pipelines/
    silver_pipeline.py         Cleaning and deduplication
    gold_pipeline.py           Analytical tables
    run_pipeline.py            Full pipeline orchestration

  utils/
    helpers.py                 Normalization and validation helpers
    azure_storage.py           Azure Blob Storage operations

tests/
  test_transformations.py      Transformation tests
  test_gold.py                 Gold pipeline tests
  test_quality_audit.py        Silver and Gold quality checks
  test_azure_storage.py        Azure upload and skip-logic tests
```

## Requirements

- Python 3.12
- Git
- Optional: Azure CLI for Azure uploads
- Optional: Docker

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Windows setup

Create and activate a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Environment variables

Copy the example file:

```powershell
Copy-Item .env.example .env
```

Set the following values in `.env`:

```dotenv
CAREERJET_AFFID=your_careerjet_affiliate_id
JSEARCH_API_KEY=your_jsearch_api_key
AZURE_STORAGE_ACCOUNT_NAME=your_storage_account_name
```

### Environment variable reference

| Variable | Required for | Description |
|---|---|---|
| `CAREERJET_AFFID` | Careerjet | Careerjet affiliate ID |
| `JSEARCH_API_KEY` | JSearch | OpenWebNinja JSearch API key |
| `AZURE_STORAGE_ACCOUNT_NAME` | Azure uploads | Azure Storage Account name |

Bayt does not require an API key.

## Run the full pipeline locally

To run all scrapers, build Silver and Gold, and keep the output locally without uploading to Azure:

```powershell
python -m src.pipelines.run_pipeline --no-upload
```

The pipeline runs in this order:

```text
Careerjet, JSearch, Bayt
        ↓
Bronze
        ↓
Silver cleaning and deduplication
        ↓
Gold analytical tables
```

## Run individual scrapers

### Careerjet

```powershell
python -m src.scrapers.careerjet
```

Requires:

```text
CAREERJET_AFFID
```

### JSearch

```powershell
python -m src.scrapers.JSearch
```

Requires:

```text
JSEARCH_API_KEY
```

### Bayt

```powershell
python -m src.scrapers.bayt_scraper
```

To remove the existing Bayt Bronze file and start from the beginning:

```powershell
python -m src.scrapers.bayt_scraper --fresh-start
```

## Run selected sources

```powershell
python -m src.pipelines.run_pipeline --sources careerjet --no-upload
python -m src.pipelines.run_pipeline --sources jsearch --no-upload
python -m src.pipelines.run_pipeline --sources bayt --no-upload
python -m src.pipelines.run_pipeline --sources careerjet jsearch --no-upload
```

## Rebuild Silver and Gold from existing Bronze data

Skip the scrapers and use existing local Bronze files:

```powershell
python -m src.pipelines.run_pipeline --skip-scrape --no-upload
```

Ignore the previous Silver file and rebuild Silver from local Bronze only:

```powershell
python -m src.pipelines.run_pipeline --skip-scrape --fresh-silver --no-upload
```

### How Silver accumulates

By default Silver is not rebuilt from scratch. Every run:

1. Loads the previous Silver table. It is downloaded from the Azure `silver`
   container first; if Azure is unreachable or the blob is missing, the local
   `data/silver/jobs.parquet` is used. If neither exists, Silver is built from
   Bronze only.
2. Appends the rows from all local Bronze files.
3. Cleans, normalizes, and deduplicates the combined set.

So jobs that have disappeared from the sources are kept in Silver instead of
being dropped. Use `--fresh-silver` to skip step 1 and rebuild from local Bronze
only.

## Output layers

### Bronze

Bronze data is stored locally under:

```text
data/bronze/
```

Formats by source:

| Source | Format |
|---|---|
| Careerjet | CSV |
| JSearch | CSV |
| Bayt | JSONL |

### Silver

The cleaned and unified detailed dataset is:

```text
data/silver/jobs.parquet
```

Format:

```text
Parquet
```

Silver includes:

- normalized job titles
- normalized companies and locations
- parsed salary fields
- source information
- Saudi-location flags
- relevance flags
- deduplicated records

### Gold

Gold tables are stored under:

```text
data/gold/
```

Available tables:

```text
jobs_by_role.parquet
jobs_by_city.parquet
jobs_by_role_city.parquet
top_companies.parquet
new_jobs_weekly.parquet
employment_type_share.parquet
bayt_job_status.parquet
source_coverage.parquet
```

Gold is built from Silver and keeps jobs that satisfy:

```text
is_relevant == True
is_saudi == True
```

## Azure Blob Storage

The Azure containers are configured in `config/config.yaml`:

```yaml
azure:
  containers:
    bronze: bronze
    silver: silver
    gold: gold
```

The project uploads files without changing their formats:

- Bronze Careerjet and JSearch files remain CSV.
- Bronze Bayt files remain JSONL.
- Silver remains Parquet.
- Gold remains Parquet.

Before uploading:

```powershell
az login
```

Then run:

```powershell
python -m src.pipelines.run_pipeline
```

Azure authentication uses `DefaultAzureCredential`.

### Upload behavior

- **Bronze is add-only.** A Bronze blob that already exists is never overwritten
  or replaced, so raw history is preserved.
- **Bayt's Bronze file gets a dated blob name.** The scraper keeps appending to
  one local `bayt_jobs.jsonl`, so each run uploads it as a new dated blob instead
  of being skipped forever by the add-only rule.
- **Silver and Gold are re-uploaded only when the content changed.** The local
  file's MD5 is compared with the blob's; an identical file is skipped.
- **A failed Azure login does not stop the run.** Access is checked once at the
  start; if it fails, the pipeline still builds Bronze, Silver, and Gold locally
  and reports the uploads as skipped.

## Read the output with pandas

Read a Gold table:

```python
import pandas as pd

jobs_by_role = pd.read_parquet("data/gold/jobs_by_role.parquet")
print(jobs_by_role.head())
```

Read the detailed Silver dataset:

```python
import pandas as pd

jobs = pd.read_parquet("data/silver/jobs.parquet")
print(jobs.head())
```

## Tests

Run all tests:

```powershell
python -m pytest -v
```

Run transformation tests:

```powershell
python -m pytest tests/test_transformations.py -v
```

Run Gold tests:

```powershell
python -m pytest tests/test_gold.py -v
```

Run Azure storage tests:

```powershell
python -m pytest tests/test_azure_storage.py -v
```

Run quality checks after generating Silver and Gold:

```powershell
python -m pytest tests/test_quality_audit.py -v
```

## Docker

The `Dockerfile` is at the repo root. Build the image:

```powershell
docker build -t saudi-job-market-pipeline .
```

The image entrypoint is the full pipeline:

```text
python -m src.pipelines.run_pipeline
```

Any `run_pipeline` flags are passed after the image name. Secrets come from `.env`,
and `data/` is mounted so the output is kept on the host.

Run the full pipeline without uploading to Azure:

```powershell
docker run --rm --env-file .env `
  -v "${PWD}\data:/app/data" `
  saudi-job-market-pipeline --no-upload
```

Run one source only:

```powershell
docker run --rm --env-file .env `
  -v "${PWD}\data:/app/data" `
  saudi-job-market-pipeline --sources bayt --no-upload
```

Uploading to Azure from the container requires Azure credentials in the container
environment. There is no `az login` inside the image, so run the upload from the
host instead:

```powershell
python -m src.pipelines.run_pipeline
```

## Data flow

```text
Careerjet API ─┐
JSearch API ───┼──> Bronze ──> Silver ──> Gold
Bayt.com ──────┘
```

- **Bronze:** source-level files
- **Silver:** cleaned, normalized, and deduplicated detailed jobs
- **Gold:** analytical summary tables