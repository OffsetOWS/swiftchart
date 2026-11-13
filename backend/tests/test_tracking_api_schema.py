import pytest
from pydantic import TypeAdapter

from app.models.schemas import TradeHistoryResult, TradeHistoryStatus


@pytest.mark.parametrize("status", ["UNRESOLVED", "EXPIRED_POSITION", "EXPIRED_RUNNER"])
def test_evidence_tracker_statuses_are_valid_api_values(status):
    assert TypeAdapter(TradeHistoryStatus).validate_python(status) == status


def test_unresolved_result_is_a_valid_api_value():
    assert TypeAdapter(TradeHistoryResult).validate_python("UNRESOLVED") == "UNRESOLVED"
