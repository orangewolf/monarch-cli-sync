"""Tests for the SyncStatus / SyncResult model."""

from monarch_cli_sync.status import SyncResult, SyncStatus


def test_exit_code_ok():
    r = SyncResult(status=SyncStatus.OK)
    assert r.exit_code == 0


def test_exit_code_no_changes():
    r = SyncResult(status=SyncStatus.NO_CHANGES)
    assert r.exit_code == 0


def test_exit_code_partial():
    r = SyncResult(status=SyncStatus.PARTIAL)
    assert r.exit_code == 1


def test_exit_code_auth_required():
    r = SyncResult(status=SyncStatus.AUTH_REQUIRED)
    assert r.exit_code == 2


def test_exit_code_rate_limited():
    r = SyncResult(status=SyncStatus.RATE_LIMITED)
    assert r.exit_code == 3


def test_exit_code_error():
    r = SyncResult(status=SyncStatus.ERROR)
    assert r.exit_code == 4


def test_summary_line_format():
    r = SyncResult(status=SyncStatus.OK, matched=5, updated=5, skipped=1)
    line = r.summary_line()
    assert "monarch-cli-sync:" in line
    assert "matched=5" in line
    assert "updated=5" in line
    assert "skipped=1" in line
    assert "errors=0" in line


def test_errors_list_default_empty():
    r = SyncResult(status=SyncStatus.OK)
    assert r.errors == []
    assert r.warnings == []


def test_errors_count_in_summary():
    r = SyncResult(status=SyncStatus.PARTIAL, errors=["boom", "bang"])
    assert "errors=2" in r.summary_line()


def test_to_dict_roundtrip():
    r = SyncResult(
        status=SyncStatus.PARTIAL,
        orders_inspected=10,
        transactions_fetched=5,
        matched=3,
        updated=2,
        skipped=1,
        errors=["err1"],
        warnings=["warn1"],
        message="partial run",
    )
    d = r.to_dict()
    assert d["status"] == "partial"
    assert d["orders_inspected"] == 10
    assert d["matched"] == 3
    assert d["errors"] == ["err1"]

    r2 = SyncResult.from_dict(d)
    assert r2.status == SyncStatus.PARTIAL
    assert r2.orders_inspected == 10
    assert r2.matched == 3
    assert r2.updated == 2
    assert r2.skipped == 1
    assert r2.errors == ["err1"]
    assert r2.warnings == ["warn1"]
    assert r2.message == "partial run"


def test_from_dict_defaults_missing_fields():
    d = {"status": "ok"}
    r = SyncResult.from_dict(d)
    assert r.status == SyncStatus.OK
    assert r.matched == 0
    assert r.errors == []


def test_to_dict_status_is_string():
    r = SyncResult(status=SyncStatus.ERROR)
    assert isinstance(r.to_dict()["status"], str)
