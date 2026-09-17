# -*- coding: utf-8 -*-
"""
Bayt.com Scraper — Multi-Layer Cloudflare-Resistant Scraper

Strategy:
    Layer 1 : curl_cffi + warm session (fast path)
    Layer 2 : Escalate to Scrapling's StealthyFetcher (Camoufox) on block
    Layer 3 : Optional residential proxy for IP reputation

Phases:
    Phase 1 — Full pagination scan
    Phase 2 — Status check for unseen jobs

Run:
    python -m src.scrapers.bayt_scraper
    python -m src.scrapers.bayt_scraper --fresh-start
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml
from bs4 import BeautifulSoup
from curl_cffi import requests as http


# ============================================================
# Constants
# ============================================================
TOTAL_COUNT_SELECTOR = 'div[data-automation-id="XJobsFound"]'
JOB_CARD_SELECTOR    = "#results_inner_card ul li[data-job-id]"
DETAIL_CARD_SELECTOR = "div#job_card"
VIEW_INNER_SELECTOR  = "div#view_inner"

PRESERVE_ON_EMPTY = {
    "title", "company", "company_url", "company_logo", "location",
    "summary", "experience", "career_level", "date_posted",
    "employment_type", "vacancies", "industry", "job_role",
    "years_of_experience", "residence_location", "nationality",
    "gender", "education", "salary",
    "responsibilities", "skills", "preferred_candidate",
    "languages", "benefits", "apply_url", "full_text", "job_url",
}

BLOCK_MARKERS = (
    "just a moment", "checking your browser",
    "verify you are human", "sorry, you have been blocked",
    "cf-challenge", "enable javascript and cookies",
    "attention required",
)

CLOSED_MARKERS = (
    "no longer available", "job expired", "job is closed",
    "position filled", "job not found", "404 not found",
    "تم إغلاق", "انتهت صلاحية", "غير متاح",
    "لم يتم العثور", "الوظيفة غير متوفرة", "لا توجد هذه الوظيفة",
)


# ============================================================
# Utilities
# ============================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} | {msg}", flush=True)


def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_session(impersonate: str = "chrome131",
                   proxy: str | None = None) -> http.Session:
    kwargs = {"impersonate": impersonate}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    s = http.Session(**kwargs)
    s.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    })
    return s


def _warm_session(session: http.Session) -> bool:
    """زيارة الصفحة الرئيسية أولاً لالتقاط الكوكيز."""
    try:
        r = session.get("https://www.bayt.com/", timeout=30)
        if r.status_code == 200:
            _log("[WARM] Home page OK")
            time.sleep(random.uniform(2, 4))
            return True
        _log(f"[WARM] Home page status={r.status_code}")
    except Exception as e:
        _log(f"[WARM] Failed: {e}")
    return False


def _is_blocked(html: str, status: int) -> bool:
    if status in (403, 429, 503):
        return True
    low = html[:3000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def _is_closed_page(html: str) -> bool:
    low = html[:5000].lower()
    return any(m in low for m in CLOSED_MARKERS)


def _with_page(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    parsed = urlparse(base_url)
    q = parse_qs(parsed.query)
    q["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in q.items()})))


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _first_text(el, selectors):
    for sel in selectors:
        found = el.select_one(sel)
        if found:
            txt = " ".join(found.get_text(" ", strip=True).split())
            if txt:
                return txt
    return ""


def _first_attr(el, selectors, attr="href"):
    for sel in selectors:
        found = el.select_one(sel)
        if found:
            v = found.get(attr)
            if v:
                return v
    return ""


# ============================================================
# Stealth Fetcher (Layer 2 fallback)
# ============================================================
class StealthFetcher:
    """
    Escalation layer using Scrapling's StealthyFetcher (Camoufox-based).
    Only invoked when curl_cffi returns 403/challenge.
    """
    def __init__(self):
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from scrapling.fetchers import StealthyFetcher  # noqa
            self._available = True
        except ImportError:
            self._available = False
            _log("[STEALTH] scrapling not installed — fallback disabled")
        return self._available

    def fetch(self, url: str, proxy: str | None = None) -> str | None:
        if not self.is_available():
            return None
        try:
            from scrapling.fetchers import StealthyFetcher
            kwargs = {"headless": True, "solve_cloudflare": True, "network_idle": True}
            if proxy:
                kwargs["proxy"] = proxy
            page = StealthyFetcher.fetch(url, **kwargs)
            if page and hasattr(page, "html_content"):
                return page.html_content
        except Exception as e:
            _log(f"[STEALTH] Failed: {e}")
        return None


# ============================================================
# Bronze IO
# ============================================================
def load_bronze(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                jid = str(obj.get("job_id", "")).strip()
                if jid:
                    out[jid] = obj
            except json.JSONDecodeError:
                continue
    return out


def merge_record(existing: dict, incoming: dict, now: str) -> dict:
    if not existing:
        merged = dict(incoming)
        merged.setdefault("first_seen_at", now)
        merged.setdefault("last_seen_at", now)
        merged["bronze_updated_at"] = now
        return merged
    merged = dict(existing)
    for k, v in incoming.items():
        if k in PRESERVE_ON_EMPTY and (v is None or v == ""):
            continue
        merged[k] = v
    merged["first_seen_at"] = existing.get("first_seen_at",
                                            existing.get("scraped_at", now))
    if incoming.get("last_seen_at"):
        merged["last_seen_at"] = incoming["last_seen_at"]
    else:
        merged.setdefault("last_seen_at", now)
    merged["bronze_updated_at"] = now
    return merged


def save_bronze(path: Path, records: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".raw_jobs_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            for rec in records.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ============================================================
# Scraper
# ============================================================
class BaytScraper:
    def __init__(self, cfg: dict, fresh_start: bool = False):
        self.cfg = cfg
        self.impersonate = cfg.get("antibot", {}).get("impersonate", "chrome131")
        self.proxy = cfg.get("antibot", {}).get("proxy")   # optional
        self.crawl_cfg = cfg["crawl"]
        self.max_pages = self.crawl_cfg["max_pages_per_url"]
        self.max_empty = self.crawl_cfg["max_empty_pages"]
        self.jobs_per_page = self.crawl_cfg.get("jobs_per_page", 25)
        self.detail_enabled = self.crawl_cfg["job_detail_enabled"]
        self.status_check_enabled = self.crawl_cfg.get("status_check_enabled", True)
        self.base_delay = cfg.get("throttle", {}).get("download_delay", 3.0)

        self.bronze_path = _PROJECT_ROOT / cfg["paths"]["bayt_bronze"]
        if fresh_start and self.bronze_path.exists():
            self.bronze_path.unlink()
            _log("[FRESH] bronze deleted")

        self.bronze = load_bronze(self.bronze_path)
        _log(f"[LOAD] {len(self.bronze)} existing records")

        self.session = _build_session(self.impersonate, self.proxy)
        self.stealth = StealthFetcher()
        self.seen_this_run: set[str] = set()
        self.dirty = False
        self._blocked = 0
        self._stealth_used = 0

    # ------------------------------------------------------------
    # HTTP with escalation
    # ------------------------------------------------------------
    def _get(self, url: str, referer: str | None = None,
             retries: int = 3) -> tuple[int, str]:
        headers = {}
        if referer:
            headers["Referer"] = referer

        # --- Layer 1: curl_cffi ---
        for attempt in range(1, retries + 1):
            try:
                r = self.session.get(url, timeout=45, headers=headers)
                self._sleep()
                if not _is_blocked(r.text or "", r.status_code):
                    return r.status_code, (r.text or "")
                _log(f"  [curl_cffi attempt {attempt}] blocked: HTTP {r.status_code}")
            except Exception as e:
                _log(f"  [curl_cffi attempt {attempt}] error: {e}")
                time.sleep(3 * attempt)

        # --- Layer 2: Stealth fetcher fallback ---
        self._blocked += 1
        _log(f"  [ESCALATE] using stealth fetcher for {url[:80]}")
        html = self.stealth.fetch(url, proxy=self.proxy)
        if html and not _is_blocked(html, 200):
            self._stealth_used += 1
            _log(f"  [STEALTH] success")
            return 200, html

        return 403, ""

    def _sleep(self) -> None:
        jitter = random.uniform(0, self.base_delay * 0.6)
        time.sleep(self.base_delay + jitter)

    # ------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------
    def _extract_card(self, card, category, language, source_url, page):
        jid = (card.get("data-job-id") or "").strip()
        if not jid.isdigit():
            return None

        job_url = _first_attr(card, ["h2 a", "h3 a", "a[href*='/jobs/']"])
        if job_url and job_url.startswith("/"):
            job_url = f"https://www.bayt.com{job_url}"
        elif job_url and not job_url.startswith("http"):
            job_url = urljoin(source_url, job_url)

        title = _first_text(card, ["h2 a", "h3 a", ".jb-title"])
        company = _first_text(card, [
            ".job-company-location-wrapper a.t-default.t-bold",
            "[class*='company'] a", "bdi",
        ])
        is_hidden = "محجوب" in company or "hidden" in company.lower()

        company_url = _first_attr(card, [
            ".job-company-location-wrapper a", "[class*='company'] a",
        ])
        if company_url and company_url.startswith("/"):
            company_url = f"https://www.bayt.com{company_url}"

        logo = _first_attr(card, ["img.jb-logo", "[class*='logo'] img"], "src")
        location = _first_text(card, [
            ".job-company-location-wrapper .t-mute.t-small a span",
            ".jb-loc", "[class*='location']",
        ])
        summary = _first_text(card, [".jb-descr", "div.t-small.m10t", "p.jb-desc"])
        summary = re.sub(r"^(ملخص|Summary)\s*[:\-]?\s*", "", summary,
                         flags=re.IGNORECASE)

        experience = career_level = ""
        for item in card.select(
            ".job-company-location-wrapper .t-mute.t-small .t-mute.t-small span"
        ):
            txt = item.get_text(" ", strip=True)
            if not txt:
                continue
            if "خبرة" in txt or "experience" in txt.lower():
                experience = txt
            elif any(k in txt for k in ("إدارة", "مبتدئ", "متوسط", "Management")):
                career_level = txt

        date_posted = _first_text(card, [
            "span[id^='jb-date-']", ".jb-date span.t-bold", "time",
            "[class*='date']",
        ])
        card_text = card.get_text(" ", strip=True)
        is_easy_apply = ("التقديم السريع" in card_text or "Easy Apply" in card_text)
        is_for_saudis_only = (
            "للمواطنين السعوديين" in card_text
            or "saudi nationals only" in card_text.lower()
        )

        return {
            "job_id": jid, "job_url": job_url, "title": title,
            "company": company, "company_url": company_url,
            "company_logo": logo, "location": location,
            "summary": summary, "experience": experience,
            "career_level": career_level, "date_posted": date_posted,
            "is_easy_apply": is_easy_apply,
            "is_for_saudis_only": is_for_saudis_only,
            "is_hidden_company": is_hidden,
            "category": category, "language": language,
            "source_url": source_url, "source_page": page,
        }

    def _extract_detail(self, html: str) -> dict:
        soup = _soup(html)
        out = {}
        full_text = " ".join(soup.get_text(" ", strip=True).split())
        out["full_text"] = full_text[:20000]

        card = soup.select_one(DETAIL_CARD_SELECTOR)
        if not card:
            return out

        mapping = {
            "employmentType": "employment_type", "vacancies": "vacancies",
            "industry": "industry", "jobRole": "job_role",
            "careerLevel": "career_level",
            "yearsOfExperience": "years_of_experience",
            "residenceLocation": "residence_location",
            "nationality": "nationality", "gender": "gender",
            "education": "education", "salary": "salary",
        }
        for row in card.select("[data-automation-id]"):
            aid = (row.get("data-automation-id") or "").strip()
            if not aid:
                continue
            txt = " ".join(row.get_text(" ", strip=True).split())
            if txt:
                out[mapping.get(aid, aid)] = txt

        for h2 in card.select("h2"):
            heading = h2.get_text(" ", strip=True)
            key = None
            if any(k in heading for k in ("مسؤوليات", "المسؤوليات")) or \
               "responsibilit" in heading.lower():
                key = "responsibilities"
            elif any(k in heading for k in ("مهارات", "المهارات")) or \
                 "skill" in heading.lower():
                key = "skills"
            elif "المرشح المفضل" in heading or "preferred" in heading.lower():
                key = "preferred_candidate"
            elif "لغات" in heading or "language" in heading.lower():
                key = "languages"
            elif "مزايا" in heading or "benefit" in heading.lower():
                key = "benefits"
            if not key:
                continue
            parts = []
            for sib in h2.find_next_siblings():
                if sib.name == "h2":
                    break
                txt = sib.get_text(" ", strip=True)
                if txt:
                    parts.append(txt)
            if parts:
                out[key] = "\n".join(parts)

        apply_el = card.select_one(
            "a[data-automation-id='applyButton'], a.jb-apply-btn, a[href*='apply']"
        )
        if apply_el:
            out["apply_url"] = apply_el.get("href", "")
        return out

    @staticmethod
    def _extract_total_count(soup: BeautifulSoup) -> int | None:
        el = soup.select_one(TOTAL_COUNT_SELECTOR)
        if el:
            m = re.search(r"(\d[\d,]*)", el.get_text(" ", strip=True))
            if m:
                return int(m.group(1).replace(",", ""))
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(\d[\d,]*)\s*(?:jobs?|وظيفة|وظائف)", text)
        return int(m.group(1).replace(",", "")) if m else None

    # ------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------
    def phase1(self) -> None:
        urls = self.cfg["job_search_urls"]
        total = sum(len(v) for v in urls.values())
        _log(f"\n=== PHASE 1 — {total} search URLs ===")

        # Warm-up session once
        _warm_session(self.session)

        for category, langs in urls.items():
            for lang, base_url in langs.items():
                self._scan_search_url(category, lang, base_url)

    def _scan_search_url(self, category: str, lang: str, base_url: str) -> None:
        _log(f"\n[{lang}/{category}]")

        status, html = self._get(base_url)
        if status != 200 or not html:
            _log(f"  [FAIL] HTTP {status}")
            return

        soup = _soup(html)
        total = self._extract_total_count(soup)
        max_pages = self.max_pages
        if total:
            max_pages = min(max_pages,
                            (total + self.jobs_per_page - 1) // self.jobs_per_page)
            _log(f"  XJobsFound={total} | pages={max_pages}")

        empty_streak = 0
        for page in range(1, max_pages + 1):
            if page > 1:
                page_url = _with_page(base_url, page)
                status, html = self._get(page_url, referer=base_url)
                if status != 200 or not html:
                    _log(f"  page {page}: HTTP {status}")
                    empty_streak += 1
                    if empty_streak >= self.max_empty:
                        break
                    continue
                soup = _soup(html)

            cards = soup.select(JOB_CARD_SELECTOR)
            _log(f"  page {page}/{max_pages} -> {len(cards)} cards")

            if not cards:
                empty_streak += 1
                if empty_streak >= self.max_empty:
                    _log("  empty streak reached")
                    break
                continue
            empty_streak = 0

            for card in cards:
                job = self._extract_card(card, category, lang, base_url, page)
                if not job:
                    continue
                jid = job["job_id"]
                self.seen_this_run.add(jid)

                if jid in self.bronze:
                    incoming = {
                        "job_id": jid, "status": "active",
                        "last_seen_at": _now(), "scraped_at": _now(),
                        "job_url": self.bronze[jid].get("job_url") or job["job_url"],
                    }
                    self.bronze[jid] = merge_record(self.bronze[jid], incoming, _now())
                    self.dirty = True
                else:
                    if self.detail_enabled and job.get("job_url"):
                        self._fetch_and_store(job)
                    else:
                        job["status"] = "active"
                        job["scraped_at"] = _now()
                        job["last_seen_at"] = job["scraped_at"]
                        self.bronze[jid] = merge_record({}, job, _now())
                        self.dirty = True

    def _fetch_and_store(self, job: dict) -> None:
        status, html = self._get(job["job_url"], referer=job.get("source_url"))
        if status != 200 or not html:
            _log(f"    [FAIL] {job['job_id']}")
            return
        if _is_closed_page(html):
            job["status"] = "closed"
        else:
            detail = self._extract_detail(html)
            job.update({k: v for k, v in detail.items() if v})
            job["status"] = "active"

        job["scraped_at"] = _now()
        job["last_seen_at"] = job["scraped_at"]
        self.bronze[job["job_id"]] = merge_record(
            self.bronze.get(job["job_id"], {}), job, _now()
        )
        self.dirty = True
        _log(f"    [NEW] {job['job_id']} — {job.get('title', '')[:60]}")

    def phase2(self) -> None:
        if not self.status_check_enabled:
            return
        unseen = set(self.bronze.keys()) - self.seen_this_run
        to_check = [
            jid for jid in unseen
            if self.bronze[jid].get("status", "") in ("active", "unknown", "")
        ]
        _log(
            f"\n=== PHASE 2 — Status check ===\n"
            f"  Bronze: {len(self.bronze)} | Seen: {len(self.seen_this_run)} | "
            f"Unseen: {len(unseen)} | To check: {len(to_check)}"
        )
        for i, jid in enumerate(to_check, 1):
            url = self.bronze[jid].get("job_url")
            if not url:
                continue
            status, html = self._get(url)
            if status != 200 or not html:
                continue
            soup = _soup(html)
            has_card = bool(soup.select_one(
                f"{DETAIL_CARD_SELECTOR}, {VIEW_INNER_SELECTOR}"
            ))
            if has_card:
                incoming = {"job_id": jid, "status": "active",
                            "last_verified_at": _now(), "scraped_at": _now()}
                _log(f"  [{i}/{len(to_check)}] {jid}: active")
            elif _is_closed_page(html):
                incoming = {"job_id": jid, "status": "closed",
                            "last_verified_at": _now(), "scraped_at": _now()}
                _log(f"  [{i}/{len(to_check)}] {jid}: CLOSED")
            else:
                continue
            self.bronze[jid] = merge_record(self.bronze[jid], incoming, _now())
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            _log("Bronze unchanged")
            return
        save_bronze(self.bronze_path, self.bronze)
        _log(f"[OK] Bronze saved: {len(self.bronze)} records")

    def run(self) -> None:
        try:
            self.phase1()
            self.phase2()
        finally:
            self.save()
            _log(f"\n[STATS] blocked={self._blocked} | stealth_used={self._stealth_used}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()
    cfg = _load_config(args.config)
    BaytScraper(cfg, fresh_start=args.fresh_start).run()


if __name__ == "__main__":
    main()