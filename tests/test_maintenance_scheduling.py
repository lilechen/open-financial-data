import plistlib
from pathlib import Path

from open_financial_data.maintenance import clean_temporary_data
from open_financial_data.project import Project
from open_financial_data.scheduling import cron_entry, launchd_plist, systemd_units


def test_cleanup_is_dry_run_by_default_and_never_targets_canonical(tmp_path: Path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    artifact = project.root / ".ofd" / "tmp" / "abandoned" / "part.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    canonical = project.root / "data" / "canonical" / "keep.bin"
    canonical.write_bytes(b"keep")
    planned = clean_temporary_data(project)
    assert planned["bytes_reclaimable"] == 4
    assert artifact.exists()
    applied = clean_temporary_data(project, apply=True)
    assert applied["removed_count"] == 1
    assert canonical.exists()


def test_scheduler_definitions_only_invoke_stable_update_cli(tmp_path: Path) -> None:
    cron = cron_entry(tmp_path, hour=18, minute=5)
    launchd = launchd_plist(tmp_path, hour=18, minute=5)
    systemd = systemd_units(tmp_path, hour=18, minute=5)
    for definition in (cron, launchd, systemd):
        assert "open_financial_data.cli" in definition
        assert "update" in definition
        assert "non-interactive" in definition
    assert "Persistent=true" in systemd
    launchd_config = plistlib.loads(launchd.encode())
    intervals = launchd_config["StartCalendarInterval"]
    assert [interval["Weekday"] for interval in intervals] == [1, 2, 3, 4, 5]
    assert all(interval["Hour"] == 18 and interval["Minute"] == 5 for interval in intervals)
