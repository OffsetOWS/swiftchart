#!/usr/bin/env python3
"""Protocol-level validator for SwiftChart's OKX x402 endpoint.

Set OKX_X402_PAYMENT_SIGNATURE to an already-authorized v2 payment signature to
also run paid, replay, and media-type compatibility checks. The signature is
never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request


DEFAULT_ENDPOINT = "https://swiftchart.vercel.app/api/asp/okx/public/analyze-market"
BODY = json.dumps({"symbol": "BTC", "timeframe": "4h"}).encode()


def request(endpoint: str, *, method: str, headers: dict[str, str], body: bytes | None):
    req = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=35)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = raw.decode(errors="replace")
    safe_headers = {
        key.lower(): ("[present]" if key.lower() in {"payment-required", "payment-response"} else value)
        for key, value in response.headers.items()
        if key.lower() in {"content-type", "payment-required", "payment-response", "location", "allow"}
    }
    return response.status, safe_headers, parsed, response.headers.get("PAYMENT-REQUIRED")


def decode_challenge(value: str) -> dict:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.b64decode(padded))


def show(name: str, result) -> None:
    status, headers, body, _ = result
    print(json.dumps({"test": name, "status": status, "headers": headers, "body": body}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    args = parser.parse_args()

    unpaid_get = request(args.endpoint, method="GET", headers={}, body=None)
    show("unpaid_get", unpaid_get)
    challenge = decode_challenge(unpaid_get[3]) if unpaid_get[3] else {}
    accepts = challenge.get("accepts") or []
    assert unpaid_get[0] == 402 and challenge.get("x402Version") == 2 and accepts

    unpaid_post = request(
        args.endpoint,
        method="POST",
        headers={"Content-Type": "application/json"},
        body=BODY,
    )
    show("unpaid_post", unpaid_post)
    assert unpaid_post[0] == 402 and unpaid_post[3]

    invalid_payload = base64.b64encode(json.dumps({"x402Version": 2, "payload": {}}).encode()).decode()
    invalid = request(
        args.endpoint,
        method="POST",
        headers={"Content-Type": "application/json", "PAYMENT-SIGNATURE": invalid_payload},
        body=BODY,
    )
    show("invalid_payment", invalid)
    assert invalid[0] == 402

    signature = os.getenv("OKX_X402_PAYMENT_SIGNATURE")
    if not signature:
        print("SKIP paid/replay tests: OKX_X402_PAYMENT_SIGNATURE is not set")
        return 0

    paid_headers = {"Content-Type": "application/json", "PAYMENT-SIGNATURE": signature}
    paid = request(args.endpoint, method="POST", headers=paid_headers, body=BODY)
    show("paid", paid)
    replay = request(args.endpoint, method="POST", headers=paid_headers, body=BODY)
    show("duplicate_replay", replay)
    media_compat = request(
        args.endpoint,
        method="POST",
        headers={"PAYMENT-SIGNATURE": signature},
        body=BODY,
    )
    show("payment_replay_without_content_type", media_compat)
    assert paid[0] == 200
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
