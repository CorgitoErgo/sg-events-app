import pytest

from pipeline.monitor import RunStats, evaluate


def run(**kwargs) -> RunStats:
    defaults = dict(source_id="luma", status="ok", urls_fetched=12, urls_failed=0, events_found=60, normalize_errors=0)
    return RunStats(**{**defaults, **kwargs})


def test_healthy_run_has_no_alerts():
    assert evaluate(run(), [58, 64, 61, 60]) == []


def test_drop_of_more_than_60_percent_versus_the_7_day_median():
    alerts = evaluate(run(events_found=10), [58, 64, 61, 60])
    assert len(alerts) == 1 and "83% below its 7-day median of 60.5" in alerts[0]
    assert evaluate(run(events_found=30), [58, 64, 61, 60]) == []  # a 50% dip is not breakage


@pytest.mark.parametrize(
    ("history", "found"),
    [([60, 60], 0), ([3, 4, 2, 3], 0)],  # too little history; tiny source where noise dominates
)
def test_no_drop_alert_without_meaningful_history(history, found):
    assert evaluate(run(events_found=found), history) == []


def test_parse_and_fetch_error_rates():
    assert "2 of 12 pages failed" in evaluate(run(urls_fetched=10, urls_failed=2), [])[0]
    assert "7 of 60 events couldn't be normalized" in evaluate(run(events_found=53, normalize_errors=7), [])[0]
    assert evaluate(run(urls_fetched=11, urls_failed=1), []) == []  # 8%: under the 10% line


def test_blocked_and_failed_runs_always_alert():
    assert "blocked (HTTP 403)" in evaluate(run(status="blocked", error="HTTP 403"), [60, 60, 60])[0]
    assert "run failed: boom" in evaluate(run(status="failed", error="boom"), [])[0]
