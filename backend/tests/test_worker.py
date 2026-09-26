from datetime import datetime

from app.config import get_settings
from app.worker import build_scheduler
from pipeline.sg import SGT
from scrapers.registry import ADAPTERS


def test_every_source_and_the_freshness_jobs_are_scheduled_in_sgt():
    scheduler = build_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}
    autosearch = {"autosearch"} if get_settings().tavily_api_key else set()  # only with a Tavily key
    assert set(jobs) == {f"crawl:{s}" for s in ADAPTERS} | {"expire", "recheck"} | autosearch

    now = datetime(2026, 9, 26, 7, 30, tzinfo=SGT)
    luma = jobs["crawl:luma"].trigger.get_next_fire_time(None, now)
    assert luma == datetime(2026, 9, 26, 12, 0, tzinfo=SGT)  # every 6 h, on SGT hours
    expire = jobs["expire"].trigger.get_next_fire_time(None, now)
    assert expire == datetime(2026, 9, 27, 3, 0, tzinfo=SGT)


def test_jobs_never_overlap_and_missed_runs_collapse():
    scheduler = build_scheduler()
    assert scheduler._job_defaults["max_instances"] == 1
    assert scheduler._job_defaults["coalesce"] is True
