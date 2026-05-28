"""End-to-end FastAPI tests (Phase 6).

Uses :class:`fastapi.testclient.TestClient` against a per-test
in-memory SQLite. The DB is seeded via the same ``ingest_messages`` /
``parse_*`` machinery the CLI exercises so the API tests verify the
real wire path, not a mocked one.
"""

from __future__ import annotations

import io
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trade_intel.api.main import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV_FIXTURE = FIXTURES / "tabular" / "inventory_sample.csv"


# ---------- fixtures ----------


@pytest.fixture
def cli_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Fresh per-test SQLite + cleared Settings cache."""
    db = tmp_path / "api.db"
    monkeypatch.setenv("SQLITE_URL", f"sqlite:///{db}")

    from trade_intel.config import get_settings
    from trade_intel.db.session import get_engine, get_sessionmaker

    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()

    from trade_intel.db import init_db

    init_db()
    yield db

    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


@pytest.fixture
def client(cli_db: Path) -> TestClient:
    """Fresh app + TestClient bound to the per-test DB."""
    app = create_app()
    return TestClient(app)


def _seed_inventory(client: TestClient) -> None:
    with CSV_FIXTURE.open("rb") as fh:
        resp = client.post(
            "/ingest",
            files={"file": ("inventory_sample.csv", fh, "text/csv")},
        )
    assert resp.status_code == 201, resp.text


def _seed_demand(client: TestClient) -> None:
    chat = (
        "28/05/26, 09:00 - Sara: need urgent 5 12pm 256gb asap\n"
        "28/05/26, 09:01 - Bob: looking for 3 mini 5 256gb\n"
    )
    resp = client.post(
        "/ingest",
        files={"file": ("chat.txt", io.BytesIO(chat.encode("utf-8")), "text/plain")},
    )
    assert resp.status_code == 201, resp.text


def _seed_full(client: TestClient) -> None:
    _seed_inventory(client)
    _seed_demand(client)


# ---------- healthz ----------


def test_healthz_open_without_auth(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"
    assert "version" in payload
    assert payload["environment"] == "dev"


# ---------- auth ----------


def test_protected_endpoint_open_when_no_api_key_configured(
    client: TestClient,
) -> None:
    # No api_key in env → dev-open mode.
    r = client.get("/messages")
    assert r.status_code == 200


def test_protected_endpoint_requires_key_when_configured(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", "supersecret")
    from trade_intel.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    cl = TestClient(app)
    no_key = cl.get("/messages")
    assert no_key.status_code == 401
    wrong = cl.get("/messages", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    ok = cl.get("/messages", headers={"X-API-Key": "supersecret"})
    assert ok.status_code == 200
    # Restore for downstream tests.
    monkeypatch.delenv("API_KEY", raising=False)
    get_settings.cache_clear()


def test_healthz_open_even_with_api_key_configured(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", "x")
    from trade_intel.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    cl = TestClient(app)
    r = cl.get("/healthz")
    assert r.status_code == 200
    monkeypatch.delenv("API_KEY", raising=False)
    get_settings.cache_clear()


# ---------- ingest ----------


def test_ingest_csv(client: TestClient) -> None:
    with CSV_FIXTURE.open("rb") as fh:
        r = client.post(
            "/ingest",
            files={"file": ("inventory_sample.csv", fh, "text/csv")},
        )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["source_type"] == "csv"
    assert payload["filename"] == "inventory_sample.csv"
    assert payload["matcher_scheduled"] is False
    assert payload["stats"]["messages_inserted"] == 6
    assert payload["stats"]["inventory_inserted"] == 6


def test_ingest_whatsapp_via_txt_extension(client: TestClient) -> None:
    body = "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh\n"
    r = client.post(
        "/ingest",
        files={"file": ("chat.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["source_type"] == "whatsapp"


def test_ingest_explicit_source_type_form_field(client: TestClient) -> None:
    body = (
        "Seller,Product\n"
        "Ali Mobiles,iPhone 12 Pro Max\n"
    )
    # Unknown extension — must require source_type explicitly.
    r = client.post(
        "/ingest",
        files={"file": ("data.unknown", io.BytesIO(body.encode()), "text/plain")},
        data={"source_type": "csv"},
    )
    assert r.status_code == 201


def test_ingest_unknown_extension_without_hint_400(client: TestClient) -> None:
    r = client.post(
        "/ingest",
        files={"file": ("x.unknown", io.BytesIO(b"nope"), "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "cannot infer" in r.json()["detail"]


def test_ingest_invalid_source_type_400(client: TestClient) -> None:
    r = client.post(
        "/ingest",
        files={"file": ("x.csv", io.BytesIO(b""), "text/csv")},
        data={"source_type": "wizard"},
    )
    assert r.status_code == 400


def test_ingest_run_matcher_query_flag(client: TestClient) -> None:
    _seed_inventory(client)
    # Now ingest a buyer file with run_matcher=true to trigger background.
    body = "28/05/26, 09:00 - Sara: need urgent 5 12pm 256gb asap\n"
    r = client.post(
        "/ingest?run_matcher=true",
        files={"file": ("chat.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 201
    assert r.json()["matcher_scheduled"] is True


# ---------- messages ----------


def test_messages_list_empty(client: TestClient) -> None:
    r = client.get("/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_messages_list_after_ingest(client: TestClient) -> None:
    _seed_demand(client)
    r = client.get("/messages")
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 2
    assert {m["sender_name"] for m in payload} == {"Sara", "Bob"}


def test_messages_intent_filter(client: TestClient) -> None:
    _seed_full(client)
    r = client.get("/messages?intent=buyer")
    assert r.status_code == 200
    payload = r.json()
    assert all(m["intent"] == "buyer" for m in payload)


def test_messages_limit_and_offset(client: TestClient) -> None:
    _seed_full(client)
    r = client.get("/messages?limit=2&offset=0")
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_messages_get_by_id_404(client: TestClient) -> None:
    r = client.get("/messages/999")
    assert r.status_code == 404


def test_messages_get_by_id_200(client: TestClient) -> None:
    _seed_demand(client)
    r = client.get("/messages/1")
    assert r.status_code == 200
    assert r.json()["id"] == 1


# ---------- sellers ----------


def test_sellers_list_orders_by_post_count(client: TestClient) -> None:
    _seed_inventory(client)
    r = client.get("/sellers")
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) >= 1
    # Ali Mobiles posted twice → should be at the top.
    assert payload[0]["canonical_name"] == "ali mobiles"
    assert payload[0]["post_count"] == 2


def test_sellers_detail_includes_inventory(client: TestClient) -> None:
    _seed_inventory(client)
    # Find Ali's id
    ali = next(
        s for s in client.get("/sellers").json() if s["canonical_name"] == "ali mobiles"
    )
    r = client.get(f"/sellers/{ali['id']}")
    assert r.status_code == 200
    payload = r.json()
    assert payload["canonical_name"] == "ali mobiles"
    assert isinstance(payload["inventory_items"], list)
    assert len(payload["inventory_items"]) >= 1


def test_sellers_detail_404(client: TestClient) -> None:
    r = client.get("/sellers/9999")
    assert r.status_code == 404


# ---------- buyers ----------


def test_buyers_list(client: TestClient) -> None:
    _seed_demand(client)
    r = client.get("/buyers")
    assert r.status_code == 200
    payload = r.json()
    assert {b["canonical_name"] for b in payload} == {"sara", "bob"}


def test_buyers_detail_includes_demand(client: TestClient) -> None:
    _seed_demand(client)
    sara = next(
        b for b in client.get("/buyers").json() if b["canonical_name"] == "sara"
    )
    r = client.get(f"/buyers/{sara['id']}")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload["demands"], list)
    assert len(payload["demands"]) >= 1


def test_buyers_detail_404(client: TestClient) -> None:
    r = client.get("/buyers/9999")
    assert r.status_code == 404


# ---------- match ----------


def test_match_returns_empty_when_no_data(client: TestClient) -> None:
    r = client.post("/match", json={"query": "12pm", "side": "seller", "k": 5})
    assert r.status_code == 200
    assert r.json()["hits"] == []


def test_match_seller_after_ingest(client: TestClient) -> None:
    _seed_inventory(client)
    r = client.post(
        "/match", json={"query": "iPhone 12 Pro Max", "side": "seller", "k": 5}
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["side"] == "seller"
    assert payload["query"] == "iPhone 12 Pro Max"
    assert any("iPhone 12 Pro Max" in h["product"] for h in payload["hits"])


def test_match_buyer_after_ingest(client: TestClient) -> None:
    _seed_demand(client)
    r = client.post("/match", json={"query": "12pm", "side": "buyer", "k": 5})
    assert r.status_code == 200
    assert r.json()["hits"]


def test_match_validates_side_value(client: TestClient) -> None:
    r = client.post("/match", json={"query": "x", "side": "broker", "k": 5})
    assert r.status_code == 422  # Pydantic validation


def test_match_validates_k_range(client: TestClient) -> None:
    r = client.post("/match", json={"query": "x", "side": "seller", "k": 0})
    assert r.status_code == 422


def test_match_rejects_empty_query(client: TestClient) -> None:
    r = client.post("/match", json={"query": "", "side": "seller", "k": 5})
    assert r.status_code == 422


# ---------- leads ----------


def _seed_and_match(client: TestClient) -> None:
    """Seed both sides + synchronously run the matcher endpoint."""
    _seed_inventory(client)
    # Use run_matcher=true on the demand ingest so leads get created.
    body = "28/05/26, 09:00 - Sara: need urgent 5 12pm 256gb asap\n"
    r = client.post(
        "/ingest?run_matcher=true",
        files={"file": ("chat.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 201


def test_leads_empty_initially(client: TestClient) -> None:
    r = client.get("/leads")
    assert r.status_code == 200
    assert r.json() == []


def test_leads_after_match_run(client: TestClient) -> None:
    _seed_and_match(client)
    r = client.get("/leads")
    assert r.status_code == 200
    leads = r.json()
    assert len(leads) >= 1
    for lead in leads:
        assert lead["stage"] == "NEW"


def test_leads_stage_filter(client: TestClient) -> None:
    _seed_and_match(client)
    r = client.get("/leads?stage=NEW")
    assert r.status_code == 200
    assert all(lead["stage"] == "NEW" for lead in r.json())


def test_leads_get_by_id_404(client: TestClient) -> None:
    r = client.get("/leads/9999")
    assert r.status_code == 404


def test_leads_advance_transitions_stage(client: TestClient) -> None:
    _seed_and_match(client)
    leads = client.get("/leads").json()
    assert leads
    lid = leads[0]["id"]
    r = client.post(
        f"/leads/{lid}/advance",
        json={
            "to_stage": "CONTACTED",
            "action": "sent WhatsApp ping",
            "notes": "asked for LP on 12pm 256gb",
        },
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["stage"] == "CONTACTED"
    assert payload["last_action"] == "sent WhatsApp ping"
    assert payload["notes"] == "asked for LP on 12pm 256gb"


def test_leads_advance_404(client: TestClient) -> None:
    r = client.post(
        "/leads/9999/advance",
        json={"to_stage": "CONTACTED", "action": "x"},
    )
    assert r.status_code == 404


def test_leads_advance_validates_stage_enum(client: TestClient) -> None:
    _seed_and_match(client)
    leads = client.get("/leads").json()
    lid = leads[0]["id"]
    r = client.post(
        f"/leads/{lid}/advance",
        json={"to_stage": "WIZARD", "action": "x"},
    )
    assert r.status_code == 422


# ---------- brief ----------


def test_brief_empty_db(client: TestClient) -> None:
    r = client.get("/brief")
    assert r.status_code == 200
    payload = r.json()
    assert payload["summary"]["messages"] == 0
    assert payload["top_matches"] == []
    assert payload["urgent_demand"] == []
    assert payload["fresh_inventory"] == []


def test_brief_after_full_pipeline(client: TestClient) -> None:
    _seed_and_match(client)
    r = client.get("/brief")
    assert r.status_code == 200
    payload = r.json()
    summary = payload["summary"]
    assert summary["active_inventory"] == 6
    assert summary["active_demand"] == 1
    assert summary["matches"] >= 1
    assert summary["new_pipeline"] >= 1
    assert len(payload["top_matches"]) >= 1
    assert len(payload["urgent_demand"]) >= 1  # 'urgent' + 'asap' both lift HIGH
    assert len(payload["fresh_inventory"]) >= 1


# ---------- OpenAPI ----------


def test_openapi_schema_published(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    payload = r.json()
    assert "openapi" in payload
    paths = payload["paths"]
    for endpoint in (
        "/healthz",
        "/ingest",
        "/messages",
        "/sellers",
        "/buyers",
        "/match",
        "/leads",
        "/brief",
    ):
        assert endpoint in paths, f"missing {endpoint} in OpenAPI spec"


def test_docs_endpoint_served(client: TestClient) -> None:
    r = client.get("/docs")
    assert r.status_code == 200
    assert "Swagger" in r.text or "swagger" in r.text
