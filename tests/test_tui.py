from rich.console import Console

from open_financial_data.project import Project
from open_financial_data.tui import render_dashboard


def test_dashboard_renders_project_state_without_mutating_it(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    before = project.state.summary()
    console = Console(record=True, width=120)
    console.print(render_dashboard(project))
    output = console.export_text()
    assert "OpenFinancialData" in output
    assert "Recent runs" in output
    assert project.state.summary() == before
