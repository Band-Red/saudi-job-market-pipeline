import hashlib
from pathlib import Path

import pandas as pd

from src.utils.helpers import (
    PROJECT_ROOT,
    clean_title,
    company_key,
    is_relevant,
    is_saudi,
    load_config,
    normalize_city,
    normalize_company,
    parse_salary,
    split_location,
    strip_html,
)

SILVER_COLUMNS = [
    "job_id", "source", "target_role", "title", "company", "city", "region", "country",
    "location_raw", "is_remote", "employment_type", "posted_at",
    "salary_min", "salary_max", "salary_period", "salary_currency", "description",
    "apply_url", "publisher", "collected_at",
]

TEXT_COLUMNS = [
    "target_role", "title", "company", "city", "region", "country", "location_raw",
    "employment_type", "salary_period", "salary_currency", "description", "apply_url", "publisher",
]


# --- 1. Load bronze ---------------------------------------------------
def load_bronze(source: str, cfg: dict) -> pd.DataFrame:
    """Read every <bronze>/<source>/<source>_jobs_*.csv into one DataFrame."""
    files = sorted((PROJECT_ROOT / cfg["paths"]["bronze"] / source).glob(f"{source}_jobs_*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(f, encoding="utf-8-sig") for f in files], ignore_index=True)


# --- 2. Map each source to the shared schema --------------------------
def map_careerjet(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["job_id"] = df["url"].map(lambda u: hashlib.sha1(str(u).encode()).hexdigest())
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
    out["collected_at"] = df["collected_at"]
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
    out["collected_at"] = df["collected_at"]
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
    report["cities renamed"] = int((old_city.notna() & (old_city != df["city"])).sum())

    df["posted_at"] = pd.to_datetime(df["posted_at"], utc=True, errors="coerce")
    df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True, errors="coerce", format="ISO8601")
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
    seen = df.groupby(["source", "job_id"])["collected_at"].agg(first_seen_at="min", last_seen_at="max")
    latest = (
        df.sort_values("collected_at", ascending=False)
        .drop_duplicates(subset=["source", "job_id"], keep="first")
    )
    return latest.merge(seen, left_on=["source", "job_id"], right_index=True)


def dedupe_cross_source(df: pd.DataFrame) -> pd.DataFrame:
    """Same job (title + company + city) listed twice: keep the JSearch row."""
    def norm(s: pd.Series) -> pd.Series:
        return s.fillna("").str.lower().str.replace(r"[^\w]+", "", regex=True)

    df = df.copy()
    df["_company"] = df["company"].map(company_key)
    df["_key"] = norm(df["title"]) + "|" + df["_company"] + "|" + norm(df["city"])
    df["_rank"] = (df["source"] != "jsearch").astype(int)  # jsearch first

    has_company = df["_company"] != ""
    matched = (
        df[has_company]
        .sort_values(["_rank", "first_seen_at"])
        .drop_duplicates(subset="_key", keep="first")
    )
    result = pd.concat([matched, df[~has_company]], ignore_index=True)
    return result.drop(columns=["_company", "_key", "_rank"])


# --- 5. Build and write -----------------------------------------------
def build_silver(cfg: dict) -> pd.DataFrame:
    mappers = {"careerjet": map_careerjet, "jsearch": map_jsearch}
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
