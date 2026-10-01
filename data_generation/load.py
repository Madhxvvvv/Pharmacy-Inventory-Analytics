"""Create the pharma_inventory schema in MySQL 8 and load all CSVs from data/.

Re-runnable: sql/01_schema.sql drops and recreates every table.

Connection settings (environment variables):
  MYSQL_PWD   password (required)
  MYSQL_HOST  default: localhost
  MYSQL_PORT  default: 3306
  MYSQL_USER  default: root

Run:  python data_generation/load.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import mysql.connector
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "sql" / "01_schema.sql"
DATA = ROOT / "data"
DATABASE = "pharma_inventory"
BATCH_SIZE = 2000

# parents before children so foreign keys resolve
LOAD_ORDER = ["dim_product", "dim_warehouse", "dim_date", "dim_batch", "fact_inventory_txn", "fact_daily_demand"]


def schema_statements(sql: str) -> list[str]:
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def connect():
    password = os.environ.get("MYSQL_PWD")
    if password is None:
        sys.exit("MYSQL_PWD environment variable is not set.")
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=password,
    )


def load_table(cur, table: str) -> int:
    df = pd.read_csv(DATA / f"{table}.csv")
    cols = ", ".join(f"`{c}`" for c in df.columns)
    placeholders = ", ".join(["%s"] * len(df.columns))
    sql = f"INSERT INTO `{table}` ({cols}) VALUES ({placeholders})"
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in df.itertuples(index=False, name=None)]
    for i in range(0, len(rows), BATCH_SIZE):
        cur.executemany(sql, rows[i:i + BATCH_SIZE])
    return len(rows)


def main():
    conn = connect()
    cur = conn.cursor()
    try:
        print(f"Creating schema from {SCHEMA_FILE.relative_to(ROOT)}")
        for stmt in schema_statements(SCHEMA_FILE.read_text(encoding="utf-8")):
            cur.execute(stmt)
        cur.execute(f"USE {DATABASE}")

        for table in LOAD_ORDER:
            n = load_table(cur, table)
            conn.commit()
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            (count,) = cur.fetchone()
            status = "ok" if count == n else f"MISMATCH (csv {n:,})"
            print(f"  {table:<20} {count:>7,} rows  {status}")
    except mysql.connector.Error as e:
        conn.rollback()
        sys.exit(f"MySQL error: {e}")
    finally:
        cur.close()
        conn.close()
    print(f"Loaded into database '{DATABASE}'.")


if __name__ == "__main__":
    main()
