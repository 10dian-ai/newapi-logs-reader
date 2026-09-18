"""Compatibility entrypoint using the official NewAPI env names."""
import os
import runpy

if os.getenv("SQL_DSN") and not os.getenv("DB_DSN"):
    os.environ["DB_DSN"] = os.environ["SQL_DSN"]
if os.getenv("LOG_SQL_DSN") and not os.getenv("LOG_DB_DSN"):
    os.environ["LOG_DB_DSN"] = os.environ["LOG_SQL_DSN"]
runpy.run_module("server_v2", run_name="__main__")
