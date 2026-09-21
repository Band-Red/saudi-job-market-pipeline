import pytest

from src.utils.helpers import (
    clean_title,
    company_key,
    is_relevant,
    is_saudi,
    normalize_city,
    normalize_company,
    parse_salary,
    split_location,
    strip_html,
)


@pytest.mark.parametrize("raw, expected", [
    ("Al Khubar", "Al Khobar"),
    ("Al Jubayl", "Al Jubail"),
    ("Al-Kharj", "Al Kharj"),
    ("Yanbu' al Bahr", "Yanbu"),
    ("riyadh", "Riyadh"),
    (None, None),
])
def test_normalize_city(raw, expected):
    assert normalize_city(raw) == expected


def test_split_location():
    assert split_location("Jeddah, Makkah") == ("Jeddah", "Makkah")
    assert split_location("Saudi Arabia") == (None, None)


@pytest.mark.parametrize("raw, expected", [
    ("Data, Engineer", "Data Engineer"),
    ("Data Engineer (Python) - Immediate Joining (3 Months)", "Data Engineer (Python)"),
    ("Resident Construction Engineer ‐ Architect", "Resident Construction Engineer - Architect"),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_normalize_company():
    assert normalize_company("Confidential Employer") is None
    assert normalize_company("confidential") is None
    assert normalize_company("  Bupa   Arabia ") == "Bupa Arabia"


def test_company_key():
    assert company_key("InnovationTeam") == company_key("Innovationteam")
    assert company_key("Qualcomm Technologies, Inc") == company_key("Qualcomm")
    assert company_key(None) == ""


def test_strip_html():
    assert strip_html("Systems <b>Engineer</b>   Location") == "Systems Engineer Location"


def test_parse_salary():
    assert parse_salary("$3000 per month") == (3000.0, 3000.0, "month", "USD")
    assert parse_salary("6000 - 9000 per month") == (6000.0, 9000.0, "month", None)
    assert parse_salary(None) == (None, None, None, None)


@pytest.mark.parametrize("title, role, expected", [
    ("Civil Engineer", "ai engineer", False),
    ("Generative AI Engineer", "ai engineer", True),
    ("Senior Maintenance Engineer", "ai engineer", False),   # "ai" inside a word must not match
    ("Big Data Engineer", "data engineer", True),
    ("Senior Electrical Engineer - Data Centre", "data engineer", False),
    ("Senior PHP Backend Developer", "backend developer", True),
    ("Full-Stack Developer", "frontend developer", True),
])
def test_is_relevant(title, role, expected):
    assert is_relevant(title, role) is expected


def test_is_saudi():
    assert is_saudi(None, "Herzliya, Tel Aviv District - Ha'il", None) is False
    assert is_saudi("Riyadh", "Riyadh", None) is True
    assert is_saudi(None, "Saudi Arabia", "SA") is True
    assert is_saudi(None, "Al Jubail   (+3 others)", None) is True
    assert is_saudi(None, None, None) is None


# --- Cross-source duplicates ------------------------------------------
def test_dedupe_cross_source_hidden_company_same_link():
    import pandas as pd
    from src.pipelines.silver_pipeline import dedupe_cross_source

    df = pd.DataFrame({
        "source": ["bayt", "jsearch"],
        "job_id": ["5482604", "abc"],
        "title": ["AI Researcher", "AI Researcher"],
        "company": pd.array([None, None], dtype="string"),
        "city": ["Riyadh", "Riyadh"],
        "apply_url": pd.array(["https://www.bayt.com/en/x-5482604/", "https://www.bayt.com/en/x-5482604"], dtype="string"),
        "first_seen_at": pd.to_datetime(["2026-09-17", "2026-09-17"], utc=True),
    })
    out = dedupe_cross_source(df)
    assert out["source"].tolist() == ["jsearch"]


# --- Bayt -------------------------------------------------------------
from src.pipelines.silver_pipeline import map_bayt  # noqa: E402
from src.utils.helpers import normalize_employment_type, split_bayt_location  # noqa: E402


def test_split_bayt_location():
    assert split_bayt_location("Al Marwah , Riyadh , Saudi Arabia") == ("Riyadh", "SA")
    assert split_bayt_location("Jeddah , Saudi Arabia") == ("Jeddah", "SA")
    assert split_bayt_location("Saudi Arabia") == (None, "SA")


@pytest.mark.parametrize("raw, expected", [
    ("Full time", "Full-time"),
    ("Full-time", "Full-time"),
    ("FULLTIME", "Full-time"),
    ("Contractor", "Contract"),
    ("No experience required", None),
    (None, None),
])
def test_normalize_employment_type(raw, expected):
    assert normalize_employment_type(raw) == expected


def test_map_bayt():
    import pandas as pd

    raw = pd.DataFrame([
        {"job_id": "1", "category": "data engineer", "title": "Data Analyst",
         "company": "Confidential Company", "is_hidden_company": True,
         "location": "Riyadh , Saudi Arabia", "date_posted_ts": 1789555539,
         "id_type_level_experience": "Full time · 2+ Years of Experience", "id_remote_working": None,
         "summary": "Analyze data", "job_url": "https://www.bayt.com/en/x-1/", "status": "active",
         "first_seen_at": "2026-09-01T00:00:00+00:00", "last_seen_at": "2026-09-17T00:00:00+00:00"},
        {"job_id": "2", "category": "data engineer", "title": "Big Data Engineer",
         "company": "Peroptyx", "is_hidden_company": False,
         "location": "Jeddah , Saudi Arabia", "date_posted_ts": 1787469180,
         "id_type_level_experience": "Part time", "id_remote_working": "Remote",
         "summary": None, "full_text": "Full page text", "job_url": "https://www.bayt.com/en/x-2/", "status": "closed",
         "first_seen_at": "2026-09-10T00:00:00+00:00", "last_seen_at": "2026-09-12T00:00:00+00:00"},
    ])
    out = map_bayt(raw)
    assert out.loc[0, "company"] is None or pd.isna(out.loc[0, "company"])
    assert out.loc[1, "company"] == "Peroptyx"
    assert out["city"].tolist() == ["Riyadh", "Jeddah"]
    assert out["country"].tolist() == ["SA", "SA"]
    assert out["employment_type"].tolist() == ["Full time", "Part time"]
    assert out["is_remote"].tolist() == [False, True]
    assert out.loc[1, "description"] == "Full page text"
    assert out["status"].tolist() == ["active", "closed"]
    assert str(out.loc[0, "posted_at"].date()) == "2026-09-16"  # posted "22 hours ago" on Sep 17


# --- Careerjet id -----------------------------------------------------
def test_careerjet_job_id_ignores_changing_url():
    import pandas as pd
    from src.pipelines.silver_pipeline import careerjet_job_id

    df = pd.DataFrame({
        "title": ["Data Engineer", "data engineer ", "Data Engineer"],
        "company": ["Mozn", "MOZN", "Mozn"],
        "locations": ["Riyadh", "Riyadh", "Jeddah"],
        "url": ["https://jobviewtrack.com/v2/aaa", "https://jobviewtrack.com/v2/bbb", "https://jobviewtrack.com/v2/aaa"],
    })
    ids = careerjet_job_id(df)
    assert ids[0] == ids[1]      # same job, different daily tracking link
    assert ids[0] != ids[2]      # same title, other city -> other job
