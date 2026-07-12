from open_financial_data.observability import JsonlEventLogger


def test_jsonl_logger_rotates_before_appending_next_event(tmp_path) -> None:
    path = tmp_path / "ofd.jsonl"
    logger = JsonlEventLogger(path, rotate_size_bytes=1024)
    path.write_bytes(b"x" * 1024)
    logger.emit("test.event", correlation_id="corr", run_id="run")
    rotated = list(tmp_path.glob("ofd.jsonl.*"))
    assert len(rotated) == 1
    assert '"event":"test.event"' in path.read_text()
