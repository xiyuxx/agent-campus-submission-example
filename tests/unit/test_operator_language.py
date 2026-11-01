from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from shadow_agent.contracts import PublishRequirement, parse_time
from shadow_agent.operator_language import display_time, humanize, parse_operator_time


def test_local_input_and_roundtrip_preserve_the_users_wall_time():
    value = parse_operator_time("2026-11-03 22:00")
    assert parse_time(value).astimezone().replace(tzinfo=None) == datetime(2026, 11, 3, 22)
    requirement = PublishRequirement.model_validate(
        {"requested_at": value, "timezone": "local", "constraint": "hard"}
    )
    assert requirement.canonical()["requested_at"].endswith("Z")


def test_explicit_offset_remains_exact_and_display_changes_date_in_local_zone():
    value = "2026-11-03T23:30:00Z"
    parsed = parse_operator_time(value)
    assert parse_time(parsed).astimezone(UTC) == datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
    assert "2026-11-04 07:30+08:00" in display_time(value, zone=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize("at", ["2026-03-08 02:30", "2026-11-01 01:30"])
def test_nonexistent_and_ambiguous_dst_times_require_explicit_offset(at):
    with pytest.raises(ValueError, match="夏令时"):
        parse_operator_time(at, zone=ZoneInfo("America/New_York"))


def test_offset_resolves_dst_ambiguity_and_date_without_time_is_rejected():
    assert parse_operator_time("2026-11-01T01:30:00-04:00").endswith("-04:00")
    with pytest.raises(ValueError, match="日期和时间"):
        parse_operator_time("2026-11-03")
    assert humanize("最低可审核资料缺少media_ref") == "最低可审核资料缺少素材文件或链接"
