import pandas as pd
import pytest

from src.pipelines.gold_pipeline import (
    build_gold,
    employment_type_share,
    filter_jobs,
    jobs_by_city,
    jobs_by_role,
    new_jobs_weekly,
    source_coverage,
    top_companies,
)


@pytest.fixture
def silver() -> pd.DataFrame:
    return pd.DataFrame({
        "job_id": ["1", "2", "3", "4", "5"],
        "source": ["jsearch", "bayt", "bayt", "careerjet", "jsearch"],
        "target_role": ["data engineer", "data engineer", "ai engineer", "ai engineer", "data engineer"],
        "company": pd.array(["Aramco", "Aramco", None, "Mozn", "Salla"], dtype="string"),
        "city": pd.array(["Riyadh", "Jeddah", None, "Riyadh", "Riyadh"], dtype="string"),
        "is_remote": pd.array([True, False, None, False, False], dtype="boolean"),
        "employment_type": pd.array(["Full-time", None, "Full-time", None, "Full-time"], dtype="string"),
        "salary_min": pd.array([5000.0, None, None, None, None], dtype="Float64"),
        "status": pd.array([None, "active", "closed", None, None], dtype="string"),
        # Wed 2026-09-16 -> week of Mon 2026-09-14; Mon 2026-09-07 stays itself
        "first_seen_at": pd.to_datetime(
            ["2026-09-16", "2026-09-07", "2026-09-16", "2026-09-17", "2026-09-10"], utc=True),
        "is_relevant": pd.array([True, True, True, True, False], dtype="boolean"),
        "is_saudi": pd.array([True, True, True, None, True], dtype="boolean"),
    })


def test_filter_jobs_drops_not_relevant_and_not_saudi(silver):
    kept = filter_jobs(silver)
    assert kept["job_id"].tolist() == ["1", "2", "3"]   # 4: is_saudi unknown, 5: not relevant


def test_jobs_by_role(silver):
    out = jobs_by_role(filter_jobs(silver)).set_index("target_role")
    assert out.loc["data engineer", "jobs"] == 2
    assert out.loc["data engineer", "companies"] == 1
    assert out.loc["data engineer", "remote_pct"] == 50.0
    assert out.loc["data engineer", "with_salary_pct"] == 50.0
    assert out.loc["ai engineer", "jobs"] == 1


def test_jobs_by_city_unknown_and_share(silver):
    out = jobs_by_city(filter_jobs(silver))
    assert set(out["city"]) == {"Riyadh", "Jeddah", "Unknown"}
    assert out["share_pct"].sum() == pytest.approx(100, abs=0.2)


def test_employment_type_share_sums_to_100(silver):
    out = employment_type_share(filter_jobs(silver))
    assert out["share_pct"].sum() == pytest.approx(100, abs=0.2)


def test_top_companies_skips_hidden(silver):
    out = top_companies(filter_jobs(silver))
    assert out["company"].tolist() == ["Aramco"]
    assert out.loc[0, "cities"] == "Jeddah, Riyadh"


def test_new_jobs_weekly_uses_monday(silver):
    out = new_jobs_weekly(filter_jobs(silver))
    assert sorted(out["week_start"].dt.strftime("%Y-%m-%d").unique()) == ["2026-09-07", "2026-09-14"]
    assert out["new_jobs"].sum() == 3


def test_source_coverage(silver):
    out = source_coverage(silver).set_index("source")
    assert out.loc["jsearch", "jobs"] == 2
    assert out.loc["jsearch", "relevant_jobs"] == 1
    assert out.loc["careerjet", "relevant_jobs"] == 0


def test_build_gold_totals_match(silver):
    tables = build_gold(silver)
    assert len(tables) == 8
    assert tables["jobs_by_role"]["jobs"].sum() == len(filter_jobs(silver))
    assert tables["bayt_job_status"]["jobs"].sum() == 2
