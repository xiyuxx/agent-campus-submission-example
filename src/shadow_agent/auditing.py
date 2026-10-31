"""Local metadata audit boundary. Remote jobs/reports are not implemented here."""

from typing import Protocol

from .contracts import MaterialSnapshot, Rule, RuleCheck
from .policy import check_rules


class Auditor(Protocol):
    def evaluate(self, snapshot: MaterialSnapshot, rules: list[Rule]) -> RuleCheck: ...


class LocalMetadataAuditor:
    def evaluate(self, snapshot: MaterialSnapshot, rules: list[Rule]) -> RuleCheck:
        return check_rules(snapshot, rules)
