"""Trusted per-item state is populated only by actual tool execution."""

from dataclasses import dataclass, field

from .config import Budget
from .contracts import (
    MaterialSnapshot,
    Request,
    RequestItem,
    RuleCheck,
    ScheduleOptions,
    ToolResult,
)
from .storage import Repository


@dataclass
class ItemContext:
    request: Request
    item: RequestItem
    run_id: str
    repo: Repository
    budget: Budget
    trace: object
    observations: dict[str, ToolResult] = field(default_factory=dict)
    snapshot: MaterialSnapshot | None = None
    review: RuleCheck | None = None
    review_id: str | None = None
    slots: dict[str, ScheduleOptions] = field(default_factory=dict)
    consumed: set[str] = field(default_factory=set)
    save_status: str = "not_attempted"
    save_error: dict | None = None
    save_observation_id: str | None = None
    technical_error: dict | None = None
    forced_status: str | None = None
    retry_counts: dict[str, int] = field(default_factory=dict)
    protocol_feedbacks: int = 0
    observation_calls: dict[str, str] = field(default_factory=dict)
    provider_records: list[dict] = field(default_factory=list)
    draft_state: dict | None = None
    expected_revision: int = 0
    turn_revision: int = 0
    save_receipt: dict | None = None
    schedule_refresh_required: bool = False
    schedule_requeries: int = 0
    supplement_available: bool = False

    def refresh_draft(self) -> None:
        self.draft_state = self.repo.current_draft(self.request.request_id, self.material_id)
        self.expected_revision = self.draft_state["revision"] if self.draft_state else 0

    @property
    def material_id(self) -> str:
        return self.item.material_id
