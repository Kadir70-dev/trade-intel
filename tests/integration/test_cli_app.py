"""End-to-end CLI tests using Typer's CliRunner against a per-test SQLite."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trade_intel.cli.main import app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def cli_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Point Settings at a fresh SQLite file and create the schema.

    Clears cached singletons (`get_settings`, `get_engine`,
    `get_sessionmaker`) before and after so test isolation holds when
    other tests have already warmed those caches.
    """
    db = tmp_path / "cli.db"
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
def runner() -> CliRunner:
    return CliRunner()


def _ingest_csv(runner: CliRunner) -> None:
    """Seed the test DB with the canonical inventory fixture."""
    result = runner.invoke(
        app,
        ["parse", str(FIXTURES / "tabular" / "inventory_sample.csv"), "--json"],
    )
    assert result.exit_code == 0, result.stdout


def _ingest_demand(runner: CliRunner, tmp_path: Path) -> None:
    """Seed a buyer message so the matcher has both sides."""
    chat = tmp_path / "chat.txt"
    chat.write_text(
        "28/05/26, 09:00 - Sara: need urgent 5 12pm 256gb asap\n"
        "28/05/26, 09:01 - Bob: looking for 3 mini 5 256gb\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["parse", str(chat), "--json"])
    assert result.exit_code == 0, result.stdout


# ---------- help ----------


def test_root_help_lists_all_commands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "parse",
        "match",
        "who-has",
        "who-needs",
        "hot",
        "opportunities",
        "brief",
        "ping",
        "chat",
        "run-matcher",
    ):
        assert cmd in result.stdout


# ---------- parse ----------


def test_parse_csv_inserts_rows(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "parse",
            str(FIXTURES / "tabular" / "inventory_sample.csv"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["messages_inserted"] == 6
    assert payload["inventory_inserted"] == 6


def test_parse_whatsapp_inserts_rows(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    chat = tmp_path / "chat.txt"
    chat.write_text(
        "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["parse", str(chat), "--json"])
    assert result.exit_code == 0, result.stdout


def test_parse_unknown_extension_requires_type(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    weird = tmp_path / "x.unknown"
    weird.write_text("nothing", encoding="utf-8")
    result = runner.invoke(app, ["parse", str(weird)])
    assert result.exit_code != 0


def test_parse_missing_file_errors(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["parse", "/does/not/exist.csv"])
    assert result.exit_code != 0


# ---------- match ----------


def test_match_seller_side_returns_results(
    cli_db: Path, runner: CliRunner
) -> None:
    _ingest_csv(runner)
    result = runner.invoke(app, ["match", "iPhone 12 Pro Max", "--side", "seller", "-k", "5"])
    assert result.exit_code == 0
    # At least one of the canonical product names should show.
    assert "iPhone 12 Pro Max" in result.stdout


def test_match_buyer_side_runs_against_demand_index(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_demand(runner, tmp_path)
    result = runner.invoke(app, ["match", "12pm", "--side", "buyer", "-k", "5"])
    assert result.exit_code == 0
    assert "iPhone 12 Pro Max" in result.stdout


def test_match_rejects_bad_side(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["match", "x", "--side", "broker"])
    assert result.exit_code != 0


def test_match_empty_index_prints_message(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(app, ["match", "x", "--side", "seller"])
    assert result.exit_code == 0
    assert "empty" in result.stdout.lower()


# ---------- who-has / who-needs ----------


def test_who_has_filters_by_product(cli_db: Path, runner: CliRunner) -> None:
    _ingest_csv(runner)
    result = runner.invoke(app, ["who-has", "iPhone 12 Pro Max"])
    assert result.exit_code == 0
    assert "Ali Mobiles".lower() in result.stdout.lower()


def test_who_has_no_match_prints_empty_msg(
    cli_db: Path, runner: CliRunner
) -> None:
    _ingest_csv(runner)
    result = runner.invoke(app, ["who-has", "Nokia 3310"])
    assert result.exit_code == 0
    assert "No inventory" in result.stdout


def test_who_has_json_output_shape(cli_db: Path, runner: CliRunner) -> None:
    _ingest_csv(runner)
    result = runner.invoke(app, ["who-has", "iPad Mini 5", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert all("seller" in row for row in payload)


def test_who_needs_filters_by_product(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_demand(runner, tmp_path)
    result = runner.invoke(app, ["who-needs", "iPhone 12 Pro Max"])
    assert result.exit_code == 0
    assert "sara" in result.stdout.lower()


# ---------- hot ----------


def test_hot_lists_recent_messages(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_demand(runner, tmp_path)
    result = runner.invoke(app, ["hot", "-n", "5"])
    assert result.exit_code == 0
    assert "sara" in result.stdout.lower() or "bob" in result.stdout.lower()


def test_hot_intent_filter(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    result = runner.invoke(app, ["hot", "--intent", "buyer", "-n", "10"])
    assert result.exit_code == 0


def test_hot_rejects_bad_intent(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["hot", "--intent", "wizard"])
    assert result.exit_code != 0


# ---------- opportunities ----------


def test_opportunities_after_run_matcher_returns_rows(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    matcher = runner.invoke(app, ["run-matcher", "--json"])
    assert matcher.exit_code == 0
    result = runner.invoke(app, ["opportunities", "-n", "3"])
    assert result.exit_code == 0
    assert "Top opportunities" in result.stdout


def test_opportunities_grade_filter(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    runner.invoke(app, ["run-matcher", "--json"])
    result = runner.invoke(app, ["opportunities", "--grade", "C"])
    assert result.exit_code == 0


def test_opportunities_rejects_bad_grade(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(app, ["opportunities", "--grade", "Z"])
    assert result.exit_code != 0


# ---------- brief ----------


def test_brief_renders_summary_on_empty_db(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(app, ["brief"])
    assert result.exit_code == 0
    assert "Summary" in result.stdout


def test_brief_after_ingest_shows_counts(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    runner.invoke(app, ["run-matcher", "--json"])
    result = runner.invoke(app, ["brief", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["active_inventory"] == 6
    assert payload["summary"]["active_demand"] == 2
    assert payload["summary"]["matches"] >= 1
    assert isinstance(payload["top_matches"], list)


# ---------- run-matcher ----------


def test_run_matcher_no_demand_yields_zero(
    cli_db: Path, runner: CliRunner
) -> None:
    _ingest_csv(runner)
    result = runner.invoke(app, ["run-matcher", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["demands_scored"] == 0


def test_run_matcher_creates_matches(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    result = runner.invoke(app, ["run-matcher", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["demands_scored"] == 2
    assert payload["matches_created"] >= 1


def test_run_matcher_rejects_bad_min_grade(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(app, ["run-matcher", "--min-grade", "Z"])
    assert result.exit_code != 0


# ---------- ping ----------


def test_ping_missing_match_id_errors(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["ping", "999"])
    assert result.exit_code != 0
    assert "not found" in result.stdout


def test_ping_renders_seller_message(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    _ingest_csv(runner)
    _ingest_demand(runner, tmp_path)
    runner.invoke(app, ["run-matcher", "--json"])
    # Find a real match id via opportunities --json
    opps = runner.invoke(app, ["opportunities", "-n", "1", "--json"])
    assert opps.exit_code == 0
    payload = json.loads(opps.stdout)
    assert payload
    match_id = payload[0]["id"]
    result = runner.invoke(app, ["ping", str(match_id)])
    assert result.exit_code == 0
    assert "LP" in result.stdout


def test_ping_rejects_bad_side(cli_db: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["ping", "1", "--side", "x"])
    assert result.exit_code != 0
