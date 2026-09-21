"""Quality checks on the real pipeline output (data/silver, data/gold).

Skipped when the files don't exist (e.g. in CI, where no data is scraped).
Run after the pipelines: python -m pytest tests/test_quality_audit.py -v
"""
import pandas as pd
import pytest

from src.utils.helpers import PROJECT_ROOT, load_config

CFG = load_config()
SILVER_PATH = PROJECT_ROOT / CFG["paths"]["silver"]
GOLD_DIR = PROJECT_ROOT / CFG["paths"]["gold"]

GOLD_TABLES = [
    "jobs_by_role", "jobs_by_city", "jobs_by_role_city", "top_companies",
    "new_jobs_weekly", "employment_type_share", "bayt_job_status", "source_coverage",
]
REQUIRED_COLUMNS = ["job_id", "source", "title", "apply_url", "target_role", "first_seen_at", "last_seen_at"]
MAX_NULL_PCT = 5.0
MOSTLY_FILLED_COLUMNS = ["title", "apply_url", "description"]


# --- Silver -----------------------------------------------------------
@pytest.fixture(scope="module")
def silver() -> pd.DataFrame:
    if not SILVER_PATH.exists():
        pytest.skip(f"No silver file at {SILVER_PATH}")
    return pd.read_parquet(SILVER_PATH)


def test_silver_not_empty(silver):
    assert len(silver) > 0


def test_silver_job_id_unique_per_source(silver):
    dupes = silver[silver.duplicated(["source", "job_id"], keep=False)]
    assert dupes.empty, f"{len(dupes)} rows share source + job_id:\n{dupes[['source', 'job_id', 'title']].head()}"


def test_silver_apply_url_unique(silver):
    dupes = silver[silver["apply_url"].duplicated(keep=False)]
    assert dupes.empty, f"{len(dupes)} rows share an apply_url:\n{dupes[['source', 'title', 'apply_url']].head()}"


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_silver_required_column_has_no_nulls(silver, column):
    nulls = int(silver[column].isna().sum())
    assert nulls == 0, f"{column} has {nulls} nulls"


@pytest.mark.parametrize("column", MOSTLY_FILLED_COLUMNS)
def test_silver_null_pct_below_limit(silver, column):
    null_pct = silver[column].isna().mean() * 100
    assert null_pct < MAX_NULL_PCT, f"{column} is {null_pct:.1f}% null (limit {MAX_NULL_PCT}%)"


def test_silver_no_future_posted_dates(silver):
    now = pd.Timestamp.now(tz="UTC")
    future = silver[silver["posted_at"] > now]
    assert future.empty, f"{len(future)} jobs posted in the future:\n{future[['source', 'title', 'posted_at']].head()}"


def test_silver_first_seen_before_last_seen(silver):
    bad = silver[silver["first_seen_at"] > silver["last_seen_at"]]
    assert bad.empty, f"{len(bad)} rows have first_seen_at after last_seen_at"


def test_silver_salary_min_not_above_max(silver):
    bad = silver[silver["salary_min"] > silver["salary_max"]]
    assert bad.empty, f"{len(bad)} rows have salary_min > salary_max"


def test_silver_source_values(silver):
    unknown = set(silver["source"].unique()) - set(CFG["sources"])
    assert not unknown, f"Unknown sources: {unknown}"


def test_silver_status_values(silver):
    unknown = set(silver["status"].dropna().unique()) - {"active", "closed"}
    assert not unknown, f"Unknown status values: {unknown}"


def test_silver_target_role_values(silver):
    unknown = set(silver["target_role"].unique()) - set(CFG["role_keywords"])
    assert not unknown, f"target_role not in config role_keywords: {unknown}"


# --- Gold -------------------------------------------------------------
@pytest.fixture(scope="module")
def gold() -> dict[str, pd.DataFrame]:
    if not GOLD_DIR.exists():
        pytest.skip(f"No gold folder at {GOLD_DIR}")
    missing = [name for name in GOLD_TABLES if not (GOLD_DIR / f"{name}.parquet").exists()]
    assert not missing, f"Missing gold tables: {missing}"
    return {name: pd.read_parquet(GOLD_DIR / f"{name}.parquet") for name in GOLD_TABLES}


def test_gold_tables_have_rows(gold):
    # bayt_job_status can be empty if no Bayt data was scraped
    empty = [name for name, table in gold.items() if table.empty and name != "bayt_job_status"]
    assert not empty, f"Empty gold tables: {empty}"


@pytest.mark.parametrize("name", ["jobs_by_city", "employment_type_share"])
def test_gold_share_pct_sums_to_100(gold, name):
    assert gold[name]["share_pct"].sum() == pytest.approx(100, abs=0.5)


def test_gold_totals_consistent(gold):
    total = gold["jobs_by_role"]["jobs"].sum()
    assert gold["jobs_by_city"]["jobs"].sum() == total
    assert gold["jobs_by_role_city"]["jobs"].sum() == total
    assert gold["new_jobs_weekly"]["new_jobs"].sum() == total
    assert gold["source_coverage"]["relevant_jobs"].sum() == total


def test_gold_not_more_than_silver(gold, silver):
    assert gold["jobs_by_role"]["jobs"].sum() <= len(silver)
    assert gold["source_coverage"]["jobs"].sum() == len(silver), "gold was built from a different silver file"
