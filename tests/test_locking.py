from pathlib import Path

import pytest

from open_financial_data.locking import ProjectLock, ProjectLockedError


def test_project_lock_excludes_concurrent_writer_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "write.lock"
    with ProjectLock(path, run_id="run_1", stale_after_seconds=60):
        with pytest.raises(ProjectLockedError):
            with ProjectLock(path, run_id="run_2", stale_after_seconds=60):
                pass
    with ProjectLock(path, run_id="run_3", stale_after_seconds=60):
        assert path.exists()
