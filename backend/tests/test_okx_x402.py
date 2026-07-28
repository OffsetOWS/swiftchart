from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.integrations.okx_asp.auth import reset_okx_asp_rate_limit_for_tests
from app.integrations.okx_asp.payments import (
    PUBLIC_ANALYSIS_PATH,
    install_okx_asp_access_logging,
    install_okx_x402,
)


PAY_TO = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
RESOURCE = f"https://swiftchart.vercel.app{PUBLIC_ANALYSIS_PATH}"


class FakeFacilitator:
    def __init__(self):
        self.verify_calls = 0
        self.settle_calls = 0

    def get_supported(self):
        from x402.schemas import SupportedKind, SupportedResponse

        return SupportedResponse(
            kinds=[
                SupportedKind(
                    x402_version=2,
                    scheme="exact",
                    network="eip155:196",
                )
            ],
            extensions=[],
            signers={},
        )

    async def verify(self, _payload, _requirements):
        from x402.schemas import VerifyResponse

        self.verify_calls += 1
        return VerifyResponse(is_valid=True, payer=PAYER)

    async def settle(self, _payload, _requirements):
        from x402.schemas import SettleResponse

        self.settle_calls += 1
        return SettleResponse(
            success=True,
            payer=PAYER,
            transaction="0x" + "3" * 64,
            network="eip155:196",
        )


def _settings(**overrides) -> Settings:
    values = {
        "okx_x402_enabled": True,
        "okx_x402_api_key": "test-api-key",
        "okx_x402_secret_key": "test-secret",
        "okx_x402_passphrase": "test-passphrase",
        "okx_x402_pay_to_address": PAY_TO,
        "okx_x402_price_usd": "0.01",
        "okx_x402_network": "eip155:196",
        "okx_x402_resource_url": RESOURCE,
    }
    values.update(overrides)
    return Settings(**values)


def _app(facilitator: FakeFacilitator) -> FastAPI:
    app = FastAPI()

    @app.get(PUBLIC_ANALYSIS_PATH)
    async def paid_discovery_analysis():
        return {"status": "NO_TRADE", "symbol": "BTC", "timeframe": "4h"}

    @app.post(PUBLIC_ANALYSIS_PATH)
    async def paid_analysis():
        return {"status": "NO_TRADE", "symbol": "BTC", "timeframe": "4h"}

    assert install_okx_x402(app, _settings(), facilitator=facilitator) is True
    return app


def test_unpaid_request_returns_standard_v2_payment_challenge_before_body_validation():
    from x402.http import decode_payment_required_header

    response = TestClient(_app(FakeFacilitator())).post(
        PUBLIC_ANALYSIS_PATH,
        headers={"Content-Type": "application/json"},
        content=b"{}",
    )

    assert response.status_code == 402
    assert "payment-required" in response.headers
    challenge = decode_payment_required_header(response.headers["payment-required"])
    assert challenge.x402_version == 2
    assert challenge.resource.url == RESOURCE
    assert len(challenge.accepts) == 1
    requirement = challenge.accepts[0]
    assert requirement.scheme == "exact"
    assert requirement.network == "eip155:196"
    assert requirement.amount == "10000"
    assert requirement.pay_to == PAY_TO
    assert requirement.max_timeout_seconds == 300
    discovery = challenge.extensions["bazaar"]
    assert discovery["info"]["input"] == {
        "type": "http",
        "method": "POST",
        "bodyType": "json",
        "body": {"symbol": "BTC", "timeframe": "4h"},
    }
    body_schema = discovery["schema"]["properties"]["input"]["properties"]["body"]
    assert body_schema["required"] == ["symbol", "timeframe"]
    assert body_schema["additionalProperties"] is False


def test_no_body_get_discovery_returns_the_same_standard_v2_challenge():
    from x402.http import decode_payment_required_header

    response = TestClient(_app(FakeFacilitator())).get(PUBLIC_ANALYSIS_PATH)

    assert response.status_code == 402
    challenge = decode_payment_required_header(response.headers["payment-required"])
    assert challenge.x402_version == 2
    assert challenge.resource.url == RESOURCE
    assert challenge.accepts[0].scheme == "exact"
    assert challenge.accepts[0].network == "eip155:196"
    assert challenge.accepts[0].amount == "10000"


def test_valid_authorization_reaches_service_and_returns_settlement_receipt():
    from x402.http import (
        decode_payment_required_header,
        decode_payment_response_header,
        encode_payment_signature_header,
    )
    from x402.schemas import PaymentPayload

    facilitator = FakeFacilitator()
    client = TestClient(_app(facilitator))
    challenge_response = client.post(PUBLIC_ANALYSIS_PATH, json={})
    challenge = decode_payment_required_header(challenge_response.headers["payment-required"])
    payment = PaymentPayload(
        payload={"authorization": {"from": PAYER}, "signature": "0x" + "4" * 130},
        accepted=challenge.accepts[0],
        resource=challenge.resource,
    )

    response = client.post(
        PUBLIC_ANALYSIS_PATH,
        headers={"Payment-Signature": encode_payment_signature_header(payment)},
        json={"symbol": "BTC", "timeframe": "4h"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NO_TRADE"
    assert facilitator.verify_calls == 1
    assert facilitator.settle_calls == 1
    receipt = decode_payment_response_header(response.headers["payment-response"])
    assert receipt.success is True
    assert receipt.network == "eip155:196"


def test_x402_configuration_rejects_missing_credentials_and_zero_price():
    import pytest

    app = FastAPI()
    with pytest.raises(RuntimeError, match="OKX_X402_API_KEY"):
        install_okx_x402(app, _settings(okx_x402_api_key=""), facilitator=FakeFacilitator())
    with pytest.raises(RuntimeError, match="greater than zero"):
        install_okx_x402(app, _settings(okx_x402_price_usd="0"), facilitator=FakeFacilitator())


def test_unpaid_x402_requests_use_dedicated_public_rate_limit(monkeypatch):
    monkeypatch.setenv("OKX_ASP_PUBLIC_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    reset_okx_asp_rate_limit_for_tests()
    app = _app(FakeFacilitator())
    install_okx_asp_access_logging(app)
    client = TestClient(app)

    first = client.post(PUBLIC_ANALYSIS_PATH, json={})
    second = client.post(PUBLIC_ANALYSIS_PATH, json={})

    assert first.status_code == 402
    assert second.status_code == 429
    assert second.headers["Retry-After"]
    get_settings.cache_clear()
    reset_okx_asp_rate_limit_for_tests()
