"""Vercel (zero-config FastAPI) entry point for the public demo https://because-agent.vercel.app (SAMPLE_MODE: DuckDB over the same public sources).

Saved runs (examples/outputs, examples/zetaris_runs) load instantly; live questions run the full agent.
"""
import os

os.environ.setdefault("SAMPLE_MODE", "1")
os.environ.setdefault("BECAUSE_HOME", "/tmp/.because")  # the only writable path on Vercel
os.environ.setdefault("BECAUSE_GDELT", "0")

from because.web.app import app  # noqa: E402,F401
