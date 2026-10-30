from pathlib import Path

import pytest

from shadow_agent.contracts import Request
from shadow_agent.storage import Repository, load_catalog


@pytest.fixture
def catalog():
    return load_catalog(Path(__file__).resolve().parents[1] / "data")


@pytest.fixture
def repo(tmp_path, catalog):
    repository = Repository(tmp_path / "agent.db")
    repository.apply_catalog_snapshot(catalog)
    return repository


@pytest.fixture
def job():
    return Request.model_validate(
        {
            "request_id": "REQ-1",
            "items": [
                {
                    "material_id": "M01",
                    "publish_requirement": {
                        "requested_at": "2026-11-03T18:00:00+08:00",
                        "timezone": "Asia/Shanghai",
                        "constraint": "hard",
                        "candidate_times": ["2026-11-03T19:00:00+08:00"],
                    },
                }
            ],
        }
    )
