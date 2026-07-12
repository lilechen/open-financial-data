from open_financial_data.project import Project


def test_open_marks_old_unlocked_run_and_task_interrupted(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    run_id = "run_orphan"
    project.state.create_run(
        run_id=run_id, correlation_id="corr", command="update",
        started_at="2020-01-01T00:00:00.000Z",
    )
    project.state.create_tasks([{
        "task_id": "task_orphan", "run_id": run_id, "asset_id": "CN.XSHG.600000",
        "requested_start": "2024-01-01", "requested_end": "2024-01-02",
    }])
    project.state.update_task(task_id="task_orphan", status="running", attempts=1)
    reopened = Project.open(project.root)
    assert reopened.state.run_context(run_id)["status"] == "interrupted"
    assert reopened.state.task_summary(run_id) == {"interrupted": 1}
    assert reopened.state.latest_recoverable_run() == run_id
