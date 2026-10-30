from copy import deepcopy

import pytest
from pydantic import ValidationError

from shadow_agent.common import digest
from shadow_agent.contracts import (
    Catalog,
    Confirmation,
    Material,
    PublishRequirement,
    Request,
    Rule,
)


def test_canonical_default_empty_and_order(job):
    original = job.model_dump(mode="json")
    without = deepcopy(original)
    without["items"][0]["publish_requirement"].pop("candidate_times")
    null = deepcopy(without)
    null["items"][0]["publish_requirement"]["candidate_times"] = None
    empty = deepcopy(null)
    empty["items"][0]["publish_requirement"]["candidate_times"] = []
    assert Request.model_validate(without).canonical() == Request.model_validate(null).canonical()
    assert Request.model_validate(empty).canonical() != Request.model_validate(null).canonical()
    equivalent = deepcopy(original)
    equivalent["items"][0]["publish_requirement"]["candidate_times"] *= 2
    assert digest(Request.model_validate(equivalent).canonical()) == digest(job.canonical())


@pytest.mark.parametrize(
    "patch",
    [
        {"requested_at": "2026-11-03T18:00:00"},
        {"requested_at": "2026-11-03T18:00:00.0000001+08:00"},
        {"requested_at": "2026-11-03T18:00:00+00:00", "timezone": "Asia/Shanghai"},
        {"timezone": "Not/AZone"},
        {"constraint": "flexible"},
        {"unknown": 1},
    ],
)
def test_invalid_requirement(patch):
    with pytest.raises(ValidationError):
        PublishRequirement.model_validate(patch)


def test_unknown_business_fields_allowed_but_identity_is_not():
    item = Material(material_id="M", material_version=1, platform="p", account_id="a", caption=" ")
    assert item.caption is None
    for patch in (
        {"material_id": " "},
        {"copyright_status": "unknown"},
        {"material_version": True},
    ):
        with pytest.raises(ValidationError):
            Material.model_validate({**item.model_dump(), **patch})


@pytest.mark.parametrize(
    "patch",
    [
        {"operator": "eval"},
        {"field": "account_id"},
        {"value": "x"},
        {"operator": "equals", "field": "copyright_status", "value": "maybe"},
        {"operator": "contains", "field": "copyright_status", "value": "authorized"},
    ],
)
def test_rule_whitelist(patch):
    with pytest.raises(ValidationError):
        Rule.model_validate(
            {
                "rule_id": "R",
                "version": 1,
                "field": "caption",
                "operator": "missing",
                "value": None,
                "severity": "needs_info",
                "message": "缺项",
                **patch,
            }
        )


def test_catalog_empty_rules_and_supplement_identity_rejected(catalog):
    for patch in (
        {"rules": []},
        {
            "supplements": [
                {
                    "material_id": "M01",
                    "supplement_version": 1,
                    "source_id": "S",
                    "fields": {"platform": "other"},
                }
            ]
        },
    ):
        with pytest.raises(ValidationError):
            Catalog.model_validate({**catalog.model_dump(mode="json"), **patch})


def test_confirmation_rejects_duplicate_and_mixed():
    for decisions in (
        [{"item_id": "x", "decision": "accept"}] * 2,
        [{"item_id": "x", "decision": "accept"}, {"item_id": "y", "decision": "reject"}],
    ):
        with pytest.raises(ValidationError):
            Confirmation(operation_id="O", draft_id="D", expected_revision=1, decisions=decisions)
