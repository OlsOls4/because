import json

from because.harness.base import HarnessError, SourceSpec
from because.harness.duckdb_backend import DuckDBBackend
from because.harness.sources import zetaris_ddl, zetaris_file_ddl
from because.harness import zetaris_backend as zb
from because.harness.zetaris_backend import ZetarisBackend, _norm_col, _parse_ui_result, _ui_org_id, _ui_token


def loader(spec):
    if spec.kind == "edgar_concept":
        return [("50863", "INTEL CORP", spec.key.split(":")[1], "2023-01-01", "2023-12-30", 100.0, "a1", 2023, "FY", "10-K", "2024-01-26"),
                ("50863", "INTEL CORP", spec.key.split(":")[1], "2024-01-01", "2024-12-28", 90.0, "a2", 2024, "FY", "10-K", "2025-01-31"),
                ("50863", "INTEL CORP", spec.key.split(":")[1], "2024-07-01", "2024-09-28", 20.0, "a3", 2024, "Q3", "10-Q", "2024-10-31")]
    return [(spec.key, "2022-01", 1.0), (spec.key, "2022-02", 2.0)]


SQL = "SELECT period_end, value_usd FROM {t} WHERE form_type = '10-K' AND fiscal_period = 'FY' ORDER BY period_end"


def test_duckdb_portable_sql_and_provenance():
    h = DuckDBBackend(row_loader=loader)
    spec = SourceSpec("edgar_concept", "0000050863:GrossProfit")
    r = h.query(SQL, {"t": spec})
    assert r.error is None and r.columns == ["period_end", "value_usd"] and len(r.rows) == 2
    assert r.query_id.startswith("Q-") and r.sources[0]["url"].endswith("CIK0000050863/us-gaap/GrossProfit.json")
    assert h.query(SQL, {"t": spec}).query_id == r.query_id  # stable id: replayable citation


def test_series_sql():
    h = DuckDBBackend(row_loader=loader)
    r = h.query("SELECT period, value FROM {t} WHERE period >= '2022-01' ORDER BY period", {"t": SourceSpec("ons_series", "D7G7")})
    assert [x[0] for x in r.rows] == ["2022-01", "2022-02"]


def test_zetaris_ddl_follows_cookbook():
    edgar = zetaris_ddl(SourceSpec("edgar_concept", "0000050863:GrossProfit"))
    assert edgar[0].startswith("CREATE LIGHTNING REST TABLE") and 'response_type "json"' in edgar[0] and "user-agent" in edgar[0]
    assert "CREATE SCHEMASTORE VIEW" in edgar[1] and "LATERAL VIEW explode(units.USD) AS fact" in edgar[1]
    ons = zetaris_ddl(SourceSpec("ons_series", "D7G7"))
    assert "explode(months)" in ons[1] and "CASE m.month" in ons[1]


def test_zetaris_file_ddl_fred_csv():
    stmts = zetaris_file_ddl(SourceSpec("fred_series", "UNRATE"), "file:///data/fred/UNRATE.csv")
    assert stmts[0].startswith("CREATE LIGHTNING FILESTORE TABLE fred_series_unrate_raw FROM BECAUSE_FILES")
    assert 'path "file:///data/fred/UNRATE.csv"' in stmts[0]
    assert 'header "true"' in stmts[0] and 'inferSchema "false"' in stmts[0]
    assert "CREATE SCHEMASTORE VIEW fred_series_unrate" in stmts[1]
    assert "`observation_date`" in stmts[1] and "`value`" in stmts[1]
    assert "because_files.fred_series_unrate_raw" in stmts[1]
    assert "WHERE `value` <> '.'" in stmts[1]


class FakeChannel:
    name = "fake"

    def __init__(self):
        self.sql = []
        self.created = set()
        self.fail_cache = False
        self.fail_filestore = False

    def run(self, sql):
        self.sql.append(sql)
        if sql.startswith("SELECT 1 FROM because."):
            if sql.split()[3] not in self.created:
                raise RuntimeError("Table or view not found")
            return ["1"], [[1]]
        if sql.startswith("CACHE TABLE") and self.fail_cache:
            raise RuntimeError("cache not supported")
        if sql.startswith("CREATE LIGHTNING FILESTORE") and self.fail_filestore:
            raise RuntimeError("filestore unavailable")
        if sql.startswith("CREATE SCHEMASTORE VIEW"):
            self.created.add("because." + sql.split()[3])
        if sql.startswith("SELECT period_end"):
            return ["period_end", "value_usd"], [["2024-12-28", 90.0]]
        if sql.startswith("SELECT period"):
            return ["period", "value"], [["2020-01", 3.5], ["2020-04", 14.8]]
        if sql.startswith("SELECT 1 AS"):
            return ["one"], [[1]]
        if sql.startswith("SHOW LIGHTNING"):
            return ["database"], [["BECAUSE_REST"]]
        return [], []

    def close(self):
        pass


def _zetaris_with_fake(monkeypatch, channel=None):
    monkeypatch.setattr("because.harness.sources._get", lambda url, ttl_s=86400: b"{}")
    z = ZetarisBackend.__new__(ZetarisBackend)
    from because.harness.base import DataHarness

    DataHarness.__init__(z)
    z.channel = channel or FakeChannel()
    z.name = f"zetaris-{z.channel.name}"
    z.ddl_log, z._bootstrapped, z._local, z._absent = [], False, None, set()
    z._file_db_bootstrapped, z._file_store_failed = False, set()
    return z


def test_zetaris_backend_registers_then_queries(monkeypatch):
    z = _zetaris_with_fake(monkeypatch)
    spec = SourceSpec("edgar_concept", "0000050863:GrossProfit")
    r = z.query(SQL, {"t": spec})
    kinds = [s.split()[0:3] for s in z.channel.sql]
    assert ["CREATE", "LIGHTNING", "DATABASE"] in kinds and ["CREATE", "SCHEMASTORE", "CONTAINER"] in kinds
    assert ["CACHE", "TABLE", "because_rest.edgar_concept_0000050863_grossprofit_raw"] in kinds
    rest_i = next(i for i, s in enumerate(z.channel.sql) if s.startswith("CREATE LIGHTNING REST TABLE"))
    cache_i = next(i for i, s in enumerate(z.channel.sql) if s.startswith("CACHE TABLE"))
    view_i = next(i for i, s in enumerate(z.channel.sql) if s.startswith("CREATE SCHEMASTORE VIEW"))
    assert rest_i < cache_i < view_i
    assert r.rows == [["2024-12-28", 90.0]] and "because.edgar_concept_0000050863_grossprofit" in r.sql
    n = len(z.channel.sql)
    z._registered.clear()
    z.query(SQL, {"t": spec})  # second session: view exists -> reused, nothing re-created
    assert not any(s.startswith("CREATE") for s in z.channel.sql[n:])


def test_zetaris_cache_skip_on_failure(monkeypatch):
    ch = FakeChannel()
    ch.fail_cache = True
    z = _zetaris_with_fake(monkeypatch, ch)
    spec = SourceSpec("edgar_concept", "0000050863:GrossProfit")
    r = z.query(SQL, {"t": spec})
    assert r.error is None and r.rows
    assert any(line.startswith("-- cache skipped:") for line in z.ddl_log)
    assert any(s.startswith("CREATE SCHEMASTORE VIEW") for s in z.channel.sql)


def test_zetaris_cache_disabled(monkeypatch):
    monkeypatch.setenv("BECAUSE_ZETARIS_CACHE", "0")
    z = _zetaris_with_fake(monkeypatch)
    z.query(SQL, {"t": SourceSpec("edgar_concept", "0000050863:GrossProfit")})
    assert not any(s.startswith("CACHE TABLE") for s in z.channel.sql)


def test_zetaris_absent_edgar_404(monkeypatch):
    def boom(url, ttl_s=86400):
        raise HarnessError(f"404 for {url}")

    z = _zetaris_with_fake(monkeypatch)
    monkeypatch.setattr("because.harness.sources._get", boom)  # after the helper's default stub
    spec = SourceSpec("edgar_concept", "0000050863:NeverReportedTag")
    r = z.query(SQL, {"t": spec})
    assert r.error is None and r.rows == [] and "period_end" in r.columns
    assert z.log[-1].get("note") == "source returned 404 (concept not reported); empty table"
    assert not any(s.startswith("CREATE LIGHTNING REST TABLE") for s in z.channel.sql)


def test_zetaris_probe_other_error_still_registers(monkeypatch):
    def boom(url, ttl_s=86400):
        raise HarnessError("503 upstream")

    monkeypatch.setattr("because.harness.sources._get", boom)
    z = _zetaris_with_fake(monkeypatch)
    r = z.query(SQL, {"t": SourceSpec("edgar_concept", "0000050863:GrossProfit")})
    assert r.error is None and r.rows
    assert any(s.startswith("CREATE LIGHTNING REST TABLE") for s in z.channel.sql)


def test_zetaris_hybrid_routes_fred_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("BECAUSE_ZETARIS_FILE_DIR", raising=False)
    z = _zetaris_with_fake(monkeypatch)
    z._local = DuckDBBackend(row_loader=loader)
    z._local.name = "duckdb (hybrid: FRED without key)"
    r = z.query("SELECT period, value FROM {t} ORDER BY period", {"t": SourceSpec("fred_series", "UNRATE")})
    assert r.backend.startswith("duckdb (hybrid") and len(r.rows) == 2 and not z.channel.sql


def test_zetaris_fred_filestore_registers_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_URI", "file:///container/because")
    monkeypatch.setattr(
        "because.harness.zetaris_backend.fetch_rows",
        lambda spec: [(spec.key, "2020-01-01", 3.5), (spec.key, "2020-04-01", 14.8)],
    )
    z = _zetaris_with_fake(monkeypatch)
    spec = SourceSpec("fred_series", "UNRATE")
    r = z.query("SELECT period, value FROM {t} ORDER BY period", {"t": spec})
    assert r.error is None and r.backend.startswith("zetaris") and len(r.rows) == 2
    csv_path = tmp_path / "fred" / "UNRATE.csv"
    assert csv_path.is_file() and oct(csv_path.stat().st_mode)[-3:] == "644"
    text = csv_path.read_text()
    assert text.splitlines()[0] == "observation_date,value"
    assert any(s.startswith("CREATE LIGHTNING DATABASE BECAUSE_FILES") for s in z.channel.sql)
    assert any(s.startswith("CREATE LIGHTNING FILESTORE TABLE") for s in z.channel.sql)
    assert any('path "file:///container/because/fred/UNRATE.csv"' in s for s in z.channel.sql)
    assert not any(s.startswith("CACHE TABLE") for s in z.channel.sql)
    assert not any(s.startswith("CREATE LIGHTNING REST TABLE") for s in z.channel.sql)


def test_zetaris_fred_filestore_failure_falls_back_to_hybrid(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("BECAUSE_ZETARIS_HYBRID", "1")
    monkeypatch.setattr(
        "because.harness.zetaris_backend.fetch_rows",
        lambda spec: [(spec.key, "2020-01-01", 1.0)],
    )
    ch = FakeChannel()
    ch.fail_filestore = True
    z = _zetaris_with_fake(monkeypatch, ch)
    z._local = DuckDBBackend(row_loader=loader)
    z._local.name = "duckdb (hybrid: FRED without key)"
    spec = SourceSpec("fred_series", "UNRATE")
    r = z.query("SELECT period, value FROM {t} ORDER BY period", {"t": spec})
    assert r.backend.startswith("duckdb (hybrid") and len(r.rows) == 2
    assert spec in z._file_store_failed
    assert any(line.startswith("-- file-store failed for UNRATE:") for line in z.ddl_log)


def test_zetaris_fred_filestore_failure_surfaces_when_hybrid_off(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("BECAUSE_ZETARIS_HYBRID", "0")
    monkeypatch.setattr(
        "because.harness.zetaris_backend.fetch_rows",
        lambda spec: [(spec.key, "2020-01-01", 1.0)],
    )
    ch = FakeChannel()
    ch.fail_filestore = True
    z = _zetaris_with_fake(monkeypatch, ch)
    r = z.query("SELECT period, value FROM {t} ORDER BY period", {"t": SourceSpec("fred_series", "UNRATE")})
    assert r.error and "register:" in r.error
    assert any(line.startswith("-- file-store failed for UNRATE:") for line in z.ddl_log)


def test_norm_col_strips_hive_qualifiers():
    assert _norm_col("period_end") == "period_end"
    assert _norm_col("view.Period_End") == "period_end"
    assert _norm_col("because.view.value_usd") == "value_usd"


def test_rest_channel_normalises_columns(monkeypatch):
    monkeypatch.setenv("ZETARIS_API_KEY", "k")

    class Resp:
        def read(self):
            return json.dumps({"data": [{"because.view.Period_End": "2024-01-01", "Value_USD": 1}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(zb, "urlopen", lambda req, timeout=None: Resp())
    cols, rows = zb._RestChannel().run("SELECT 1")
    assert cols == ["period_end", "value_usd"] and rows == [["2024-01-01", 1]]


def test_ui_token_and_org_parsing():
    jwt = "aaaaaaaa.bbbbbbbb.cccccccc"
    assert _ui_token({"data": {"idToken": "tok-1"}}) == "tok-1"
    assert _ui_token({"nested": {"accessToken": "tok-2"}}) == "tok-2"
    assert _ui_token({"msg": f"bearer {jwt}"}) == jwt
    assert _ui_org_id({"user": {"orgId": 42}}) == 42
    assert _ui_org_id({"x": 1}) is None


def test_parse_ui_result_shapes():
    cols, rows = _parse_ui_result({"data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]})
    assert cols == ["a", "b"] and rows == [[1, 2], [3, 4]]
    cols, rows = _parse_ui_result({"result": {"columns": ["x", {"name": "y"}], "rows": [[9, 8]]}})
    assert cols == ["x", "y"] and rows == [[9, 8]]
    assert _parse_ui_result({"ok": True}) == ([], [])
    assert _parse_ui_result({"data": []}) == ([], [])


def test_ui_channel_login_and_query(monkeypatch):
    monkeypatch.setenv("ZETARIS_USERNAME", "admin")
    monkeypatch.setenv("ZETARIS_PASSWORD", "secret")
    monkeypatch.setenv("ZETARIS_UI_URL", "http://ui.test")
    calls = []

    class Resp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        data = req.data.decode() if req.data else ""
        hdrs = {k.lower(): v for k, v in req.headers.items()}
        assert "secret" not in str(hdrs)
        if url.endswith("/api/auth/login"):
            assert json.loads(data)["username"] == "admin"
            return Resp({"idToken": "hdr.payload.sig", "orgId": 7})
        assert "authorization" in hdrs and hdrs["authorization"].startswith("Bearer ")
        assert "secret" not in hdrs["authorization"]
        return Resp({"data": [{"One": 1}]})

    monkeypatch.setattr(zb, "urlopen", fake_urlopen)
    ch = zb._UiChannel()
    assert ch.org == "7"
    cols, rows = ch.run("SELECT 1 AS one")
    assert cols == ["one"] and rows == [[1]]
    assert any("/api/auth/login" in u for u in calls)
    assert any("run-query" in u for u in calls)


def test_auto_channel_falls_back_to_ui(monkeypatch):
    class BoomJdbc:
        name = "jdbc"

        def __init__(self):
            raise HarnessError("jdbc unavailable")

    class OkUi:
        name = "ui"

        def __init__(self):
            pass

        def run(self, sql):
            return ["one"], [[1]]

        def close(self):
            pass

    monkeypatch.setenv("ZETARIS_CHANNEL", "auto")
    monkeypatch.setattr(zb, "_JdbcChannel", BoomJdbc)
    monkeypatch.setattr(zb, "_UiChannel", OkUi)
    z = ZetarisBackend()
    assert z.channel.name == "ui" and z.name == "zetaris-ui"


def test_auto_channel_both_fail(monkeypatch):
    class BoomJdbc:
        def __init__(self):
            raise HarnessError("jdbc down")

    class BoomUi:
        def __init__(self):
            raise HarnessError("ui down")

    monkeypatch.setenv("ZETARIS_CHANNEL", "auto")
    monkeypatch.setattr(zb, "_JdbcChannel", BoomJdbc)
    monkeypatch.setattr(zb, "_UiChannel", BoomUi)
    try:
        ZetarisBackend()
        assert False, "expected HarnessError"
    except HarnessError as e:
        msg = str(e).lower()
        assert "jdbc" in msg and "ui" in msg


def test_ping_includes_channel(monkeypatch):
    z = _zetaris_with_fake(monkeypatch)
    out = z.ping()
    assert out["channel"] == "fake" and out["select_1"] == 1


def test_warm_specs_keeps_fred_when_file_dir(monkeypatch):
    from because.cli import warm_specs

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("BECAUSE_ZETARIS_FILE_DIR", raising=False)
    assert not any(s.kind == "fred_series" for s in warm_specs())
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_DIR", "/tmp/because-files")
    assert any(s.kind == "fred_series" for s in warm_specs())


def test_zetaris_ddl_cli_prints_filestore(monkeypatch, capsys):
    from because import cli

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("BECAUSE_ZETARIS_FILE_DIR", "/tmp/because-files")
    monkeypatch.setattr(
        cli,
        "warm_specs",
        lambda: [SourceSpec("fred_series", "UNRATE"), SourceSpec("ons_series", "D7G7")],
    )
    assert cli.main(["zetaris", "ddl"]) == 0
    out = capsys.readouterr().out
    assert "CREATE LIGHTNING FILESTORE TABLE" in out
    assert "fred/UNRATE.csv" in out
    assert "CREATE LIGHTNING REST TABLE" in out  # ONS still REST


def test_zetaris_sql_cli(monkeypatch, capsys):
    from because import cli
    import because.harness.zetaris_backend as zmod

    ch = FakeChannel()

    class FakeZ:
        def __init__(self):
            self.channel = ch

    monkeypatch.setattr(zmod, "ZetarisBackend", FakeZ)
    assert cli.main(["zetaris", "sql", "SHOW LIGHTNING DATABASES"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["columns"] == ["database"] and payload["rows"] == [["BECAUSE_REST"]]
    assert ch.sql == ["SHOW LIGHTNING DATABASES"]


def test_zetaris_sql_cli_missing_statement(capsys):
    from because import cli

    assert cli.main(["zetaris", "sql"]) == 1
    assert "usage:" in capsys.readouterr().err.lower()
