import re
from functools import lru_cache
from pathlib import Path

import yaml

# helpers.py is at: <root>/src/utils/helpers.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- Config -----------------------------------------------------------
@lru_cache(maxsize=1)
def load_config() -> dict:
    """Read config/config.yaml once."""
    with open(PROJECT_ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, float) and value != value) or str(value).strip() in ("", "<NA>")


# --- Location ---------------------------------------------------------
# Keys are lowercase with punctuation removed (see _city_key)
CITY_ALIASES = {
    "al khubar": "Al Khobar", "khobar": "Al Khobar", "al khobar": "Al Khobar",
    "al jubayl": "Al Jubail", "jubail": "Al Jubail", "al jubail": "Al Jubail",
    "al kharj": "Al Kharj", "kharj": "Al Kharj",
    "makkah": "Makkah", "mecca": "Makkah", "makkah al mukarramah": "Makkah",
    "madinah": "Madinah", "medina": "Madinah", "al madinah": "Madinah",
    "yanbu al sinaiyah": "Yanbu", "yanbu al bahr": "Yanbu", "yanbu": "Yanbu",
    "ad dammam": "Dammam", "dammam": "Dammam",
    "al quwayiyah": "Al Quwayiyah",
    "ar riyad": "Riyadh", "riyadh": "Riyadh",
    "jiddah": "Jeddah", "jeddah": "Jeddah",
    "jizan": "Jazan", "jazan": "Jazan",
    "ash sharqiyah": "Eastern Province", "eastern province": "Eastern Province",
}

SAUDI_CITIES = {
    "Riyadh", "Jeddah", "Dammam", "Al Khobar", "Dhahran", "Al Jubail", "Makkah",
    "Madinah", "Yanbu", "Jazan", "Al Kharj", "Al Quwayiyah", "Tabuk", "Abha",
    "Taif", "Hail", "Buraydah", "Khamis Mushait", "Najran", "Al Ahsa", "Hofuf",
    "Qatif", "Neom", "Thuwal", "Ras Tanura", "Eastern Province",
}

FOREIGN_PLACES = ["tel aviv", "herzliya", "israel", "dubai", "abu dhabi", "qatar", "doha",
                  "kuwait", "bahrain", "egypt", "cairo", "jordan", "amman", "india", "pakistan"]


def _city_key(text: str) -> str:
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^\w\s]", "", text)  # "yanbu' al bahr" -> "yanbu al bahr"
    return re.sub(r"\s+", " ", text).strip()


def normalize_city(value) -> str | None:
    """'Al Khubar' -> 'Al Khobar', 'Al-Kharj' -> 'Al Kharj'."""
    if _is_blank(value):
        return None
    key = _city_key(str(value))
    if not key:
        return None
    return CITY_ALIASES.get(key, key.title())


def split_location(text) -> tuple[str | None, str | None]:
    """'Jeddah, Makkah' -> ('Jeddah', 'Makkah'); 'Saudi Arabia' -> (None, None)."""
    if _is_blank(text) or str(text).strip().lower() == "saudi arabia":
        return None, None
    parts = [p.strip() for p in str(text).split(",")]
    return parts[0], (parts[1] if len(parts) > 1 else None)


def is_saudi(city, location_raw, country) -> bool | None:
    """True/False when we can tell, None when unknown."""
    location = "" if _is_blank(location_raw) else str(location_raw).lower()
    if any(place in location for place in FOREIGN_PLACES):
        return False
    if not _is_blank(country):
        return str(country).strip().upper() == "SA"
    first_place = location.split(",")[0].split("(")[0]
    if normalize_city(city) in SAUDI_CITIES or normalize_city(first_place) in SAUDI_CITIES:
        return True
    if "saudi" in location or re.search(r"\bksa\b", location):
        return True
    return None


# --- Company ----------------------------------------------------------
HIDDEN_COMPANIES = {"confidential", "confidential employer", "confidential company", "undisclosed"}
LEGAL_SUFFIXES = r"\b(inc|llc|ltd|limited|co|corp|company|technologies|global service|group)\b"


def normalize_company(value) -> str | None:
    """Trim spaces; hidden employer names -> None."""
    if _is_blank(value):
        return None
    name = re.sub(r"\s+", " ", str(value)).strip()
    if name.lower() in HIDDEN_COMPANIES:
        return None
    return name


def company_key(value) -> str:
    """Match key: 'Qualcomm Technologies, Inc' and 'Qualcomm' -> 'qualcomm'."""
    name = normalize_company(value)
    if name is None:
        return ""
    name = re.sub(LEGAL_SUFFIXES, "", name.lower())
    return re.sub(r"[^\w]+", "", name)


# --- Title / text -----------------------------------------------------
CONTRACT_TAGS = [
    r"\(\s*\d+\s*months?\s*\)",          # (3 Months)
    r"-?\s*immediate joining",           # - Immediate Joining
]


def clean_title(value) -> str | None:
    """'Data, Engineer' -> 'Data Engineer'; drops '(3 Months)' style tags."""
    if _is_blank(value):
        return None
    title = str(value).replace("‐", "-").replace("‑", "-")
    for pattern in CONTRACT_TAGS:
        title = re.sub(pattern, " ", title, flags=re.IGNORECASE)
    title = re.sub(r"(\w),\s+(\w)", r"\1 \2", title)   # stray comma between words
    title = re.sub(r"\s+", " ", title).strip(" -|,")
    return title or None


def strip_html(value) -> str | None:
    """Remove leftover HTML tags and collapse whitespace."""
    if _is_blank(value):
        return None
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _contains_word(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def is_relevant(title, target_role, cfg: dict | None = None) -> bool:
    """Title matches the searched role and is not an unrelated engineering job."""
    if _is_blank(title) or _is_blank(target_role):
        return False
    cfg = cfg or load_config()
    text = str(title).lower()
    if any(_contains_word(text, word) for word in cfg["role_exclude"]):
        return False
    keywords = cfg["role_keywords"].get(str(target_role).lower(), [])
    return any(_contains_word(text, word) for word in keywords)


# --- Salary -----------------------------------------------------------
SALARY_RE = re.compile(r"([\d,.]+)\s*(?:-\s*([\d,.]+))?\s*per\s+(\w+)", re.IGNORECASE)


def parse_salary(text) -> tuple[float | None, float | None, str | None, str | None]:
    """'$3000 per month' -> (3000.0, 3000.0, 'month', 'USD')."""
    if _is_blank(text):
        return None, None, None, None
    text = str(text)
    match = SALARY_RE.search(text)
    if not match:
        return None, None, None, None
    low = float(match.group(1).replace(",", ""))
    high = float(match.group(2).replace(",", "")) if match.group(2) else low

    if "$" in text or "usd" in text.lower():
        currency = "USD"
    elif "sar" in text.lower() or "ريال" in text:
        currency = "SAR"
    else:
        currency = None
    return low, high, match.group(3).lower(), currency
