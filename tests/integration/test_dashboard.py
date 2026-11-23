"""Dashboard, import audit trail, upload safety and chat proxy tests."""

from __future__ import annotations

import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trade_intel.api.main import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WA = FIXTURES / "whatsapp" / "android_sample.txt"
CSV = FIXTURES / "tabular" / "inventory_sample.csv"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("SQLITE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("API_KEY", "k-test")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from trade_intel.config import get_settings
    from trade_intel.db.session import get_engine, get_sessionmaker

    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()
    from trade_intel.db import init_db

    init_db()
    yield
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()


@pytest.fixture
def client(env: None) -> TestClient:
    return TestClient(create_app(), headers={"X-API-Key": "k-test"})


def _upload(client: TestClient, path: Path, uploader: str = "Main", name: str | None = None):
    with path.open("rb") as fh:
        return client.post(
            "/ingest",
            params={"run_matcher": "true"},
            files={"file": (name or path.name, fh)},
            data={"uploader": uploader},
        )


def test_ui_shell_public_but_data_requires_key(env: None) -> None:
    anon = TestClient(create_app())
    assert anon.get("/").status_code == 200
    assert anon.get("/static/dashboard.js").status_code == 200
    for path in ("/dashboard/summary", "/dashboard/imports", "/dashboard/opportunities"):
        assert anon.get(path).status_code == 401
    assert anon.post("/chat/stream", json={"query": "x"}).status_code == 401
    assert anon.get("/dashboard/summary", headers={"X-API-Key": "wrong"}).status_code == 401


def test_import_records_uploader_format_counts_and_dedup(client: TestClient) -> None:
    first = _upload(client, WA, "Main")
    assert first.status_code == 201, first.text
    assert first.json()["uploader"] == "Main"
    again = _upload(client, WA, "Yaseen")
    assert again.json()["stats"]["messages_inserted"] == 0
    assert again.json()["stats"]["messages_skipped_dup"] > 0

    rows = client.get("/dashboard/imports").json()
    assert [r["uploader"] for r in rows] == ["Yaseen", "Main"]
    assert rows[1]["format"] == "whatsapp" and rows[1]["filename"] == WA.name
    assert rows[1]["inserted"] > 0 and rows[0]["inserted"] == 0
    assert rows[0]["duplicates"] == rows[1]["inserted"]
    assert {r["status"] for r in rows} == {"completed"}  # matching ran after response


def test_csv_import_then_summary_and_opportunities(client: TestClient) -> None:
    assert _upload(client, WA, "Main").status_code == 201
    assert _upload(client, CSV, "Yaseen").status_code == 201
    s = client.get("/dashboard/summary").json()
    assert s["imports"] == 2 and s["messages"] > 0
    assert set(s["match_grades"]) == {"A+", "A", "B", "C"}
    assert set(s["intent"]) == {"buyer", "seller", "broker", "unknown"}
    assert s["matches"] == sum(s["match_grades"].values())
    opps = client.get("/dashboard/opportunities?limit=5").json()
    scores = [o["score"] for o in opps]
    assert scores == sorted(scores, reverse=True)
    assert all(o["rank"] == i + 1 for i, o in enumerate(opps))


def test_rejects_unknown_uploader_bad_ext_empty_and_oversize(client: TestClient) -> None:
    assert _upload(client, WA, "Mallory").status_code == 400
    bad = client.post("/ingest", files={"file": ("x.exe", b"abc")}, data={"uploader": "Main"})
    assert bad.status_code == 400
    empty = client.post("/ingest", files={"file": ("e.txt", b"")}, data={"uploader": "Main"})
    assert empty.status_code == 400
    big = client.post(
        "/ingest", files={"file": ("b.txt", b"a" * (2 * 1024 * 1024))}, data={"uploader": "Main"}
    )
    assert big.status_code == 413


def test_hostile_filename_is_sanitised_and_never_used_as_path(client: TestClient) -> None:
    r = client.post(
        "/ingest",
        files={"file": ("../../etc/<script>x.txt", WA.read_bytes())},
        data={"uploader": "Main"},
    )
    assert r.status_code == 201, r.text
    name = r.json()["filename"]
    assert "/" not in name and "<" not in name and name.endswith(".txt")


def test_corrupt_excel_records_failed_import(client: TestClient) -> None:
    r = client.post(
        "/ingest", files={"file": ("bad.xlsx", b"not a workbook")}, data={"uploader": "Main"}
    )
    assert r.status_code == 422
    row = client.get("/dashboard/imports").json()[0]
    assert row["status"] == "failed" and row["errors"] == 1


class _Broker(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: token\ndata: hello\n\nevent: done\ndata: {\"answer\":\"hello\"}\n\n")

    def log_message(self, *a: object) -> None:
        pass


def test_chat_proxy_streams_sse_from_broker(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    srv = HTTPServer(("127.0.0.1", 0), _Broker)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("BROKER_URL", f"http://127.0.0.1:{srv.server_port}")
    from trade_intel.config import get_settings

    get_settings.cache_clear()
    c = TestClient(create_app(), headers={"X-API-Key": "k-test"})
    r = c.post("/chat/stream", json={"query": "need 5 iphone"})
    srv.shutdown()
    assert r.status_code == 200
    assert "event: token" in r.text and "hello" in r.text


def test_chat_proxy_502_when_broker_down(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROKER_URL", "http://127.0.0.1:9")
    from trade_intel.config import get_settings

    get_settings.cache_clear()
    c = TestClient(create_app(), headers={"X-API-Key": "k-test"})
    assert c.post("/chat/stream", json={"query": "x"}).status_code == 502
    assert c.post("/chat/stream", json={"query": ""}).status_code == 422
