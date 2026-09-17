import os
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import pandas as pd

# --- Setup: find project root and load secrets -----------------------
# JSearch.py is at: <root>/src/scrapers/JSearch.py
# parents[0] = scrapers, parents[1] = src, parents[2] = <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

api_key = os.environ.get("JSEARCH_API_KEY")
if not api_key:
    raise ValueError(f"JSEARCH_API_KEY not found. Checked: {PROJECT_ROOT / '.env'}")

URL = "https://api.openwebninja.com/jsearch/search-v2"

# The roles we care about for this project.
ROLES = [
    "software engineer",
    "data engineer",
    "frontend developer",
    "backend developer",
    "ai engineer",
]

MAX_PAGES_PER_QUERY = 10


def search_jsearch(query: str, cursor: str | None = None) -> dict | None:
    """Fetch one page from JSearch. Returns the raw JSON, or None on failure."""
    params = {"query": query, "country": "sa", "language": "en"}
    if cursor:
        params["cursor"] = cursor

    try:
        response = requests.get(URL, headers={"x-api-key": api_key}, params=params, timeout=30)
    except requests.RequestException as error:
        print(f"  Request error: {error}")
        return None

    if response.status_code != 200:
        print(f"  Request failed ({response.status_code}): {response.text[:300]}")
        return None

    return response.json()


def resolve_date(date_str: str | None = None) -> str:
    """Return date_str, or today's date in UTC."""
    return date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def bronze_dir(date_str: str | None = None) -> Path:
    """Return data/bronze/jsearch/<date> (today in UTC by default)."""
    return PROJECT_ROOT / "data" / "bronze" / "jsearch" / resolve_date(date_str)


def save_bronze(role: str, page: int, payload: dict) -> Path:
    """Save a raw API response to data/bronze/jsearch/<date>/<role>_p<page>.json."""
    out_dir = bronze_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_role = role.replace(" ", "_")  # "data engineer" -> "data_engineer"
    out_path = out_dir / f"{safe_role}_p{page}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return out_path


def combine_bronze(date_str: str | None = None) -> pd.DataFrame:
    """Combine all role/page JSON files for one date into a single DataFrame."""
    rows = []
    for path in sorted(bronze_dir(date_str).glob("*.json")):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)

        role = path.stem.rsplit("_p", 1)[0].replace("_", " ")  # "data_engineer_p2" -> "data engineer"
        collected_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        for job in payload.get("data", {}).get("jobs", []):
            job["target_role"] = role
            job["search_query"] = f"{role} in Saudi Arabia"
            job["collected_at"] = collected_at
            rows.append(job)

    return pd.DataFrame(rows)


def save_csv(date_str: str | None = None) -> Path:
    """Combine the date's JSON files into data/bronze/jsearch/jsearch_jobs_<date>.csv, then delete the JSON files."""
    in_dir = bronze_dir(date_str)
    out_path = in_dir.parent / f"jsearch_jobs_{resolve_date(date_str)}.csv"
    json_files = list(in_dir.glob("*.json"))

    if not json_files:
        print(f"No JSON files in {in_dir}")
        return out_path

    df = combine_bronze(date_str)

    # Merge with a CSV from an earlier run on the same date
    if out_path.exists():
        old_df = pd.read_csv(out_path)
        combined = pd.concat([old_df, df], ignore_index=True)
    else:
        combined = df

    before = len(combined)
    if "job_uid" in combined.columns:
        combined = combined.drop_duplicates(subset=["job_uid"], keep="first")
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")

    # CSV written successfully -> raw JSON no longer needed
    for path in json_files:
        path.unlink()
    if not any(in_dir.iterdir()):
        in_dir.rmdir()  # date folder is empty now

    print(f"Jobs in this batch: {len(df)}")
    print(f"Duplicates removed: {before - len(combined)}")
    print(f"Total unique jobs saved: {len(combined)}")
    print(f"File: {out_path}")
    print(f"Deleted {len(json_files)} JSON files")
    if not df.empty:
        print(df["target_role"].value_counts())
    return out_path


# --- Main run: search each role page by page, save each page ----------
if __name__ == "__main__":
    for role in ROLES:
        query = f"{role} in Saudi Arabia"
        print(f"Searching: {query} ...")

        cursor = None
        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            data = search_jsearch(query, cursor)
            if data is None:
                break

            result = data.get("data", {})
            jobs = result.get("jobs", [])
            if not jobs:
                print(f"  Page {page}: no more jobs")
                break

            saved_path = save_bronze(role, page, data)
            print(f"  Page {page}: {len(jobs)} jobs -> {saved_path}")

            cursor = result.get("cursor")
            if not cursor:
                break

    save_csv()
