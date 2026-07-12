from datetime import datetime
import json
from zoneinfo import ZoneInfo

from open_financial_data.calendar import expected_cn_watermark, expected_project_watermark
from open_financial_data.importers.canonical_jsonl import import_canonical_jsonl
from open_financial_data.project import Project

TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_expected_watermark_skips_weekend_and_pre_close_day() -> None:
    assert expected_cn_watermark(datetime(2026, 7, 11, 20, tzinfo=TIMEZONE)).isoformat() == "2026-07-10"
    assert expected_cn_watermark(datetime(2026, 7, 13, 10, tzinfo=TIMEZONE)).isoformat() == "2026-07-10"
    assert expected_cn_watermark(datetime(2026, 7, 13, 20, tzinfo=TIMEZONE)).isoformat() == "2026-07-13"


def test_project_calendar_overrides_weekday_fallback(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    source = tmp_path / "calendar.jsonl"
    records = [
        {"calendar_id": "XSHG", "market": "CN", "exchange": "XSHG",
         "trade_date": "2026-07-09", "is_open": True,
         "previous_open_date": None, "next_open_date": None},
        {"calendar_id": "XSHG", "market": "CN", "exchange": "XSHG",
         "trade_date": "2026-07-10", "is_open": False,
         "previous_open_date": "2026-07-09", "next_open_date": None},
    ]
    source.write_text("\n".join(json.dumps(item) for item in records) + "\n")
    _, run_id = project.begin_operation(command="import.canonical-jsonl")
    import_canonical_jsonl(
        project, source=source, dataset_name="reference.calendar.trading",
        schema_version="1.0.0", provider="test", run_id=run_id,
    )
    actual = expected_project_watermark(
        project, market="CN", now=datetime(2026, 7, 11, 20, tzinfo=TIMEZONE)
    )
    assert actual.isoformat() == "2026-07-09"
