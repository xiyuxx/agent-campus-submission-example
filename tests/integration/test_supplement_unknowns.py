"""Unknown supplement values neither delete nor contradict known business facts."""

import json

import pytest
from test_agent import context, run

from shadow_agent.contracts import Catalog
from shadow_agent.tools.registry import execute


@pytest.mark.parametrize("known_base", [False, True])
@pytest.mark.parametrize("unknown", ["missing", None, "", " \t"])
def test_unknown_supplement_preserves_base_and_allows_real_new_facts(
    repo, catalog, known_base, unknown
):
    value = catalog.model_dump(mode="json")
    if not known_base:
        value["materials"][0]["title"] = None
    fields = value["supplements"][0]["fields"]
    if unknown != "missing":
        fields["title"] = unknown
    repo.apply_catalog_snapshot(Catalog.model_validate(value), update=True)
    basic = repo.read_material_basic("M01")
    merged = repo.read_supplement(basic)
    assert merged.material.title == basic.material.title
    assert merged.sources["title"] == basic.sources["title"]
    assert merged.material.copyright_status == "authorized"
    assert merged.sources["copyright_status"].startswith("supplements:")
    assert basic.material.copyright_status is None
    with repo.transaction() as connection:
        stored = json.loads(
            connection.execute(
                "SELECT fields_json FROM supplements WHERE material_id=?", ("M01",)
            ).fetchone()[0]
        )
    if unknown == "missing":
        assert "title" not in stored
    else:
        assert "title" in stored and stored["title"] is None


def test_equal_known_fact_keeps_original_source(repo, catalog):
    changed = catalog.model_copy(deep=True)
    changed.supplements[0].fields["title"] = changed.materials[0].title
    repo.apply_catalog_snapshot(changed, update=True)
    basic = repo.read_material_basic("M01")
    merged = repo.read_supplement(basic)
    assert merged.material.title == basic.material.title
    assert merged.sources["title"] == basic.sources["title"]
    assert merged.material.copyright_status == "authorized"


def test_new_known_fact_fills_unknown_with_supplement_source(repo, catalog):
    changed = catalog.model_copy(deep=True)
    changed.materials[0].title = None
    changed.supplements[0].fields["title"] = "补充标题"
    repo.apply_catalog_snapshot(changed, update=True)
    basic = repo.read_material_basic("M01")
    merged = repo.read_supplement(basic)
    assert basic.material.title is None
    assert merged.material.title == "补充标题"
    assert merged.sources["title"].startswith("supplements:")


def test_real_conflict_rejects_entire_merge_without_mutating_context(repo, catalog, job):
    changed = catalog.model_copy(deep=True)
    changed.supplements[0].fields.update({"title": "与基本事实冲突", "cover_ref": None})
    repo.apply_catalog_snapshot(changed, update=True)
    ctx = context(repo, job)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    before = ctx.snapshot.model_dump()
    failed = execute("read_material", {"detail": "supplement"}, ctx)
    assert failed.code == "DATA_CONFLICT"
    assert ctx.snapshot.model_dump() == before
    assert ctx.snapshot.material.copyright_status is None
    assert repo.read_material_basic("M01").model_dump() == before


def test_nullable_export_can_complete_and_replay_one_draft(repo, catalog, job):
    changed = catalog.model_copy(deep=True)
    changed.supplements[0].fields["title"] = None
    repo.apply_catalog_snapshot(changed, update=True)
    _, first = run(repo, job)
    _, repeated = run(repo, job)
    assert first.review_status == repeated.review_status == "passed"
    assert first.draft.save_status == "saved"
    assert repeated.draft.save_status == "reused"
    assert first.draft.id == repeated.draft.id
    assert first.draft.revision == repeated.draft.revision == 1
    assert repeated.task_status == "needs_input"
    assert repeated.confirmation_status == "pending"
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM draft_history").fetchone()[0] == 1
