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
