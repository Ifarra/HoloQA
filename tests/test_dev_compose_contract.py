from pathlib import Path


def test_dev_compose_contract():
    compose = Path("docker-compose.dev.yml").read_text()

    assert ".:/app" in compose
    assert "--reload" in compose
    assert "uv sync --dev" in compose
    assert "holoqa-dev-venv" in compose
    assert "dockerfile: Dockerfile.dev" in compose
