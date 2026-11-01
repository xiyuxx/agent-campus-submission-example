"""Deterministic checks; these never choose the model's next business action."""

from .common import BusinessError, digest, utc
from .contracts import MaterialSnapshot, RequestItem, Rule, RuleCheck


def effective_rules(snapshot: MaterialSnapshot, rules: list[Rule]) -> list[Rule]:
    """Public minimum requirements precede catalog extensions; all enter audit evidence."""
    fields = ["type", "title"]
    if snapshot.material.type in ("video", "image"):
        fields.append("media_ref")
    elif snapshot.material.type == "text":
        fields.append("caption")
    return [
        Rule(
            rule_id=f"SYS-MINIMUM-{field.upper()}",
            version=1,
            field=field,
            operator="missing",
            value=None,
            severity="needs_info",
            message=f"最低可审核资料缺少 {field}",
        )
        for field in fields
    ] + rules


def review_is_current(snapshot: MaterialSnapshot, review: RuleCheck, catalog_revision: int) -> bool:
    return (
        review.catalog_revision == snapshot.catalog_revision == catalog_revision
        and review.material_sha256 == snapshot.facts_hash()
    )


def check_rules(snapshot: MaterialSnapshot, rules: list[Rule]) -> RuleCheck:
    if not rules:
        raise BusinessError("INVALID_RULES", "当前规则集不可为空")
    matches = []
    rank = {"passed": 0, "needs_review": 1, "needs_info": 2, "blocked": 3}
    status = "passed"
    rules = effective_rules(snapshot, rules)
    for rule in rules:
        value = getattr(snapshot.material, rule.field)
        matched = (
            rule.operator == "missing"
            and value is None
            or rule.operator == "equals"
            and value is not None
            and value == rule.value
            or rule.operator == "contains"
            and value is not None
            and rule.value in value
        )
        if matched:
            matches.append(
                {
                    "rule_id": rule.rule_id,
                    "version": rule.version,
                    "severity": rule.severity,
                    "field": rule.field,
                    "observed_value": value,
                    "source_id": snapshot.sources[rule.field],
                    "message": rule.message,
                }
            )
            if rank[rule.severity] > rank[status]:
                status = rule.severity
    return RuleCheck(
        catalog_revision=snapshot.catalog_revision,
        material_sha256=snapshot.facts_hash(),
        rules=rules,
        review_status=status,
        matches=matches,
    )


def confirmation_definitions(item: RequestItem, review: RuleCheck, at_utc: str) -> list[dict]:
    definitions = [
        {"id": f"risk:{x['rule_id']}", "kind": "confirm", "message": x["message"]}
        for x in review.matches
        if x["severity"] == "needs_review"
    ]
    requirement = item.publish_requirement
    if requirement.requested_at and at_utc != utc(requirement.requested_at):
        definitions.append(
            {
                "id": "change_time",
                "kind": "confirm",
                "message": f"是否接受 {at_utc} 替代原要求 {utc(requirement.requested_at)}？",
            }
        )
    return sorted(definitions, key=lambda x: x["id"])


def semantic_value(
    snapshot: MaterialSnapshot,
    review: RuleCheck,
    item: RequestItem,
    at_utc: str,
    definitions: list[dict],
) -> dict:
    return {
        "material_version": snapshot.material.material_version,
        "effective_material_sha256": snapshot.facts_hash(),
        "rules": [r.model_dump() for r in review.rules],
        "matches": review.matches,
        "original_requirement": item.publish_requirement.canonical(),
        "at_utc": at_utc,
        "confirmation_items": [{"id": x["id"], "kind": x["kind"]} for x in definitions],
    }


def semantic_hash(
    snapshot: MaterialSnapshot,
    review: RuleCheck,
    item: RequestItem,
    at_utc: str,
    definitions: list[dict],
) -> str:
    return digest(semantic_value(snapshot, review, item, at_utc, definitions))
