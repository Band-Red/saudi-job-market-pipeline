"""Run the whole pipeline: scrapers -> silver -> gold, uploading each layer to Azure.

Run:
    python -m src.pipelines.run_pipeline                  # everything
    python -m src.pipelines.run_pipeline --skip-scrape    # use existing bronze files
    python -m src.pipelines.run_pipeline --no-upload      # don't upload to Azure
    python -m src.pipelines.run_pipeline --sources careerjet jsearch
    python -m src.pipelines.run_pipeline --fresh-silver   # rebuild silver from local bronze only

Silver = previous silver (from Azure, or the local file if Azure is not reachable)
         + new bronze rows, cleaned and deduplicated together.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running this file directly (VS Code Run button), not only with python -m
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.pipelines import gold_pipeline, silver_pipeline  # noqa: E402
from src.utils.helpers import PROJECT_ROOT, load_config  # noqa: E402

SCRAPERS = {
    "careerjet": "src.scrapers.careerjet",
    "jsearch": "src.scrapers.JSearch",
    "bayt": "src.scrapers.bayt_scraper",
}


def run_scraper(source: str) -> bool:
    """Run one scraper in its own process so a crash doesn't stop the others."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", SCRAPERS[source]], cwd=PROJECT_ROOT, env=env)
    return result.returncode == 0


def upload(local_dir: Path, container: str, prefix: str = "", pattern: str = "*", **options) -> bool:
    """Upload a folder to Azure; print a count per result (uploaded / updated / skipped)."""
    from src.utils.azure_storage import upload_folder  # imported here so --no-upload works without Azure

    try:
        results = upload_folder(local_dir, container, prefix, pattern, **options)
    except Exception as error:  # noqa: BLE001 — report and let the next step run
        print(f"  Azure upload to '{container}' failed: {error}")
        return False

    counts = {status: list(results.values()).count(status) for status in ("uploaded", "updated", "skipped")}
    print(f"  Azure '{container}/{prefix}': {counts}")
    return True


def load_previous_silver(silver_path: Path, container: str, use_azure: bool) -> pd.DataFrame | None:
    """Old silver to combine with the new data: Azure copy first, local file as fallback."""
    if use_azure:
        from src.utils.azure_storage import download_file

        with tempfile.TemporaryDirectory() as tmp:
            tmp_file = Path(tmp) / silver_path.name
            try:
                if download_file(container, silver_path.name, tmp_file):
                    previous = pd.read_parquet(tmp_file)
                    print(f"  Previous silver from Azure: {len(previous)} rows")
                    return previous
                print("  No silver in Azure yet")
            except Exception as error:  # noqa: BLE001
                print(f"  Could not download silver from Azure: {error}")

    if silver_path.exists():
        previous = pd.read_parquet(silver_path)
        print(f"  Previous silver from local file: {len(previous)} rows")
        return previous

    print("  No previous silver: building from bronze only")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scrapers, silver and gold, and upload to Azure")
    parser.add_argument("--skip-scrape", action="store_true", help="don't run the scrapers")
    parser.add_argument("--no-upload", action="store_true", help="don't upload to Azure")
    parser.add_argument("--sources", nargs="+", choices=list(SCRAPERS), default=list(SCRAPERS))
    parser.add_argument("--fresh-silver", action="store_true",
                        help="ignore the previous silver and rebuild from local bronze only")
    args = parser.parse_args()

    cfg = load_config()
    containers = cfg["azure"]["containers"]
    bronze_dir = PROJECT_ROOT / cfg["paths"]["bronze"]
    silver_path = PROJECT_ROOT / cfg["paths"]["silver"]
    gold_dir = PROJECT_ROOT / cfg["paths"]["gold"]
    status: dict[str, str] = {}
    start = time.time()

    # Check Azure once, so a login problem doesn't slow down every upload
    if not args.no_upload:
        from src.utils.azure_storage import check_access

        error = check_access()
        if error:
            print(f"Azure not reachable, uploads skipped this run:\n  {error}")
            status["azure access"] = "FAILED"
            args.no_upload = True

    # 1. Bronze: each scraper, then upload its folder.
    #    Add-only: files already in Azure are never changed or deleted; undated names get today's date.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for source in args.sources:
        if not args.skip_scrape:
            print(f"\n=== Scraper: {source} ===")
            status[f"scrape {source}"] = "ok" if run_scraper(source) else "FAILED"
        if not args.no_upload:
            ok = upload(bronze_dir / source, containers["bronze"], prefix=source, add_only=True, date_str=today)
            status[f"upload bronze/{source}"] = "ok" if ok else "FAILED"

    # 2. Silver
    print("\n=== Silver ===")
    try:
        previous = None if args.fresh_silver else load_previous_silver(
            silver_path, containers["silver"], use_azure=not args.no_upload)
        silver_pipeline.save_silver(silver_pipeline.build_silver(cfg, previous), cfg)
        status["silver"] = "ok"
    except Exception as error:  # noqa: BLE001
        print(f"  Silver failed: {error}")
        status["silver"] = "FAILED"
    if not args.no_upload and status["silver"] == "ok":
        ok = upload(silver_path.parent, containers["silver"], pattern=silver_path.name)
        status["upload silver"] = "ok" if ok else "FAILED"

    # 3. Gold (only if silver worked)
    if status["silver"] == "ok":
        print("\n=== Gold ===")
        try:
            gold_pipeline.save_gold(gold_pipeline.build_gold(gold_pipeline.load_silver(cfg)), cfg)
            status["gold"] = "ok"
        except Exception as error:  # noqa: BLE001
            print(f"  Gold failed: {error}")
            status["gold"] = "FAILED"
        if not args.no_upload and status["gold"] == "ok":
            ok = upload(gold_dir, containers["gold"], pattern="*.parquet")
            status["upload gold"] = "ok" if ok else "FAILED"

    # Summary
    print(f"\n=== Summary ({time.time() - start:.0f}s) ===")
    for step, result in status.items():
        print(f"  {step:<24} {result}")
    return 1 if "FAILED" in status.values() else 0


if __name__ == "__main__":
    sys.exit(main())
