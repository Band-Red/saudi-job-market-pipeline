import os
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import pandas as pd

# --- Setup: find project root and load secrets -----------------------
# careerjet.py is at: <root>/src/ingestion/careerjet.py
# parents[0] = ingestion, parents[1] = src, parents[2] = <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

affid = os.environ.get("CAREERJET_AFFID")
if not affid:
    raise ValueError(f"CAREERJET_AFFID not found. Checked: {PROJECT_ROOT / '.env'}")

# The roles we care about for this project.
ROLES = [
    "software engineer",
    "data engineer",
    "frontend developer",
    "backend developer",
    "ai engineer",
]


def get_public_ip() -> str:
    """Ask a free service what our own public IP address is."""
    try:
        response = requests.get("https://api.ipify.org?format=json", timeout=5)
        return response.json()["ip"]
    except requests.RequestException:
        return "0.0.0.0"


def search_careerjet(keywords: str, location: str = "Saudi Arabia") -> dict:
    """Run one search against Careerjet and return the raw JSON response."""
    url = "http://public.api.careerjet.net/search"
    params = {
        "keywords": keywords,
        "location": location,
        "affid": affid,
        "locale_code": "en_SA",
        "user_agent": "job-market-pipeline-student-project",
        "user_ip": get_public_ip(),
    }
    headers = {
        "Referer": "https://github.com/RIANS/Jonb-Markt-Saudi",  # your repo URL
    }
    response = requests.get(url, params=params, headers=headers, timeout=15)
    return response.json()


def save_bronze(source: str, role: str, payload: dict) -> Path:
    """Save a raw API response to data/bronze/<source>/<date>/<role>.json."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = PROJECT_ROOT / "data" / "bronze" / source / date_str
    out_dir.mkdir(parents=True, exist_ok=True)  # creates all missing folders

    safe_role = role.replace(" ", "_")  # "data engineer" -> "data_engineer"
    out_path = out_dir / f"{safe_role}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return out_path


CSV_COLUMNS = [
    "title", "company", "locations", "date", "salary", "description",
    "site", "url", "target_role", "search_query", "collected_at",
]


def resolve_date(date_str: str | None = None) -> str:
    """Return date_str, or today's date in UTC."""
    return date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def bronze_dir(date_str: str | None = None) -> Path:
    """Return data/bronze/careerjet/<date> (today in UTC by default)."""
    date_str = resolve_date(date_str)
    return PROJECT_ROOT / "data" / "bronze" / "careerjet" / date_str


def combine_bronze(date_str: str | None = None) -> pd.DataFrame:
    """Combine all role JSON files for one date into a single DataFrame."""
    rows = []
    for path in sorted(bronze_dir(date_str).glob("*.json")):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("type") != "JOBS":
            continue

        role = path.stem.replace("_", " ")  # "data_engineer" -> "data engineer"
        collected_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        for job in payload.get("jobs", []):
            job["target_role"] = role
            job["search_query"] = role
            job["collected_at"] = collected_at
            rows.append(job)

    df = pd.DataFrame(rows).reindex(columns=CSV_COLUMNS)
    # Careerjet highlights keywords with <b> tags; strip them
    df["description"] = df["description"].str.replace(r"</?b>", "", regex=True)
    return df


def save_csv(date_str: str | None = None) -> Path:
    """Combine the date's JSON files into data/bronze/careerjet/careerjet_jobs_<date>.csv, then delete the JSON files."""
    in_dir = bronze_dir(date_str)
    out_path = in_dir.parent / f"careerjet_jobs_{resolve_date(date_str)}.csv"
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
    combined = combined.drop_duplicates(subset=["url"], keep="first")
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
    print(df["target_role"].value_counts())
    return out_path


# --- Main run: search each role, save each result ---------------------
if __name__ == "__main__":
    for role in ROLES:
        print(f"Searching: {role} ...")
        data = search_careerjet(role)

        if data.get("type") == "JOBS":
            print(f"  {data['hits']} total hits, {len(data['jobs'])} on this page")
        else:
            print(f"  No results or error: {data}")

        saved_path = save_bronze("careerjet", role, data)
        print(f"  Saved to: {saved_path}\n")

    save_csv()