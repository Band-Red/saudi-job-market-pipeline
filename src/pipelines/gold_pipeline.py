import sys
from pathlib import Path

# Allow running this file directly (VS Code Run button), not only with python -m
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.utils.helpers import PROJECT_ROOT, load_config  # noqa: E402

TOP_COMPANIES = 50


# --- 1. Load and filter silver ----------------------------------------
def load_silver(cfg: dict) -> pd.DataFrame:
    return pd.read_parquet(PROJECT_ROOT / cfg["paths"]["silver"])


def filter_jobs(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only jobs that match their role and are in Saudi Arabia."""
    keep = df["is_relevant"].fillna(False).astype(bool) & df["is_saudi"].fillna(False).astype(bool)
    return df[keep].copy()


def _share(counts: pd.Series) -> pd.Series:
    return (counts / counts.sum() * 100).round(1)


# --- 2. Summary tables ------------------------------------------------
def jobs_by_role(df: pd.DataFrame) -> pd.DataFrame:
    out = df.groupby("target_role").agg(
        jobs=("job_id", "size"),
        companies=("company", "nunique"),
        remote_pct=("is_remote", lambda s: round(s.fillna(False).astype(bool).mean() * 100, 1)),
        with_salary_pct=("salary_min", lambda s: round(s.notna().mean() * 100, 1)),
    )
    return out.sort_values("jobs", ascending=False).reset_index()


def jobs_by_city(df: pd.DataFrame) -> pd.DataFrame:
    counts = df["city"].fillna("Unknown").value_counts()
    out = pd.DataFrame({"city": counts.index, "jobs": counts.values})
    out["share_pct"] = _share(out["jobs"]).values
    return out


def jobs_by_role_city(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.assign(city=df["city"].fillna("Unknown"))
        .groupby(["target_role", "city"]).size().rename("jobs").reset_index()
    )
    return out.sort_values(["target_role", "jobs"], ascending=[True, False]).reset_index(drop=True)


def top_companies(df: pd.DataFrame, limit: int = TOP_COMPANIES) -> pd.DataFrame:
    def join_unique(s: pd.Series) -> str:
        return ", ".join(sorted(s.dropna().unique()))

    out = df.dropna(subset=["company"]).groupby("company").agg(
        jobs=("job_id", "size"),
        roles=("target_role", join_unique),
        cities=("city", join_unique),
    )
    return out.sort_values(["jobs", "company"], ascending=[False, True]).head(limit).reset_index()


def new_jobs_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """New jobs per week, by the week (Monday) they were first seen."""
    first_seen = df["first_seen_at"].dt.tz_convert("UTC").dt.tz_localize(None)
    week_start = (first_seen - pd.to_timedelta(first_seen.dt.weekday, unit="D")).dt.normalize()
    out = (
        df.assign(week_start=week_start)
        .groupby(["week_start", "target_role"]).size().rename("new_jobs").reset_index()
    )
    return out.sort_values(["week_start", "target_role"]).reset_index(drop=True)


def employment_type_share(df: pd.DataFrame) -> pd.DataFrame:
    counts = df["employment_type"].fillna("Unknown").value_counts()
    out = pd.DataFrame({"employment_type": counts.index, "jobs": counts.values})
    out["share_pct"] = _share(out["jobs"]).values
    return out


def bayt_job_status(df: pd.DataFrame) -> pd.DataFrame:
    bayt = df[df["source"] == "bayt"]
    return bayt.groupby(["target_role", "status"]).size().rename("jobs").reset_index()


def source_coverage(silver: pd.DataFrame) -> pd.DataFrame:
    """Uses the unfiltered silver table: how much each source adds after filtering."""
    kept = filter_jobs(silver)
    out = pd.DataFrame({
        "jobs": silver["source"].value_counts(),
        "relevant_jobs": kept["source"].value_counts(),
    }).fillna(0).astype(int)
    out["relevant_pct"] = (out["relevant_jobs"] / out["jobs"] * 100).round(1)
    return out.rename_axis("source").sort_values("jobs", ascending=False).reset_index()


# --- 3. Build and write -----------------------------------------------
def build_gold(silver: pd.DataFrame) -> dict[str, pd.DataFrame]:
    jobs = filter_jobs(silver)
    print(f"Silver rows: {len(silver)} | kept after filter: {len(jobs)} | removed: {len(silver) - len(jobs)}")
    return {
        "jobs_by_role": jobs_by_role(jobs),
        "jobs_by_city": jobs_by_city(jobs),
        "jobs_by_role_city": jobs_by_role_city(jobs),
        "top_companies": top_companies(jobs),
        "new_jobs_weekly": new_jobs_weekly(jobs),
        "employment_type_share": employment_type_share(jobs),
        "bayt_job_status": bayt_job_status(jobs),
        "source_coverage": source_coverage(silver),
    }


def save_gold(tables: dict[str, pd.DataFrame], cfg: dict) -> Path:
    out_dir = PROJECT_ROOT / cfg["paths"]["gold"]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_parquet(out_dir / f"{name}.parquet", index=False)
    return out_dir


if __name__ == "__main__":
    config = load_config()
    gold = build_gold(load_silver(config))
    folder = save_gold(gold, config)

    print(f"\nSaved {len(gold)} tables to {folder}")
    for table_name, table in gold.items():
        print(f"\n{table_name} ({len(table)} rows)")
        print(table.head(3).to_string(index=False))
