"""Azure upload logic tested with a fake container (no network, no login)."""
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from src.utils import azure_storage
from src.utils.azure_storage import dated_name, file_md5, upload_file, upload_folder


class FakeBlob:
    def __init__(self, store: dict, name: str):
        self.store, self.name = store, name

    def get_blob_properties(self):
        if self.name not in self.store:
            raise ResourceNotFoundError("missing")
        return SimpleNamespace(content_settings=SimpleNamespace(content_md5=self.store[self.name]["md5"]))

    def upload_blob(self, data, overwrite, content_settings):
        if not overwrite and self.name in self.store:
            raise ResourceExistsError("exists")
        self.store[self.name] = {"data": data.read(), "md5": bytearray(content_settings.content_md5)}


class FakeContainer:
    def __init__(self):
        self.store = {}
        self.uploads = 0

    def get_blob_client(self, name):
        blob = FakeBlob(self.store, name)
        original = blob.upload_blob

        def counting_upload(*args, **kwargs):
            self.uploads += 1
            return original(*args, **kwargs)

        blob.upload_blob = counting_upload
        return blob


def test_upload_new_then_skip_same_then_update_changed(tmp_path):
    container = FakeContainer()
    f = tmp_path / "jobs.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")

    assert upload_file(f, container, "careerjet/jobs.csv") == "uploaded"
    assert upload_file(f, container, "careerjet/jobs.csv") == "skipped"      # same content: nothing sent
    assert container.uploads == 1

    f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert upload_file(f, container, "careerjet/jobs.csv") == "updated"      # changed: overwrite, no new file
    assert container.uploads == 2
    assert list(container.store) == ["careerjet/jobs.csv"]                     # still one blob
    assert bytes(container.store["careerjet/jobs.csv"]["md5"]) == file_md5(f)


def test_upload_folder_keeps_relative_paths(tmp_path, monkeypatch):
    container = FakeContainer()
    monkeypatch.setattr(azure_storage, "get_container", lambda name: container)

    (tmp_path / "sub").mkdir()
    (tmp_path / "a.csv").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "b.csv").write_text("y", encoding="utf-8")

    first = upload_folder(tmp_path, "bronze", prefix="careerjet")
    assert first == {"careerjet/a.csv": "uploaded", "careerjet/sub/b.csv": "uploaded"}

    second = upload_folder(tmp_path, "bronze", prefix="careerjet")
    assert set(second.values()) == {"skipped"}
    assert len(container.store) == 2


def test_upload_folder_pattern_and_missing_dir(tmp_path, monkeypatch):
    container = FakeContainer()
    monkeypatch.setattr(azure_storage, "get_container", lambda name: container)

    (tmp_path / "jobs.parquet").write_bytes(b"pq")
    (tmp_path / "jobs.csv").write_text("csv", encoding="utf-8")
    assert list(upload_folder(tmp_path, "silver", pattern="jobs.parquet")) == ["jobs.parquet"]
    assert upload_folder(tmp_path / "nope", "silver") == {}


def test_run_pipeline_order(monkeypatch):
    """Scrapers -> upload bronze each -> silver -> upload -> gold -> upload."""
    from src.pipelines import run_pipeline

    calls = []
    monkeypatch.setattr(run_pipeline, "run_scraper", lambda s: calls.append(f"scrape {s}") or True)
    monkeypatch.setattr(run_pipeline, "upload",
                        lambda d, container, prefix="", pattern="*", **o: calls.append(f"upload {container}/{prefix}") or True)
    monkeypatch.setattr(run_pipeline, "load_previous_silver", lambda *a, **k: None)
    monkeypatch.setattr(run_pipeline.silver_pipeline, "build_silver", lambda cfg, previous=None: "silver")
    monkeypatch.setattr(run_pipeline.silver_pipeline, "save_silver", lambda df, cfg: calls.append("silver"))
    monkeypatch.setattr(run_pipeline.gold_pipeline, "load_silver", lambda cfg: "silver")
    monkeypatch.setattr(run_pipeline.gold_pipeline, "build_gold", lambda df: {})
    monkeypatch.setattr(run_pipeline.gold_pipeline, "save_gold", lambda t, cfg: calls.append("gold"))
    monkeypatch.setattr("src.utils.azure_storage.check_access", lambda: None)
    monkeypatch.setattr("sys.argv", ["run_pipeline"])

    assert run_pipeline.main() == 0
    assert calls == [
        "scrape careerjet", "upload bronze/careerjet",
        "scrape jsearch", "upload bronze/jsearch",
        "scrape bayt", "upload bronze/bayt",
        "silver", "upload silver/",
        "gold", "upload gold/",
    ]


def test_run_pipeline_failed_scraper_continues(monkeypatch):
    from src.pipelines import run_pipeline

    monkeypatch.setattr(run_pipeline, "run_scraper", lambda s: s != "jsearch")
    monkeypatch.setattr(run_pipeline, "load_previous_silver", lambda *a, **k: None)
    monkeypatch.setattr(run_pipeline.silver_pipeline, "build_silver", lambda cfg, previous=None: "silver")
    monkeypatch.setattr(run_pipeline.silver_pipeline, "save_silver", lambda df, cfg: None)
    monkeypatch.setattr(run_pipeline.gold_pipeline, "load_silver", lambda cfg: "silver")
    monkeypatch.setattr(run_pipeline.gold_pipeline, "build_gold", lambda df: {})
    monkeypatch.setattr(run_pipeline.gold_pipeline, "save_gold", lambda t, cfg: None)
    monkeypatch.setattr("sys.argv", ["run_pipeline", "--no-upload"])

    assert run_pipeline.main() == 1   # jsearch failed -> exit code 1, but silver and gold still ran


# --- Bronze: add-only ---------------------------------------------------
def test_add_only_never_changes_existing_blob(tmp_path):
    container = FakeContainer()
    f = tmp_path / "careerjet_jobs_2026-09-21.csv"
    f.write_text("first run", encoding="utf-8")
    assert upload_file(f, container, "careerjet/" + f.name, add_only=True) == "uploaded"

    f.write_text("second run, more rows", encoding="utf-8")                    # same name, new content
    assert upload_file(f, container, "careerjet/" + f.name, add_only=True) == "skipped"
    assert container.store["careerjet/" + f.name]["data"] == b"first run"      # Azure copy untouched
    assert container.uploads == 1


def test_dated_name():
    assert dated_name("bayt_jobs.jsonl", "2026-09-21") == "bayt_jobs_2026-09-21.jsonl"
    assert dated_name("careerjet_jobs_2026-09-17.csv", "2026-09-21") == "careerjet_jobs_2026-09-17.csv"


def test_bronze_folder_add_only_with_dates(tmp_path, monkeypatch):
    container = FakeContainer()
    monkeypatch.setattr(azure_storage, "get_container", lambda name: container)
    container.store["bayt/bayt_jobs.jsonl"] = {"data": b"old undated", "md5": bytearray(16)}   # older file

    (tmp_path / "bayt_jobs.jsonl").write_text("day 1", encoding="utf-8")
    day1 = upload_folder(tmp_path, "bronze", prefix="bayt", add_only=True, date_str="2026-09-21")
    assert day1 == {"bayt/bayt_jobs_2026-09-21.jsonl": "uploaded"}

    (tmp_path / "bayt_jobs.jsonl").write_text("day 2", encoding="utf-8")
    day2 = upload_folder(tmp_path, "bronze", prefix="bayt", add_only=True, date_str="2026-09-22")
    assert day2 == {"bayt/bayt_jobs_2026-09-22.jsonl": "uploaded"}

    assert sorted(container.store) == ["bayt/bayt_jobs.jsonl", "bayt/bayt_jobs_2026-09-21.jsonl",
                                       "bayt/bayt_jobs_2026-09-22.jsonl"]   # nothing deleted or merged
    assert container.store["bayt/bayt_jobs_2026-09-21.jsonl"]["data"] == b"day 1"
