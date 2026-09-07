import os
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests

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