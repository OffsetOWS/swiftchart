import json, sqlite3, zlib
from copy import deepcopy
from datetime import UTC, datetime
import pytest
from app.config import get_settings
from app.models.schemas import Zone, RiskSettings
from app.services import score_archive as archive
from app.services.score_rebuild import load, rebuild, coverage
from app.services.trade_history import save_trade_ideas
from app.services import telegram_dispatch as dispatch
from app.strategy.market_regime import detect_market_regime
from app.strategy.trade_ideas import _build_idea
from app.utils import database
from test_market_regime import candles_from_prices
from test_canonical_telegram_dispatch import configure_db, idea


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setenv("CRYPTO_SCORE_ARCHIVE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    database._INITIALIZED = False


def evaluated(*, strength=0, rr_target=130):
    df = candles_from_prices([100 + (n % 8) * 0.1 for n in range(120)])
    df.attrs.update(
        requested_at="2026-01-06T00:00:00+00:00",
        fetched_at="2026-01-06T00:00:01+00:00",
        timeframe="1h",
        source_snapshot={"raw": [{"c": "100.00000000000001"}]},
    )
    regime = detect_market_regime(df)
    from app.services.score_archive import capture_context

    with capture_context(
        "BTCUSDT",
        "1h",
        "hyperliquid",
        df,
        RiskSettings(),
        [df],
        -12.123456789,
        42.123456789,
    ):
        candidate, review = _build_idea(
            symbol="BTCUSDT",
            timeframe="1h",
            exchange="hyperliquid",
            direction="Long",
            df=df,
            regime="RANGE_BOUND",
            htf_bias="HTF_NEUTRAL",
            entry_low=100,
            entry_high=102,
            stop=95,
            tp1=110,
            tp2=rr_target,
            zone=Zone(
                type="support",
                lower=99,
                upper=102,
                strength=0.95,
                touches=4,
                strength_score=strength,
            ),
            sweep=None,
            settings=RiskSettings(),
            vol_ok=True,
            mom_ok=True,
            distance_from_mid=0.123456789,
            market_regime_data=regime,
            support=None,
            resistance=None,
            bullish_sweep=None,
            bearish_sweep=None,
        )
    artifact = (candidate or review).score_evaluation
    return artifact, df


@pytest.mark.parametrize(
    "strength,target", [(0, 130), (95, 118.999999999), (100, 119.000000001), (50, 101)]
)
def test_all_components_adjustments_and_rejections_rebuild_exactly_offline(
    enabled, monkeypatch, strength, target
):
    artifact, df = evaluated(strength=strength, rr_target=target)
    assert artifact is not None
    expected = rebuild(artifact)
    assert expected["state"] in {"EXACT", "NOT_COMPUTED"}, expected
    assert artifact["inputs"]["distance_from_mid"] == 0.123456789
    assert (
        artifact["market_snapshots"][0]["source"]["raw"][0]["c"] == "100.00000000000001"
    )
    # Future provider data and settings cannot alter an archived decision.
    df.loc[:, "close"] = 999999
    monkeypatch.setenv("DEFAULT_MIN_RR", "999")
    get_settings.cache_clear()
    assert rebuild(artifact) == expected
    with database.get_connection() as c:
        identifier = archive.persist(c, artifact)
        archive.persist(c, artifact)
        saved = load(c, identifier)
        assert rebuild(saved) == expected
        assert c.execute("SELECT COUNT(*) FROM score_evaluations").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM score_evaluations")
        changed = deepcopy(artifact)
        changed["inputs"]["distance_from_mid"] = 0.9
        with pytest.raises(ValueError):
            archive.persist(c, changed)


def test_original_evaluation_and_alert_are_pinned_after_rescan(enabled):
    artifact, df = evaluated()
    candidate = idea()
    candidate.score_evaluation = artifact
    candidate.market_volume_24h = 500000
    identifier = save_trade_ideas([candidate])[0]
    first = dispatch.claim_telegram_dispatches([101])[0]
    dispatch.mark_telegram_recipient_success(
        first.dispatch_id, 101, telegram_message_id=9
    )
    later = idea(entry_zone=(103, 105))
    later.score_evaluation = evaluated(strength=95)[0]
    assert save_trade_ideas([later]) == [identifier]
    with database.get_connection() as c:
        row = dict(
            c.execute("SELECT * FROM trade_ideas WHERE id=?", (identifier,)).fetchone()
        )
        assert row["entry_zone_low"] == 100
        assert (
            row["initial_score_evaluation_id"]
            == artifact["evaluation_id"]
            == row["score_evaluation_id"]
        )
        assert (
            c.execute("SELECT score_evaluation_id FROM telegram_dispatches").fetchone()[
                0
            ]
            == artifact["evaluation_id"]
        )
        assert (
            c.execute("SELECT COUNT(*) FROM score_opportunity_evaluations").fetchone()[
                0
            ]
            == 2
        )
        assert coverage(c)["archived"] == 1
    assert "score_evaluation" not in candidate.model_dump()


def test_archive_failure_rolls_back_opportunity_and_no_alert_is_publishable(enabled):
    candidate = idea()
    candidate.score_evaluation = evaluated()[0]
    with database.get_connection() as c:
        c.execute(
            "CREATE TRIGGER reject_archive BEFORE INSERT ON score_evaluations BEGIN SELECT RAISE(ABORT,'disk simulated'); END"
        )
    assert save_trade_ideas([candidate]) == []
    assert dispatch.claim_telegram_dispatches([101]) == []
    with database.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM trade_ideas").fetchone()[0] == 0


def test_strict_new_capture_never_fabricates_legacy_points(enabled):
    assert save_trade_ideas([idea()]) == []


def test_volume_ranking_is_independent_of_score(monkeypatch):
    from app.services.signal_limits import cap_signals_per_scan

    monkeypatch.setenv("CRYPTO_MAX_SIGNALS_PER_SCAN", "1")
    get_settings.cache_clear()
    high_score = idea()
    high_score.setup_score = 99
    high_score.market_volume_24h = 500000
    high_volume = idea(symbol="SOLUSDT")
    high_volume.setup_score = 65
    high_volume.market_volume_24h = 1000000
    assert cap_signals_per_scan([high_score, high_volume]) == [high_volume]
    assert high_volume.setup_score == 65 and high_score.setup_score == 99
    get_settings.cache_clear()
