"""Loopback HTTP read seam for two fixed, public synthetic V3 resources.

This is a protocol-level client surface, not a host integration or a general
deal API. It loads a packaged public fixture, reads no user files, runs no
engines, and creates no artifacts.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from plat_harness.original_thesis import record_original_thesis
from plat_harness.reasonability import present_underwriting
from plat_harness.underwriting_direction import (
    YIELD_FORMULA,
    classify_physical_position,
    year_2_unlevered_yield_on_cost,
    yield_in_band,
)

DEAL_PATH = "/v1/synthetic/deals/DEMO-LEASE-UP"
OAK_RIDGE_PATH = "/v1/synthetic/operations/oak-ridge-2026-05"
READ_PATHS = frozenset({DEAL_PATH, OAK_RIDGE_PATH})


def _deal_resource() -> dict:
    """Apply the existing harness contracts to a fixed, invented fixture."""
    position = classify_physical_position(8, 2, 0, 10, noi="72000")
    assert position is not None
    price, capex, year_2_noi = "900000", "100000", "80000"
    ratio = year_2_unlevered_yield_on_cost(year_2_noi, price, capex)
    review = present_underwriting(
        {
            "purchase_price": price,
            "going_in_cap_rate": "0.08",
            "exit_cap_rate": "0.055",
            "price_per_unit": "90000",
            "units": 10,
            "minimum_dscr": "1.20",
            "year_1_noi": "72000",
        }
    )
    thesis = record_original_thesis(
        "DEMO-LEASE-UP",
        purchase_price=price,
        year_2_unlevered_noi=year_2_noi,
        capex=capex,
        present_as_bid=review["present_as_bid"],
    )
    return {
        "contract_version": 1,
        "data_class": "synthetic",
        "fixture_id": "DEMO-LEASE-UP",
        "occupancy": position.as_dict(),
        "year_2_yield": {
            "year_2_unlevered_noi": year_2_noi,
            "purchase_price": price,
            "capex": capex,
            "formula": YIELD_FORMULA,
            "ratio": format(ratio, "f"),
            "in_target_band": yield_in_band(ratio),
        },
        "reasonability": {
            "review_type": "numeric_band_check",
            "present_as_bid": review["present_as_bid"],
            "bid": review["bid"],
            "engine": review["engine"],
            "breaches": review["breaches"],
        },
        "original_thesis": {
            "deal_id": thesis.thesis.deal_id,
            "purchase_price": thesis.thesis.purchase_price,
            "year_2_unlevered_noi": thesis.thesis.year_2_unlevered_noi,
            "capex": thesis.thesis.capex,
            "year_2_unlevered_yield_on_cost": thesis.thesis.year_2_unlevered_yield_on_cost,
            "present_as_bid": thesis.thesis.present_as_bid,
            "operations_actual_noi": thesis.operations_actual_noi,
        },
    }


def _oak_ridge_resource() -> dict:
    """Load the versioned public sample; no producer or user files needed."""
    return json.loads(files("plat_harness").joinpath(
        "fixtures/oak_ridge_2026_05.v2.json"
    ).read_text(encoding="utf-8"))


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        default=lambda value: format(value, "f") if isinstance(value, Decimal) else _raise_type(value),
        separators=(",", ":"),
    ).encode("utf-8")


def _raise_type(value: object) -> None:
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def make_server(token_scopes: Mapping[str, Iterable[str]], port: int = 0) -> ThreadingHTTPServer:
    """Bind only to loopback; each token can read only its explicit paths."""
    scopes = {token: frozenset(paths) for token, paths in token_scopes.items()}
    if not scopes or any(not token or not paths or not paths <= READ_PATHS for token, paths in scopes.items()):
        raise ValueError("Each nonempty token needs one or more known synthetic read paths")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            scope = next(
                (allowed for secret, allowed in scopes.items() if hmac.compare_digest(token, secret)),
                None,
            ) if token.isascii() else None
            if scope is None:
                self._send(401, {"error": "UNAUTHENTICATED"}, authenticate=True)
                return
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment or parsed.path not in READ_PATHS:
                self._send(404, {"error": "NOT_FOUND"})
                return
            if parsed.path not in scope:
                self._send(403, {"error": "SCOPE_FORBIDDEN"})
                return
            resource = _deal_resource() if parsed.path == DEAL_PATH else _oak_ridge_resource()
            self._send(200, resource)

        def do_POST(self) -> None:
            self._send(405, {"error": "READ_ONLY"})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def _send(self, status: int, payload: dict, *, authenticate: bool = False) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if authenticate:
                self.send_header("WWW-Authenticate", 'Bearer realm="plat-synthetic-read"')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Avoid recording bearer credentials or untrusted URL text.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve fixed synthetic PLAT read resources on loopback")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("PLAT_SYNTHETIC_READ_TOKEN", "")
    if not token:
        parser.error("PLAT_SYNTHETIC_READ_TOKEN is required")
    with make_server({token: READ_PATHS}, port=args.port) as server:
        print(f"PLAT synthetic read HTTP listening on 127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
