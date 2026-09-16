from pathlib import Path

from holoqa.project_store import ProjectStore


def test_initialize_records_commit_bound_codegraph_snapshot(tmp_path: Path):
    artifact = tmp_path / ".codebase-memory" / "graph.db.zst"
    artifact.parent.mkdir()
    artifact.write_bytes(b"graph-artifact")

    result = ProjectStore(tmp_path / "state.db").initialize(tmp_path)

    assert result.codegraph_status == "available"
    assert result.codegraph_artifact == str(artifact)
    assert result.codegraph_sha256
    assert result.commit.startswith("uncommitted-")
