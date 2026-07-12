import json
from urllib.error import URLError

import open_financial_data.providers.rest as rest_module
from typer.testing import CliRunner

from open_financial_data.cli import app
from open_financial_data.config import (
    SourceCandidate,
    SourceConfig,
    SourceMatch,
    SourceRoute,
    SourceUse,
)
from open_financial_data.models import Frequency
from open_financial_data.project import Project


runner = CliRunner()


def test_automatic_fallback_starts_new_run_before_canonical_commit(tmp_path, monkeypatch) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    source = tmp_path / "fallback.csv"
    source.write_text(
        "asset_id,trade_date,open,high,low,close,volume,turnover\n"
        "CN.XSHG.600000,2024-01-02,10,11,9,10,1,10\n"
    )
    route = SourceRoute(
        id="automatic", match=SourceMatch(
            dataset="market.equity.bar", market="CN",
            frequency=Frequency.DAILY, adjustment="none",
        ),
        use=SourceUse(
            adapter="rest.equity_daily",
            options={
                "url_template": "https://example.invalid/{asset_id}",
                "assets": ["CN.XSHG.600000"],
            },
            fallback=(SourceCandidate(
                adapter="file.equity_daily", options={"path": str(source)}
            ),),
            fallback_policy="automatic",
        ),
    )
    project.config = project.config.model_copy(
        update={"sources": SourceConfig(routes=(route,))}
    )
    (project.root / "ofd.yaml").write_text(project.config.to_yaml())
    monkeypatch.setattr(
        rest_module, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("down"))
    )
    result = runner.invoke(app, [
        "bootstrap", "--project", str(project.root), "--start", "2024-01-02",
        "--end", "2024-01-02", "--assets", "CN.XSHG.600000", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["provider"] == "file"
    runs = project.state.recent_runs(10)
    assert any(item["command"] == "bootstrap" and item["status"] == "failed" for item in runs)
    assert project.state.search_audit(event="provider.fallback.triggered")
