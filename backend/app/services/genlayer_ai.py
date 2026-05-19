from __future__ import annotations

import json
from datetime import UTC, datetime

from app.models.schemas import (
    GenLayerSignalPayload,
    GenLayerValidationResult,
    GenLayerValidatorVote,
    TradeIdea,
)
from app.utils.database import get_connection


def trade_idea_to_genlayer_signal(idea: TradeIdea) -> GenLayerSignalPayload:
    entry_low, entry_high = idea.entry_zone
    entry = (float(entry_low) + float(entry_high)) / 2
    return GenLayerSignalPayload(
        symbol=idea.symbol.upper(),
        side="BUY" if idea.direction == "Long" else "SELL",
        timeframe=idea.timeframe,
        entry=entry,
        entry_zone=(float(entry_low), float(entry_high)),
        stop_loss=float(idea.stop_loss),
        take_profits=[float(idea.take_profit_1), float(idea.take_profit_2)],
        risk_to_reward=float(idea.risk_reward_ratio),
        setup_score=idea.setup_score or idea.confidence_score,
        market_regime=idea.regime_label or idea.market_regime,
        htf_bias=idea.higher_timeframe_bias,
        volatility_info={
            "move_maturity": idea.move_maturity,
            "exhaustion_risk": idea.exhaustion_risk,
            "entry_status": idea.entry_status,
            "regime_confidence_score": idea.regime_confidence_score,
            "regime_confidence_adjustment": idea.regime_confidence_adjustment,
        },
        reason=idea.reason,
        invalidation_condition=idea.invalid_condition,
        source=idea.source or idea.exchange,
        exchange=idea.exchange,
    )


def _alignment_supports_signal(signal: GenLayerSignalPayload) -> bool:
    if signal.side == "BUY":
        return signal.htf_bias in {"HTF_BULLISH", "HTF_NEUTRAL"}
    return signal.htf_bias in {"HTF_BEARISH", "HTF_NEUTRAL"}


def _warning_flags(signal: GenLayerSignalPayload) -> list[str]:
    flags: list[str] = []
    volatility = signal.volatility_info
    if volatility.get("entry_status") == "WAIT_FOR_RETEST":
        flags.append("Wait for retest before entry.")
    if volatility.get("exhaustion_risk") == "High":
        flags.append("Move exhaustion risk is high.")
    if signal.risk_to_reward < 2:
        flags.append("Risk-to-reward is below preferred threshold.")
    if not _alignment_supports_signal(signal):
        flags.append("Higher-timeframe bias conflicts with signal direction.")
    return flags


def mock_validate_signal(idea: TradeIdea) -> GenLayerValidationResult:
    signal = trade_idea_to_genlayer_signal(idea)
    score = float(signal.setup_score or 0)
    flags = _warning_flags(signal)
    entry_status = signal.volatility_info.get("entry_status")
    exhaustion_risk = signal.volatility_info.get("exhaustion_risk")
    aligned = _alignment_supports_signal(signal)

    if entry_status == "REJECTED_EXHAUSTED" or (exhaustion_risk == "High" and score < 78):
        decision = "REJECT"
        risk_level = "High"
        size = 0
    elif entry_status == "WAIT_FOR_RETEST":
        decision = "WAIT"
        risk_level = "Medium"
        size = 0
    elif score >= 78 and aligned and signal.risk_to_reward >= 2 and exhaustion_risk != "High":
        decision = "APPROVE"
        risk_level = "Medium" if flags else "Low"
        size = 0.75 if risk_level == "Low" else 0.5
    elif score >= 70 and signal.risk_to_reward >= 1.5:
        decision = "REDUCE_SIZE"
        risk_level = "Medium" if exhaustion_risk != "High" else "High"
        size = 0.5 if risk_level == "Medium" else 0.25
    else:
        decision = "WAIT"
        risk_level = "Medium"
        size = 0

    confidence = min(94.0, max(42.0, score - len(flags) * 6 + (8 if aligned else -8)))
    votes = _validator_votes(decision, confidence, flags, aligned)
    reasoning = _reasoning(signal, decision, risk_level, flags, aligned)
    paper_status = "PAPER_EXECUTED" if decision in {"APPROVE", "REDUCE_SIZE"} else "NOT_EXECUTED"
    return GenLayerValidationResult(
        signal=signal,
        decision=decision,
        confidence_score=round(confidence, 1),
        risk_level=risk_level,
        validator_votes=votes,
        reasoning=reasoning,
        recommended_position_size=size,
        warning_flags=flags,
        paper_execution_status=paper_status,
    )


def _validator_votes(decision: str, confidence: float, flags: list[str], aligned: bool) -> list[GenLayerValidatorVote]:
    primary_vote = "approve" if decision == "APPROVE" else "cautious" if decision == "REDUCE_SIZE" else "wait" if decision == "WAIT" else "reject"
    risk_vote = "cautious" if flags else "approve"
    structure_vote = "approve" if aligned and decision in {"APPROVE", "REDUCE_SIZE"} else primary_vote
    return [
        GenLayerValidatorVote(
            validator="structure-validator",
            vote=structure_vote,
            confidence=round(max(40.0, min(95.0, confidence + (4 if aligned else -6))), 1),
            reason="Checks market structure, direction, and HTF alignment.",
        ),
        GenLayerValidatorVote(
            validator="risk-validator",
            vote=risk_vote,
            confidence=round(max(40.0, min(95.0, confidence - len(flags) * 3)), 1),
            reason="Reviews stop distance, risk-to-reward, and warning flags.",
        ),
        GenLayerValidatorVote(
            validator="execution-validator",
            vote=primary_vote,
            confidence=round(max(40.0, min(95.0, confidence - 2)), 1),
            reason="Evaluates whether the setup should be acted on now or delayed.",
        ),
    ]


def _reasoning(signal: GenLayerSignalPayload, decision: str, risk_level: str, flags: list[str], aligned: bool) -> str:
    alignment_text = "aligned with HTF bias" if aligned else "not aligned with HTF bias"
    if decision == "APPROVE":
        return f"Setup is {alignment_text}, score is strong, and risk/reward is acceptable. Risk level is {risk_level}."
    if decision == "REDUCE_SIZE":
        return f"Setup has enough quality for paper execution, but position size is reduced because {', '.join(flags) if flags else 'risk is not ideal'}."
    if decision == "REJECT":
        return f"Consensus rejects this setup because {', '.join(flags) if flags else 'risk controls do not support entry'}."
    return f"Consensus prefers waiting because {', '.join(flags) if flags else 'confirmation is not strong enough yet'}."


def save_validation_result(result: GenLayerValidationResult) -> GenLayerValidationResult:
    payload = result.model_dump(mode="json")
    signal = payload["signal"]
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO genlayer_ai_scans (
                symbol, timeframe, exchange, direction, source, signal_json,
                decision, confidence, risk_level, validator_reasoning,
                validator_votes_json, recommended_position_size, warning_flags_json,
                paper_execution_status, final_trade_outcome
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal["symbol"],
                signal["timeframe"],
                signal["exchange"],
                "Long" if signal["side"] == "BUY" else "Short",
                signal["source"],
                json.dumps(signal),
                result.decision,
                result.confidence_score,
                result.risk_level,
                result.reasoning,
                json.dumps(payload["validator_votes"]),
                result.recommended_position_size,
                json.dumps(result.warning_flags),
                result.paper_execution_status,
                result.final_trade_outcome,
            ),
        )
        row = connection.execute("SELECT * FROM genlayer_ai_scans WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_validation_result(row)


def row_to_validation_result(row) -> GenLayerValidationResult:
    data = dict(row)
    created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return GenLayerValidationResult(
        id=int(data["id"]),
        signal=GenLayerSignalPayload(**json.loads(data["signal_json"])),
        decision=data["decision"],
        confidence_score=float(data["confidence"]),
        risk_level=data["risk_level"],
        validator_votes=[GenLayerValidatorVote(**vote) for vote in json.loads(data["validator_votes_json"])],
        reasoning=data["validator_reasoning"],
        recommended_position_size=float(data["recommended_position_size"]),
        warning_flags=json.loads(data["warning_flags_json"]),
        paper_execution_status=data["paper_execution_status"],
        final_trade_outcome=data["final_trade_outcome"],
        created_at=created_at,
    )


def validate_and_store_signal(idea: TradeIdea) -> GenLayerValidationResult:
    result = mock_validate_signal(idea)
    return save_validation_result(result)


def list_validation_history(limit: int = 50) -> list[GenLayerValidationResult]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM genlayer_ai_scans ORDER BY created_at DESC, id DESC LIMIT ?",
            (min(max(1, limit), 250),),
        ).fetchall()
    return [row_to_validation_result(row) for row in rows]
