"""Exercise the synthetic connector through an HTTP client, not a direct call."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from threading import Thread

import pytest

from plat_harness.synthetic_read_http import DEAL_PATH, OAK_RIDGE_PATH, make_server


@pytest.fixture
def client():
    server = make_server({"deal-secret": {DEAL_PATH}, "ops-secret": {OAK_RIDGE_PATH}})
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path: str, token: str = "", method: str = "GET"):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        status = response.status
        body = json.loads(response.read())
        cache_control = response.getheader("Cache-Control")
        connection.close()
        return status, body, cache_control

    try:
        yield request
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_deal_contracts_via_http_client(client) -> None:
    status, body, cache_control = client(DEAL_PATH, "deal-secret")
    assert status == 200
    assert cache_control == "no-store"
    assert body["data_class"] == "synthetic"
    assert body["contract_version"] == 1
    assert body["occupancy"]["label"] == "lease_up"
    assert body["occupancy"]["rate"] == "0.8"
    assert body["occupancy"]["denominator"] == body["reasonability"]["engine"]["units"]
    assert sum(body["occupancy"][key] for key in ("occupied", "vacant", "down")) == 10
    assert body["year_2_yield"]["ratio"] == "0.08"
    assert body["year_2_yield"]["in_target_band"] is True
    assert body["reasonability"]["review_type"] == "numeric_band_check"
    assert body["reasonability"]["present_as_bid"] is False
    assert body["reasonability"]["bid"] is None
    assert body["reasonability"]["breaches"] == [
        {"field": "exit_cap", "value": "0.055", "band": "[0.06, 0.12]"}
    ]
    assert body["original_thesis"]["year_2_unlevered_yield_on_cost"] == "0.08"
    assert body["original_thesis"]["present_as_bid"] is False
    assert body["original_thesis"]["operations_actual_noi"] is None


def test_oak_ridge_pinned_sample_via_http_client(client) -> None:
    status, body, _ = client(OAK_RIDGE_PATH, "ops-secret")
    assert status == 200
    assert body["data_class"] == "synthetic"
    assert body["actual_noi"] == "339150"
    assert body["budget_noi"] == "353200"
    assert body["noi_variance"] == "-14050"
    assert (body["occupied_units"], body["vacant_units"], body["down_units"]) == (153, 15, 4)
    assert "denominator" not in body
    assert body["source"]["commit"] == "171eb9622f53fe9d6b9703191d5a6832064100ed"


def test_scope_auth_and_read_only_boundary(client) -> None:
    assert client(DEAL_PATH)[0:2] == (401, {"error": "UNAUTHENTICATED"})
    assert client(DEAL_PATH, "wrong")[0] == 401
    assert client(DEAL_PATH, "ops-secret")[0:2] == (403, {"error": "SCOPE_FORBIDDEN"})
    assert client(OAK_RIDGE_PATH, "deal-secret")[0] == 403
    assert client(DEAL_PATH + "?file=/etc/passwd", "deal-secret")[0] == 404
    assert client("/v1/synthetic/deals/OTHER", "deal-secret")[0] == 404
    assert client(DEAL_PATH, "deal-secret", "POST")[0:2] == (405, {"error": "READ_ONLY"})


def test_reject_unknown_or_empty_scope() -> None:
    with pytest.raises(ValueError):
        make_server({"token": {"/v1/private"}})
    with pytest.raises(ValueError):
        make_server({"": {DEAL_PATH}})
