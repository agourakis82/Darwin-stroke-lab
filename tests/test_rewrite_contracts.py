from pathlib import Path

from sounio_stroke_lab.rewrite_contracts import build_contract_snapshot


def test_build_contract_snapshot_writes_openapi_schemas_and_golden_fixtures(tmp_path: Path):
    written = build_contract_snapshot(tmp_path / "contracts")

    assert Path(written["openapi"]).exists()
    assert Path(written["comparison_registry"]).exists()
    assert Path(written["snapshot_manifest"]).exists()

    for key in (
        "golden:JobRecord",
        "golden:ArtifactRef",
        "golden:BenchmarkRun",
        "golden:CampaignRecord",
        "golden:ProgramRecord",
        "golden:PortfolioReport",
        "golden:ProgramPortfolioReport",
        "golden:AgentRun",
        "schema:JobRecord",
        "schema:BenchmarkRun",
        "schema:CampaignRecord",
        "schema:ProgramRecord",
        "schema:PortfolioReport",
        "schema:ProgramPortfolioReport",
        "schema:AgentRun",
    ):
        assert key in written
        assert Path(written[key]).exists()
