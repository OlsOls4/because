import os

from because.harness.base import SourceSpec
from because.harness.duckdb_backend import DuckDBBackend
from because.harness.sources import zetaris_ddl
from because.harness.zetaris_backend import ZetarisBackend


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


class FakeChannel:
    name = "fake"

    def __init__(self):
        self.sql = []
        self.created = set()

    def run(self, sql):
        self.sql.append(sql)
        if sql.startswith("SELECT 1 FROM because."):
            if sql.split()[3] not in self.created:
                raise RuntimeError("Table or view not found")
            return ["1"], [[1]]
        if sql.startswith("CREATE SCHEMASTORE VIEW"):
            self.created.add("because." + sql.split()[3])
        if sql.startswith("SELECT period_end"):
            return ["period_end", "value_usd"], [["2024-12-28", 90.0]]
        return [], []

    def close(self):
        pass


def test_zetaris_backend_registers_then_queries():
    z = ZetarisBackend.__new__(ZetarisBackend)
    from because.harness.base import DataHarness

    DataHarness.__init__(z)
    z.channel, z.name, z.ddl_log, z._bootstrapped, z._local = FakeChannel(), "zetaris-fake", [], False, None
    spec = SourceSpec("edgar_concept", "0000050863:GrossProfit")
    r = z.query(SQL, {"t": spec})
    kinds = [s.split()[0:3] for s in z.channel.sql]
    assert ["CREATE", "LIGHTNING", "DATABASE"] in kinds and ["CREATE", "SCHEMASTORE", "CONTAINER"] in kinds
    assert r.rows == [["2024-12-28", 90.0]] and "because.edgar_concept_0000050863_grossprofit" in r.sql
    n = len(z.channel.sql)
    z._registered.clear()
    z.query(SQL, {"t": spec})  # second session: view exists -> reused, nothing re-created
    assert not any(s.startswith("CREATE") for s in z.channel.sql[n:])


def test_zetaris_hybrid_routes_fred_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    z = ZetarisBackend.__new__(ZetarisBackend)
    from because.harness.base import DataHarness

    DataHarness.__init__(z)
    z.channel, z.name, z.ddl_log, z._bootstrapped = FakeChannel(), "zetaris-fake", [], False
    z._local = DuckDBBackend(row_loader=loader)
    z._local.name = "duckdb (hybrid: FRED without key)"
    r = z.query("SELECT period, value FROM {t} ORDER BY period", {"t": SourceSpec("fred_series", "UNRATE")})
    assert r.backend.startswith("duckdb (hybrid") and len(r.rows) == 2 and not z.channel.sql
