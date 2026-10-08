"""Live smoke korpusu referencyjnego (AU-011): klient, statystyki, porównanie przebiegów, raport i kod wyjścia."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.main import app
from app.schemas.analyze import AnalyzeResponse, ParcelIdAnalyzeRequest
from scripts import live_smoke_corpus as smoke

CASES = [{"case_id": f"c{i:02d}", "parcel_identifier": f"146510_8.0502.{i}"} for i in range(1, 7)]


def _ok(status: str = "partial", *, mpzp: int | None = 0, pog: int | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "analysis_id": 7,
        "access_token": "v2.1.k.TAJNYTOKEN",
        "status": status,
        "mpzp_zones": [{}] * mpzp if mpzp is not None else None,
        "pog": None if pog is None else {"zones": [{}] * pog, "legal_status": "binding", "coverage_status": "available"},
        "warnings": [{"code": "B"}, {"code": "A"}, {"code": "A"}],
    }
    body.update(extra)
    return body


def _error(code: str) -> dict[str, Any]:
    return {"error": code, "detail": "x", "request_id": "r"}


def _run(handler, cases=CASES, **kwargs) -> list[dict[str, Any]]:
    async def no_sleep(_: float) -> None:
        return None

    kwargs.setdefault("sleep", no_sleep)
    return asyncio.run(
        smoke.run_smoke("http://api.test", cases, transport=httpx.MockTransport(handler), run_id="t", **kwargs)
    )


def _by_id(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {result["case_id"]: result for result in results}


# --- Retry-After i klasy --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("30", 30.0), (" 5 ", 5.0), ("0", 0.0), (None, None), ("", None), ("kiedyś", None), ("-3", None), ("1.5", None)],
)
def test_parse_retry_after_seconds(raw: str | None, expected: float | None) -> None:
    assert smoke.parse_retry_after(raw) == expected


def test_parse_retry_after_http_date_is_relative_to_now_and_never_negative() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC).timestamp()

    assert smoke.parse_retry_after("Thu, 08 Oct 2026 12:01:30 GMT", now=lambda: now) == 90.0
    assert smoke.parse_retry_after("Thu, 08 Oct 2026 11:00:00 GMT", now=lambda: now) == 0.0


def test_rate_limit_gate_holds_everyone_until_the_longest_window_ends() -> None:
    clock = [100.0]
    gate = smoke.RateLimitGate(lambda: clock[0])

    assert gate.remaining() == 0.0
    gate.close_for(30)
    gate.close_for(10)  # krótsza prośba nie skraca bramki
    assert gate.remaining() == 30.0
    clock[0] = 125.0
    assert gate.remaining() == 5.0
    clock[0] = 140.0
    assert gate.remaining() == 0.0


@pytest.mark.parametrize(("code", "expected"), [(200, "2xx"), (404, "4xx"), (429, "4xx"), (500, "5xx"), (503, "5xx"), (None, "transport")])
def test_classify_http(code: int | None, expected: str) -> None:
    assert smoke.classify_http(code) == expected


# --- Pola pochodne odpowiedzi -----------------------------------------------------


def test_summary_of_a_success_never_contains_the_access_token() -> None:
    summary = smoke.summarize_response(200, _ok("complete", mpzp=2, pog=3, manual_zone_required=False, mpzp_discovery={"status": "available"}))

    assert summary["analysis_status"] == "complete"
    assert (summary["mpzp_zone_count"], summary["pog_zone_count"]) == (2, 3)
    assert summary["pog_legal_status"] == "binding" and summary["pog_coverage_status"] == "available"
    assert summary["mpzp_discovery_status"] == "available"
    assert summary["manual_zone_required"] is False
    assert summary["warning_codes"] == ["A", "B"]
    assert "TAJNYTOKEN" not in json.dumps(summary)


def test_missing_sections_are_null_not_zero() -> None:
    summary = smoke.summarize_response(200, _ok(mpzp=None, pog=None))

    assert summary["mpzp_zone_count"] is None
    assert summary["pog_zone_count"] is None
    assert smoke.summarize_response(200, {**_ok(), "pog": {"zones": []}})["pog_zone_count"] == 0


def test_error_response_yields_the_error_code_and_nothing_else() -> None:
    summary = smoke.summarize_response(503, _error("PERSISTENCE_FAILED"))

    assert summary["error_code"] == "PERSISTENCE_FAILED"
    assert summary["analysis_status"] is None and summary["mpzp_zone_count"] is None


@pytest.mark.parametrize("body", [None, "tekst", [1, 2], 5])
def test_non_object_bodies_are_marked_invalid(body: object) -> None:
    assert smoke.summarize_response(200, body)["body_valid"] is False


# --- Klient: równoległość, 429, błędy -----------------------------------------------


def test_every_case_posts_the_parcel_id_with_a_correlation_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok())

    results = _run(handler)

    assert sorted(
        (json.loads(request.content) for request in seen), key=lambda body: body["parcel_identifier"]
    ) == [{"method": "parcel_id", "parcel_identifier": case["parcel_identifier"]} for case in CASES]
    assert {request.url.path for request in seen} == {"/analyze"}
    assert all(request.url.query == b"" for request in seen)
    assert [result["case_id"] for result in results] == [case["case_id"] for case in CASES]  # kolejność manifestu
    assert [result["request_id"] for result in results] == [f"live-smoke-t-{i:03d}" for i in range(1, 7)]
    assert all(request.headers["x-request-id"].startswith("live-smoke-t-") for request in seen)


def test_force_refresh_is_sent_only_on_request() -> None:
    queries: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.query)
        return httpx.Response(200, json=_ok())

    _run(handler, CASES[:1])
    _run(handler, CASES[:1], force_refresh=True)

    assert queries == [b"", b"force_refresh=true"]


def test_concurrency_is_bounded() -> None:
    state = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return httpx.Response(200, json=_ok())

    _run(handler, concurrency=3)
    assert state["peak"] == 3

    state.update(now=0, peak=0)
    _run(handler, concurrency=1)
    assert state["peak"] == 1


def test_invalid_concurrency_is_rejected() -> None:
    with pytest.raises(smoke.SmokeError):
        _run(lambda request: httpx.Response(200, json=_ok()), concurrency=0)


def test_server_errors_are_recorded_and_never_retried() -> None:
    calls: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        identifier = json.loads(request.content)["parcel_identifier"]
        calls[identifier] += 1
        if identifier.endswith(".2"):
            return httpx.Response(500, json=_error("INTERNAL_ERROR"), headers={"X-Request-ID": "srv-req-0001"})
        if identifier.endswith(".3"):
            return httpx.Response(503, json=_error("PERSISTENCE_FAILED"))
        if identifier.endswith(".4"):
            return httpx.Response(404, json=_error("PARCEL_NOT_FOUND"))
        return httpx.Response(200, json=_ok("complete", mpzp=1, pog=2))

    results = _by_id(_run(handler))

    assert results["c02"]["http_status"] == 500 and results["c02"]["error_code"] == "INTERNAL_ERROR"
    assert results["c02"]["request_id"] == "srv-req-0001"  # identyfikator z odpowiedzi serwera
    assert results["c03"]["http_class"] == "5xx" and results["c04"]["http_class"] == "4xx"
    assert results["c01"]["analysis_status"] == "complete" and results["c01"]["attempts"] == 1
    assert all(count == 1 for count in calls.values())  # ani 4xx, ani 5xx nie są ponawiane


def test_429_waits_for_retry_after_and_then_succeeds() -> None:
    waits: list[float] = []
    attempts: Counter[str] = Counter()

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        identifier = json.loads(request.content)["parcel_identifier"]
        attempts[identifier] += 1
        if attempts[identifier] < 3:
            return httpx.Response(429, json=_error("RATE_LIMITED"), headers={"Retry-After": "7"})
        return httpx.Response(200, json=_ok())

    (result,) = _run(handler, CASES[:1], sleep=sleep)

    assert result["http_status"] == 200 and result["attempts"] == 3
    assert waits and all(wait > 0 for wait in waits)
    assert result["waited_seconds"] > 0


def test_429_without_retry_after_falls_back_to_the_limiter_window_capped_by_max_wait() -> None:
    gate_waits: list[float] = []

    async def sleep(seconds: float) -> None:
        gate_waits.append(seconds)

    responses = iter([httpx.Response(429, json=_error("RATE_LIMITED")), httpx.Response(200, json=_ok())])

    (result,) = _run(lambda request: next(responses), CASES[:1], sleep=sleep, max_wait_seconds=20)

    assert result["http_status"] == 200 and result["attempts"] == 2
    assert 0 < gate_waits[0] <= 20  # 60 s okna ograniczone do --max-wait


def test_persistent_429_gives_up_after_max_retries_and_is_not_a_server_error() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json=_error("RATE_LIMITED"), headers={"Retry-After": "1"})

    (result,) = _run(handler, CASES[:1], max_retries=2)

    assert len(calls) == 3 and result["attempts"] == 3
    assert (result["http_status"], result["http_class"], result["retry_after_seconds"]) == (429, "4xx", 1.0)
    assert smoke.build_summary([result])["passed"] is True  # limit to nie 5xx; widać go w histogramie


def test_transport_errors_are_recorded_without_a_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("odmowa połączenia", request=request)

    (result,) = _run(handler, CASES[:1])

    assert result["http_status"] is None and result["http_class"] == "transport"
    assert result["transport_error"].startswith("ConnectError")
    assert smoke.build_summary([result])["passed"] is False


def test_non_json_body_is_a_response_with_an_invalid_body() -> None:
    (result,) = _run(lambda request: httpx.Response(502, text="<html>Bad gateway</html>"), CASES[:1])

    assert result["http_status"] == 502 and result["body_valid"] is False and result["error_code"] is None


# --- Statystyki ---------------------------------------------------------------------


def _result(case_id: str, http: int | None, *, status: str | None = None, error: str | None = None,
            mpzp: int | None = None, pog: int | None = None, ms: float | None = 100.0, attempts: int = 1) -> dict[str, Any]:
    return {
        "case_id": case_id, "parcel_identifier": f"p-{case_id}", "http_status": http, "http_class": smoke.classify_http(http),
        "analysis_status": status, "error_code": error, "mpzp_zone_count": mpzp, "pog_zone_count": pog,
        "duration_ms": ms, "attempts": attempts, "warning_codes": [], "request_id": f"r-{case_id}", "transport_error": None,
    }


def test_percentile_is_linear_and_handles_empty_input() -> None:
    assert smoke.percentile([], 0.5) is None
    assert smoke.percentile([7], 0.95) == 7
    assert smoke.percentile([10, 20, 30, 40], 0.5) == 25.0
    assert smoke.percentile([0, 100], 0.95) == pytest.approx(95.0)


def test_summary_reproduces_the_audit_table_shape() -> None:
    results = [_result(f"ok{i}", 200, status="partial", mpzp=0, pog=0, ms=1000 + i) for i in range(20)]
    results += [_result("c", 200, status="complete", mpzp=1, pog=2, ms=500), _result("z", 200, status="partial", mpzp=1, pog=None, ms=700)]
    results += [_result(f"e{i}", 500, error="INTERNAL_ERROR", ms=50) for i in range(8)]

    summary = smoke.build_summary(results)

    assert summary["cases"] == 30
    assert summary["http_status_histogram"] == {"200": 22, "500": 8}
    assert summary["analysis_status_histogram"] == {"partial": 21, "complete": 1}
    assert summary["error_code_histogram"] == {"INTERNAL_ERROR": 8}
    assert (summary["successful"], summary["complete"], summary["five_xx"]) == (22, 1, 8)
    assert summary["mpzp"] == {"cases_with_zones": 2, "zones_total": 2, "cases_with_section": 22, "cases_without_section": 0}
    assert summary["pog"] == {"cases_with_zones": 1, "zones_total": 2, "cases_with_section": 21, "cases_without_section": 1}
    assert summary["passed"] is False
    assert summary["latency_ms"]["max"] == 1019 and summary["latency_ms"]["count"] == 30


def test_zero_five_xx_passes() -> None:
    assert smoke.build_summary([_result("a", 200, status="partial")])["passed"] is True


# --- Porównanie przebiegów ---------------------------------------------------------


def _report(results: list[dict[str, Any]], generated_at: str = "2026-10-01T00:00:00Z") -> dict[str, Any]:
    return smoke.build_report(
        results,
        metadata={"generated_at": generated_at, "commit_sha": "abc", "base_url": "http://x"},
        previous=None,
    )


def test_comparison_without_a_previous_run_is_marked_unavailable() -> None:
    assert smoke.compare_runs(None, _report([_result("a", 200, status="partial")])) == {"available": False}


def test_comparison_lists_new_regressions_fixes_and_other_changes() -> None:
    previous = _report([
        _result("fixed", 500, error="INTERNAL_ERROR"),
        _result("broke", 200, status="complete", mpzp=2, pog=1),
        _result("worse_status", 200, status="complete", mpzp=1),
        _result("fewer_zones", 200, status="partial", mpzp=3, pog=0),
        _result("more_zones", 200, status="partial", mpzp=0, pog=0),
        _result("same", 200, status="partial", mpzp=1, pog=1),
        _result("recode", 500, error="A"),
        _result("gone", 200, status="partial"),
        _result("sections_appear", 200, status="partial", mpzp=None, pog=None),
    ])
    current = _report([
        _result("fixed", 200, status="partial", mpzp=0),
        _result("broke", 503, error="PERSISTENCE_FAILED"),
        _result("worse_status", 200, status="partial", mpzp=1),
        _result("fewer_zones", 200, status="partial", mpzp=1, pog=0),
        _result("more_zones", 200, status="partial", mpzp=2, pog=0),
        _result("same", 200, status="partial", mpzp=1, pog=1),
        _result("recode", 500, error="B"),
        _result("new", 200, status="partial"),
        _result("sections_appear", 200, status="partial", mpzp=0, pog=0),
    ])

    comparison = smoke.compare_runs(previous, current)

    assert comparison["available"] is True
    brief = lambda items: sorted((item["case_id"], item["field"], item["before"], item["after"]) for item in items)  # noqa: E731
    assert brief(comparison["regressions"]) == [
        ("broke", "http_status", 200, 503),
        ("fewer_zones", "mpzp_zone_count", 3, 1),
        ("worse_status", "analysis_status", "complete", "partial"),
    ]
    assert brief(comparison["fixes"]) == [
        ("fixed", "http_status", 500, 200),
        ("more_zones", "mpzp_zone_count", 0, 2),
    ]
    assert brief(comparison["other_changes"]) == [
        ("recode", "error_code", "A", "B"),
        ("sections_appear", "mpzp_zone_count", None, 0),
        ("sections_appear", "pog_zone_count", None, 0),
    ]
    assert comparison["unchanged_cases"] == 1  # same
    assert comparison["added_cases"] == ["new"] and comparison["removed_cases"] == ["gone"]
    assert comparison["deltas"]["five_xx"] == {"before": 2, "after": 2}


def test_transport_failure_ranks_worse_than_a_server_error() -> None:
    previous = _report([_result("a", 500, error="X")])
    current = _report([_result("a", None, ms=None)])

    comparison = smoke.compare_runs(previous, current)

    assert [(c["field"], c["before"], c["after"]) for c in comparison["regressions"]] == [("http_status", 500, None)]


def test_find_previous_report_picks_the_newest_valid_one_and_skips_the_current(tmp_path: Path) -> None:
    older = _report([_result("a", 200, status="partial")], "2026-09-01T00:00:00Z")
    newer = _report([_result("a", 200, status="partial")], "2026-10-01T00:00:00Z")
    current = _report([_result("a", 200, status="partial")], "2026-10-08T00:00:00Z")
    for name, report in (("2026-09-01.json", older), ("2026-10-01.json", newer), ("2026-10-08.json", current)):
        (tmp_path / name).write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "zepsuty.json").write_text("{nie json", encoding="utf-8")
    (tmp_path / "obcy.json").write_text(json.dumps({"kind": "inny", "cases": []}), encoding="utf-8")

    path, report = smoke.find_previous_report(tmp_path, exclude=tmp_path / "2026-10-08.json")  # type: ignore[misc]

    assert path.name == "2026-10-01.json" and report["metadata"]["generated_at"] == "2026-10-01T00:00:00Z"
    assert smoke.find_previous_report(tmp_path / "brak") is None
    assert smoke.find_previous_report(tmp_path / "puste-dir-nie-istnieje", exclude=None) is None


def test_load_report_rejects_foreign_files(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text(json.dumps({"kind": "inny"}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")

    for name in ("x.json", "bad.json", "nie-ma.json"):
        with pytest.raises(smoke.SmokeError):
            smoke.load_report(tmp_path / name)


# --- Markdown -----------------------------------------------------------------------


def test_markdown_has_the_required_sections_and_hides_secrets() -> None:
    previous = _report([_result("a", 200, status="complete", mpzp=1, pog=1), _result("b", 500, error="INTERNAL_ERROR")])
    current_results = [_result("a", 500, error="INTERNAL_ERROR"), _result("b", 200, status="partial", mpzp=0, pog=0)]
    metadata = {
        "generated_at": "2026-10-08T10:00:00Z", "started_at": "2026-10-08T09:55:00Z", "finished_at": "2026-10-08T10:00:00Z",
        "run_date": "2026-10-08", "duration_seconds": 300.0, "base_url": "http://localhost:8000", "corpus_id": "BK-002",
        "manifest_sha256": "a" * 64, "commit_sha": "deadbeef", "concurrency": 3, "timeout_seconds": 180.0, "force_refresh": False,
    }

    report = smoke.build_report(current_results, metadata=metadata, previous=previous)
    markdown = smoke.render_markdown(report)

    for heading in (
        "# Live smoke korpusu referencyjnego — 2026-10-08", "**Wynik: FAIL**", "### Statusy HTTP", "### Status analizy",
        "### Kody błędów", "### Czasy odpowiedzi", "### Strefy planistyczne", "## Zmiany względem poprzedniego przebiegu",
        "### Nowe regresje (1)", "### Naprawy (1)", "## Wyniki per działka",
    ):
        assert heading in markdown, heading
    assert "| 1 | `a` | `p-a` | 500 |" in markdown
    assert "`INTERNAL_ERROR`" in markdown and "p50" in markdown and "p95" in markdown
    assert "TAJNYTOKEN" not in markdown and "access_token" not in markdown


def test_markdown_without_a_previous_run_says_so_and_pass_verdict() -> None:
    metadata = {
        "generated_at": "g", "started_at": "s", "finished_at": "f", "run_date": "2026-10-08", "duration_seconds": 1.0,
        "base_url": "http://x", "corpus_id": None, "manifest_sha256": "b" * 64, "commit_sha": "c", "concurrency": 3,
        "timeout_seconds": 5.0, "force_refresh": True,
    }

    markdown = smoke.render_markdown(smoke.build_report([_result("a", 200, status="partial", mpzp=None, ms=None)], metadata=metadata, previous=None))

    assert "**Wynik: PASS**" in markdown and "Brak poprzedniego przebiegu" in markdown
    assert "| 1 | `a` | `p-a` | 200 | partial | — | — | — | — | 1 |" in markdown


# --- Manifest i prawdziwy kontrakt API ------------------------------------------------


def test_real_manifest_yields_30_valid_parcel_identifiers() -> None:
    manifest, sha = smoke.load_manifest(smoke.DEFAULT_MANIFEST)
    cases = smoke.manifest_cases(manifest)

    assert len(cases) == 30 and len(sha) == 64
    assert len({case["case_id"] for case in cases}) == 30 and len({case["parcel_identifier"] for case in cases}) == 30
    for case in cases:
        ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=case["parcel_identifier"])  # to samo, co zwaliduje API
    assert len(smoke.manifest_cases(manifest, limit=4)) == 4


def test_manifest_errors_are_usage_errors(tmp_path: Path) -> None:
    (tmp_path / "bez-cases.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bez-id.json").write_text(json.dumps({"cases": [{"case_id": "x"}]}), encoding="utf-8")
    (tmp_path / "zly.json").write_text("{", encoding="utf-8")

    for name in ("bez-cases.json", "zly.json", "nie-ma.json"):
        with pytest.raises(smoke.SmokeError):
            smoke.load_manifest(tmp_path / name)
    manifest, _ = smoke.load_manifest(tmp_path / "bez-id.json")
    with pytest.raises(smoke.SmokeError):
        smoke.manifest_cases(manifest)


def test_the_script_reads_the_real_api_contract_through_the_whole_app_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prawdziwa aplikacja FastAPI (middleware, handlery błędów, serializacja ``AnalyzeResponse``)."""
    from app.core.settings import settings
    from app.services import uldk

    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    response = AnalyzeResponse(
        analysis_id=7, status="partial", analyzed_at=datetime(2026, 10, 8, tzinfo=UTC), mpzp_zones=[], pog=None,
        infrastructure=[], risks=[], warnings=[], sources=[],
    )

    async def fake_run_analysis(request, db, force_refresh=False):  # noqa: ANN001, ANN202
        identifier = request.parcel_identifier
        if identifier.endswith(".2"):
            raise uldk.ParcelNotFoundError("brak działki")
        if identifier.endswith(".3"):
            raise RuntimeError("awaria wewnętrzna z tajnym szczegółem")
        return response

    async def go() -> list[dict[str, Any]]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        return await smoke.run_smoke("http://testserver", CASES[:4], transport=transport, run_id="e2e")

    from app.db.session import get_db

    app.dependency_overrides[get_db] = lambda: None  # run_analysis jest podmienione — baza niepotrzebna
    try:
        with patch("app.routers.analyze.run_analysis", AsyncMock(side_effect=fake_run_analysis)):
            results = _by_id(asyncio.run(go()))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert results["c01"]["http_status"] == 200 and results["c01"]["analysis_status"] == "partial"
    assert results["c01"]["mpzp_zone_count"] == 0 and results["c01"]["pog_zone_count"] is None
    assert results["c02"]["http_status"] == 404 and results["c02"]["error_code"] == "PARCEL_NOT_FOUND"
    assert results["c03"]["http_status"] == 500 and results["c03"]["error_code"] == "INTERNAL_ERROR"
    assert results["c03"]["request_id"] == "live-smoke-e2e-003"  # serwer odsyła przekazany X-Request-ID
    assert "tajnym" not in json.dumps(results)
    assert "access_token" not in json.dumps(results)
    summary = smoke.build_summary(list(results.values()))
    assert summary["five_xx"] == 1 and summary["passed"] is False


# --- CLI ----------------------------------------------------------------------------


def _fake_run_smoke(statuses: dict[str, int | None]):
    async def fake(base_url, cases, **kwargs):  # noqa: ANN001, ANN202
        results = []
        for case in cases:
            code = statuses.get(case["case_id"], 200)
            results.append(
                _result(case["case_id"], code, status="partial" if code == 200 else None,
                        error="INTERNAL_ERROR" if code and code >= 500 else None, mpzp=0 if code == 200 else None,
                        pog=0 if code == 200 else None)
            )
            if kwargs.get("on_result"):
                kwargs["on_result"](results[-1])
        return results

    return fake


def _manifest(tmp_path: Path, count: int = 3) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"corpus_id": "T", "cases": [{"case_id": f"c{i}", "parcel_identifier": f"146510_8.0502.{i}"} for i in range(1, count + 1)]}),
        encoding="utf-8",
    )
    return path


def test_cli_writes_markdown_and_json_and_returns_zero_without_server_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({}))
    out = tmp_path / "wyniki" / "2026-10-08.md"

    code = smoke.main(["--manifest", str(_manifest(tmp_path)), "--output", str(out), "--base-url", "http://x:8000"])

    assert code == 0
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["kind"] == "live-smoke" and report["summary"]["passed"] is True and report["comparison"] == {"available": False}
    assert report["metadata"]["corpus_id"] == "T" and report["metadata"]["base_url"] == "http://x:8000"
    assert "Zmiany względem poprzedniego przebiegu" in out.read_text(encoding="utf-8")
    assert "5xx=0" in capsys.readouterr().out


@pytest.mark.parametrize("status", [500, 502, 503, None])
def test_cli_exit_code_is_nonzero_for_any_5xx_or_missing_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({"c2": status}))
    out = tmp_path / "r.md"

    code = smoke.main(["--manifest", str(_manifest(tmp_path)), "--output", str(out)])

    assert code == 1
    assert out.exists()  # raport powstaje także przy awarii


def test_cli_compares_with_the_latest_previous_report_in_the_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({"c1": 500}))
    assert smoke.main(["--manifest", str(manifest), "--output", str(tmp_path / "r" / "2026-10-01.md")]) == 1

    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({}))
    second = tmp_path / "r" / "2026-10-08.md"
    assert smoke.main(["--manifest", str(manifest), "--output", str(second), "--fail-on-regression"]) == 0

    comparison = json.loads(second.with_suffix(".json").read_text(encoding="utf-8"))["comparison"]
    assert comparison["available"] is True and len(comparison["fixes"]) == 1 and comparison["regressions"] == []
    assert "### Naprawy (1)" in second.read_text(encoding="utf-8")

    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({"c3": 404}))
    third = tmp_path / "r" / "2026-10-15.md"
    assert smoke.main(["--manifest", str(manifest), "--output", str(third)]) == 0  # 4xx nie jest 5xx
    assert smoke.main(["--manifest", str(manifest), "--output", str(third), "--fail-on-regression"]) == 1  # …ale to regresja


def test_cli_previous_none_and_explicit_previous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({}))
    first = tmp_path / "a.md"
    assert smoke.main(["--manifest", str(manifest), "--output", str(first)]) == 0

    skipped = tmp_path / "b.md"
    assert smoke.main(["--manifest", str(manifest), "--output", str(skipped), "--previous", "none"]) == 0
    assert json.loads(skipped.with_suffix(".json").read_text())["comparison"] == {"available": False}

    explicit = tmp_path / "c.md"
    assert smoke.main(["--manifest", str(manifest), "--output", str(explicit), "--previous", str(first.with_suffix(".json"))]) == 0
    assert json.loads(explicit.with_suffix(".json").read_text())["comparison"]["available"] is True


def test_cli_usage_errors_return_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(["--manifest", str(tmp_path / "nie-ma.json"), "--output", str(tmp_path / "o.md")]) == 2
    assert smoke.main(["--manifest", str(_manifest(tmp_path)), "--output", str(tmp_path / "o.md"), "--previous", str(tmp_path / "nie-ma.json")]) == 2
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert smoke.main(["--manifest", str(empty), "--output", str(tmp_path / "o.md")]) == 2
    assert "live smoke:" in capsys.readouterr().err


def test_cli_limit_runs_only_the_first_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    async def fake(base_url, cases, **kwargs):  # noqa: ANN001, ANN202
        seen.append(len(cases))
        return [_result(case["case_id"], 200, status="partial") for case in cases]

    monkeypatch.setattr(smoke, "run_smoke", fake)

    assert smoke.main(["--manifest", str(_manifest(tmp_path, 5)), "--output", str(tmp_path / "o.md"), "--limit", "2"]) == 0
    assert seen == [2]


def test_report_marks_a_dirty_worktree_next_to_the_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _fake_run_smoke({}))
    monkeypatch.setattr(smoke, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(smoke, "_worktree_dirty", lambda: True)
    out = tmp_path / "o.md"

    assert smoke.main(["--manifest", str(_manifest(tmp_path)), "--output", str(out)]) == 0

    assert "| Commit | `abc123` + niezacommitowane zmiany |" in out.read_text(encoding="utf-8")
    assert json.loads(out.with_suffix(".json").read_text())["metadata"]["worktree_dirty"] is True
    monkeypatch.setattr(smoke, "_worktree_dirty", lambda: False)
    assert smoke.main(["--manifest", str(_manifest(tmp_path)), "--output", str(out)]) == 0
    assert "niezacommitowane" not in out.read_text(encoding="utf-8")


def test_git_helpers_degrade_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise FileNotFoundError("git")

    monkeypatch.setattr(smoke.subprocess, "run", broken)
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    assert smoke._git_commit() == "unknown" and smoke._worktree_dirty() is None
    monkeypatch.setenv("GITHUB_SHA", "feedface")
    assert smoke._git_commit() == "feedface"


def test_default_output_path_is_dated_markdown() -> None:
    assert smoke.default_output_path("2026-10-08").name == "2026-10-08.md"
