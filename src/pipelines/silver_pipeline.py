import hashlib
import sys
from pathlib import Path

# Allow running this file directly (VS Code Run button), not only with python -m
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.utils.helpers import (  # noqa: E402
    PROJECT_ROOT,
    clean_title,
    company_key,
    is_relevant,
    is_saudi,
    load_config,
    normalize_city,
    normalize_company,
    normalize_employment_type,
    parse_salary,
    split_bayt_location,
    split_location,
    strip_html,
)

SILVER_COLUMNS = [
    "job_id", "source", "target_role", "title", "company", "city", "region", "country",
    "location_raw", "is_remote", "employment_type", "posted_at",
    "salary_min", "salary_max", "salary_period", "salary_currency", "description",
    "apply_url", "publisher", "status", "skills", "collected_at", "source_first_seen_at",
]

# Cross-source duplicates: keep the row from the first source in this list
SOURCE_PRIORITY = ["jsearch", "bayt", "careerjet"]

TEXT_COLUMNS = [
    "target_role", "title", "company", "city", "region", "country", "location_raw",
    "employment_type", "salary_period", "salary_currency", "description", "apply_url", "publisher",
    "status", "skills",
]


# --- 1. Load bronze ---------------------------------------------------
def load_bronze(source: str, cfg: dict) -> pd.DataFrame:
    """Read every <bronze>/<source>/<source>_jobs_*.csv (Bayt: one .jsonl file) into one DataFrame."""
    if source == "bayt":
        path = PROJECT_ROOT / cfg["paths"]["bayt_bronze"]
        return pd.read_json(path, lines=True, dtype={"job_id": str}) if path.exists() else pd.DataFrame()
    files = sorted((PROJECT_ROOT / cfg["paths"]["bronze"] / source).glob(f"{source}_jobs_*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(f, encoding="utf-8-sig") for f in files], ignore_index=True)


# --- 2. Map each source to the shared schema --------------------------
def careerjet_job_id(df: pd.DataFrame) -> pd.Series:
    """Careerjet urls are tracking links that change every day, so build the id from the job itself."""
    def norm(s: pd.Series) -> pd.Series:
        return s.fillna("").astype(str).str.lower().str.replace(r"[^\w]+", "", regex=True)

    key = norm(df["title"]) + "|" + norm(df["company"]) + "|" + norm(df["locations"])
    return key.map(lambda k: hashlib.sha1(k.encode()).hexdigest())


def map_careerjet(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["job_id"] = careerjet_job_id(df)
    out["source"] = "careerjet"
    out["target_role"] = df["target_role"]
    out["title"] = df["title"]
    out["company"] = df["company"]
    locations = df["locations"].map(split_location)
    out["city"] = locations.str[0]
    out["region"] = locations.str[1]
    out["country"] = None
    out["location_raw"] = df["locations"]
    out["is_remote"] = None
    out["employment_type"] = None
    out["posted_at"] = pd.to_datetime(df["date"], format="%a, %d %b %Y %H:%M:%S GMT", utc=True, errors="coerce")
    salaries = df["salary"].map(parse_salary)
    out["salary_min"] = salaries.str[0]
    out["salary_max"] = salaries.str[1]
    out["salary_period"] = salaries.str[2]
    out["salary_currency"] = salaries.str[3]
    out["description"] = df["description"]
    out["apply_url"] = df["url"]
    out["publisher"] = df["site"].fillna("careerjet").replace("", "careerjet")
    out["status"] = None
    out["skills"] = None
    out["collected_at"] = df["collected_at"]
    out["source_first_seen_at"] = None
    return out[SILVER_COLUMNS]


def map_jsearch(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["job_id"] = df["job_uid"]
    out["source"] = "jsearch"
    out["target_role"] = df["target_role"]
    out["title"] = df["job_title"]
    out["company"] = df["employer_name"]
    out["city"] = df["job_city"]
    out["region"] = df["job_state"]
    out["country"] = df["job_country"]
    out["location_raw"] = df["job_location"]
    out["is_remote"] = df["job_is_remote"]
    out["employment_type"] = df["job_employment_type"]
    out["posted_at"] = pd.to_datetime(df["job_posted_at_datetime_utc"], utc=True, errors="coerce")
    out["salary_min"] = df["job_min_salary"]
    out["salary_max"] = df["job_max_salary"]
    out["salary_period"] = df["job_salary_period"]
    out["salary_currency"] = None
    out["description"] = df["job_description"]
    out["apply_url"] = df["job_apply_link"]
    out["publisher"] = df["job_publisher"]
    out["status"] = None
    out["skills"] = None
    out["collected_at"] = df["collected_at"]
    out["source_first_seen_at"] = None
    return out[SILVER_COLUMNS]


def map_bayt(df: pd.DataFrame) -> pd.DataFrame:
    def col(name):
        return df[name] if name in df else pd.Series(None, index=df.index, dtype="object")

    out = pd.DataFrame()
    out["job_id"] = df["job_id"].astype(str)
    out["source"] = "bayt"
    out["target_role"] = df["category"]
    out["title"] = df["title"]
    hidden = col("is_hidden_company").fillna(False).astype(bool)
    out["company"] = df["company"].where(~hidden)
    locations = df["location"].map(split_bayt_location)
    out["city"] = locations.str[0]
    out["region"] = None
    out["country"] = locations.str[1]
    out["location_raw"] = df["location"]
    out["is_remote"] = col("id_remote_working").fillna("").astype(str).str.contains("remote", case=False)
    # "Full time · Mid career · 5 - 7 Years of Experience" -> "Full time"
    out["employment_type"] = col("id_type_level_experience").fillna("").astype(str).str.split("·").str[0].str.strip()
    out["posted_at"] = pd.to_datetime(col("date_posted_ts"), unit="s", utc=True, errors="coerce")
    salaries = col("salary").map(parse_salary)
    out["salary_min"] = salaries.str[0]
    out["salary_max"] = salaries.str[1]
    out["salary_period"] = salaries.str[2]
    out["salary_currency"] = salaries.str[3]
    out["description"] = col("summary").fillna(col("full_text"))
    out["apply_url"] = df["job_url"]  # Bayt apply_url is a relative login link
    out["publisher"] = "bayt"
    out["status"] = col("status")
    out["skills"] = col("skills")
    out["collected_at"] = df["last_seen_at"]
    out["source_first_seen_at"] = df["first_seen_at"]
    return out[SILVER_COLUMNS]


# --- 3. Clean (rules live in src/utils/helpers.py) --------------------
def clean(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    df = df.copy()
    report = {}

    for col in TEXT_COLUMNS:
        df[col] = df[col].astype("string").str.strip().replace("", pd.NA)

    before = len(df)
    df = df.dropna(subset=["title", "apply_url"])
    report["dropped (no title/url)"] = before - len(df)

    old_city = df["city"]
    df["title"] = df["title"].map(clean_title).astype("string")
    df["company"] = df["company"].map(normalize_company).astype("string")
    df["city"] = df["city"].map(normalize_city).astype("string")
    df["region"] = df["region"].map(normalize_city).astype("string")
    df["description"] = df["description"].map(strip_html).astype("string")
    df["target_role"] = df["target_role"].str.lower()
    df["salary_period"] = df["salary_period"].str.lower()
    df["country"] = df["country"].str.upper()
    df["employment_type"] = df["employment_type"].map(normalize_employment_type).astype("string")
    report["cities renamed"] = int((old_city.notna() & (old_city != df["city"])).sum())

    df["posted_at"] = pd.to_datetime(df["posted_at"], utc=True, errors="coerce")
    for col in ["collected_at", "source_first_seen_at"]:
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce", format="ISO8601")
    df["salary_min"] = pd.to_numeric(df["salary_min"], errors="coerce").astype("Float64")
    df["salary_max"] = pd.to_numeric(df["salary_max"], errors="coerce").astype("Float64")
    df["is_remote"] = df["is_remote"].map({True: True, False: False, "True": True, "False": False}).astype("boolean")

    # Flags: keep every row, let gold decide what to filter
    df["is_saudi"] = pd.array(
        [is_saudi(c, l, k) for c, l, k in zip(df["city"], df["location_raw"], df["country"])],
        dtype="boolean",
    )
    df["is_relevant"] = pd.array(
        [is_relevant(t, r, cfg) for t, r in zip(df["title"], df["target_role"])],
        dtype="boolean",
    )
    return df, report


# --- 4. Deduplicate ---------------------------------------------------
def dedupe_same_source(df: pd.DataFrame) -> pd.DataFrame:
    """Same job seen on several days: keep the latest row, track first/last seen."""
    df = df.copy()
    df["_first"] = df[["collected_at", "source_first_seen_at"]].min(axis=1)  # Bayt tracks its own first_seen_at
    seen = df.groupby(["source", "job_id"]).agg(first_seen_at=("_first", "min"), last_seen_at=("collected_at", "max"))
    latest = (
        df.sort_values("collected_at", ascending=False)
        .drop_duplicates(subset=["source", "job_id"], keep="first")
    )
    latest = latest.drop(columns=["_first", "source_first_seen_at"])
    return latest.merge(seen, left_on=["source", "job_id"], right_index=True)


def dedupe_cross_source(df: pd.DataFrame) -> pd.DataFrame:
    """Same job listed twice: keep the row from the highest-priority source.

    Match 1: same apply link (works even when the employer is hidden).
    Match 2: same title + company + city (only rows with a company).
    """
    def norm(s: pd.Series) -> pd.Series:
        return s.fillna("").str.lower().str.replace(r"[^\w]+", "", regex=True)

    df = df.copy()
    df["_rank"] = df["source"].map({s: i for i, s in enumerate(SOURCE_PRIORITY)}).fillna(len(SOURCE_PRIORITY))
    df = df.sort_values(["_rank", "first_seen_at"])

    df["_url"] = df["apply_url"].str.lower().str.rstrip("/")
    df = df.drop_duplicates(subset="_url", keep="first")

    df["_company"] = df["company"].map(company_key)
    df["_key"] = norm(df["title"]) + "|" + df["_company"] + "|" + norm(df["city"])
    has_company = df["_company"] != ""
    matched = df[has_company].drop_duplicates(subset="_key", keep="first")
    result = pd.concat([matched, df[~has_company]], ignore_index=True)
    return result.drop(columns=["_company", "_key", "_rank", "_url"])


# --- 5. Build and write -----------------------------------------------
def build_silver(cfg: dict) -> pd.DataFrame:
    mappers = {"careerjet": map_careerjet, "jsearch": map_jsearch, "bayt": map_bayt}
    frames = []
    for source in cfg["sources"]:
        raw = load_bronze(source, cfg)
        print(f"{source}: {len(raw)} bronze rows")
        if not raw.empty:
            frames.append(mappers[source](raw))

    if not frames:
        return pd.DataFrame(columns=SILVER_COLUMNS)

    df, report = clean(pd.concat(frames, ignore_index=True), cfg)
    before = len(df)
    df = dedupe_same_source(df)
    after_same = len(df)
    df = dedupe_cross_source(df)

    report["same-source duplicates"] = before - after_same
    report["cross-source duplicates"] = after_same - len(df)
    report["is_saudi = False"] = int((df["is_saudi"] == False).sum())  # noqa: E712
    report["is_saudi unknown"] = int(df["is_saudi"].isna().sum())
    report["is_relevant = False"] = int((df["is_relevant"] == False).sum())  # noqa: E712

    print("\nCleaning report:")
    for name, value in report.items():
        print(f"  {name}: {value}")

    df = df.drop(columns="collected_at")
    return df.sort_values(["source", "posted_at"], ascending=[True, False]).reset_index(drop=True)


def save_silver(df: pd.DataFrame, cfg: dict) -> Path:
    path = PROJECT_ROOT / cfg["paths"]["silver"]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


if __name__ == "__main__":
    config = load_config()
    silver = build_silver(config)
    out_path = save_silver(silver, config)
    print(f"\nSaved {len(silver)} jobs to {out_path}")
    print(silver["source"].value_counts())
    print(silver["target_role"].value_counts())
