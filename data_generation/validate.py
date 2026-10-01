"""Validate the generated CSVs in data/ and print a summary.

Checks
  1. No batch ever goes negative (running balance in txn_id order).
  2. For every product x warehouse, sum(qty_change) = closing stock, where closing
     stock is the sum of batch closing balances; also cross-checks that
     PURCHASE qty = quantity_received and SALE units = fulfilled demand.
  3. fulfilled_qty + unmet_qty = demand_qty (and none negative).
  4. Every foreign key exists in its dimension.

Run:  python data_generation/validate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
AS_OF = pd.Timestamp("2026-09-30")
SLOW_MOVERS = ["P006", "P017", "P019"]
SPIKE_PRODUCT = "P001"
UNDERSUPPLIED_WH = "W04"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def load():
    t = {n: pd.read_csv(DATA / f"{n}.csv") for n in
         ["dim_product", "dim_warehouse", "dim_date", "dim_batch", "fact_inventory_txn", "fact_daily_demand"]}
    for col in ["manufacture_date", "expiry_date", "purchase_date"]:
        t["dim_batch"][col] = pd.to_datetime(t["dim_batch"][col])
    t["fact_inventory_txn"]["txn_date"] = pd.to_datetime(t["fact_inventory_txn"]["txn_date"])
    return t


def main():
    t = load()
    prod, wh, dates, batch = t["dim_product"], t["dim_warehouse"], t["dim_date"], t["dim_batch"]
    txn, dem = t["fact_inventory_txn"], t["fact_daily_demand"]

    print("Row counts")
    for name, df in t.items():
        print(f"  {name:<20} {len(df):>7,}")

    print("\nChecks")
    # 1. batch balances never negative ------------------------------------------
    txn = txn.sort_values("txn_id")
    check("txn_id order is chronological", txn["txn_date"].is_monotonic_increasing)
    running = txn.groupby("batch_id")["qty_change"].cumsum()
    neg = txn[running < 0]
    check("1. no batch balance ever negative", neg.empty, f"{len(neg)} negative points, e.g. {neg.head(3).to_dict('records')}")

    signs_ok = (
        ((txn.txn_type.isin(["PURCHASE", "RETURN"])) & (txn.qty_change > 0))
        | ((txn.txn_type.isin(["SALE", "EXPIRED"])) & (txn.qty_change < 0))
    )
    check("qty_change sign matches txn_type", signs_ok.all(), f"{(~signs_ok).sum()} rows")
    check("txn_value = qty_change * unit_cost",
          ((txn.qty_change * txn.unit_cost).round(2) - txn.txn_value).abs().max() < 0.01)

    batch_close = txn.groupby("batch_id")["qty_change"].sum().rename("closing")
    b = batch.set_index("batch_id").join(batch_close)
    expired_before = b[b.expiry_date <= AS_OF]
    check("batches past expiry hold zero stock", (expired_before.closing == 0).all(),
          f"{(expired_before.closing != 0).sum()} batches")
    late = txn.merge(batch[["batch_id", "expiry_date"]], on="batch_id")
    late = late[(late.txn_type != "EXPIRED") & (late.txn_date >= late.expiry_date)]
    check("no movements on/after expiry other than the write-off", late.empty, f"{len(late)} rows")

    # 2. product x warehouse ledger = closing stock -----------------------------
    pw_ledger = txn.groupby(["product_id", "warehouse_id"])["qty_change"].sum()
    pw_close = b.groupby(["product_id", "warehouse_id"])["closing"].sum()
    check("2. sum(qty_change) = closing stock for every product x warehouse",
          pw_ledger.sort_index().equals(pw_close.sort_index()) and len(pw_ledger) == len(prod) * len(wh))
    purchases = txn[txn.txn_type == "PURCHASE"].groupby("batch_id")["qty_change"].sum()
    check("   PURCHASE qty = dim_batch.quantity_received (one per batch)",
          purchases.reindex(batch.batch_id).fillna(-1).values.tolist() == batch.quantity_received.tolist()
          and txn[txn.txn_type == "PURCHASE"].batch_id.is_unique)
    sold = -txn[txn.txn_type == "SALE"].groupby(["product_id", "warehouse_id"])["qty_change"].sum()
    fulfilled = dem.groupby(["product_id", "warehouse_id"])["fulfilled_qty"].sum()
    check("   SALE units = fulfilled demand for every product x warehouse",
          sold.reindex(fulfilled.index).fillna(0).astype(int).equals(fulfilled.astype(int)))

    # 3. demand arithmetic ------------------------------------------------------
    check("3. fulfilled_qty + unmet_qty = demand_qty",
          (dem.fulfilled_qty + dem.unmet_qty == dem.demand_qty).all()
          and (dem[["demand_qty", "fulfilled_qty", "unmet_qty"]] >= 0).all().all())
    check("   one demand row per date x product x warehouse",
          len(dem) == len(dates) * len(prod) * len(wh)
          and not dem.duplicated(["date_key", "product_id", "warehouse_id"]).any())

    # 4. foreign keys -------------------------------------------------------------
    def fk(child, col, parent, pcol, label):
        missing = ~child[col].isin(parent[pcol])
        check(f"4. FK {label}", not missing.any(), f"{missing.sum()} orphans")

    fk(batch, "product_id", prod, "product_id", "dim_batch.product_id")
    fk(batch, "warehouse_id", wh, "warehouse_id", "dim_batch.warehouse_id")
    fk(txn, "date_key", dates, "date_key", "fact_inventory_txn.date_key")
    fk(txn, "product_id", prod, "product_id", "fact_inventory_txn.product_id")
    fk(txn, "warehouse_id", wh, "warehouse_id", "fact_inventory_txn.warehouse_id")
    fk(txn, "batch_id", batch, "batch_id", "fact_inventory_txn.batch_id")
    fk(dem, "date_key", dates, "date_key", "fact_daily_demand.date_key")
    fk(dem, "product_id", prod, "product_id", "fact_daily_demand.product_id")
    fk(dem, "warehouse_id", wh, "warehouse_id", "fact_daily_demand.warehouse_id")
    tb = txn.merge(batch, on="batch_id", suffixes=("", "_b"))
    check("   txn product/warehouse matches its batch",
          ((tb.product_id == tb.product_id_b) & (tb.warehouse_id == tb.warehouse_id_b)).all())

    # batch attribute rules -------------------------------------------------------
    shelf = batch.merge(prod[["product_id", "shelf_life_days"]], on="product_id")
    check("expiry_date = manufacture_date + shelf_life_days",
          ((shelf.expiry_date - shelf.manufacture_date).dt.days == shelf.shelf_life_days).all())
    age = (batch.purchase_date - batch.manufacture_date).dt.days
    check("manufacture_date 1-4 months before purchase_date", age.between(30, 120).all(),
          f"range {age.min()}-{age.max()}")

    # Summary -------------------------------------------------------------------
    print("\nTransactions by type")
    summary = txn.groupby("txn_type").agg(rows=("txn_id", "size"), units=("qty_change", "sum"),
                                          value_inr=("txn_value", "sum"))
    print(summary.to_string(float_format=lambda x: f"{x:,.0f}"))
    sold_units = -summary.loc["SALE", "units"]
    print(f"  returns = {summary.loc['RETURN', 'units'] / sold_units:.1%} of units sold")

    print("\nPlanted scenario 1 - slow movers with stock expiring 30-90 days after as-of")
    names = prod.set_index("product_id").product_name
    live = b[(b.closing > 0)].copy()
    live["days_to_expiry"] = (live.expiry_date - AS_OF).dt.days
    last90 = dem[dem.date_key > 20260701].groupby("product_id").fulfilled_qty.sum() / 91
    for p in SLOW_MOVERS:
        lp = live[live.product_id == p]
        soon = lp[lp.days_to_expiry.between(30, 90)].closing.sum()
        print(f"  {p} {names[p]}: closing {lp.closing.sum():,} units, {soon:,} expiring in 30-90d "
              f"(~{lp.closing.sum() / last90[p]:.0f} days of cover at current sales)")

    print("\nPlanted scenario 2 - stock-out days (product x warehouse days with unmet demand)")
    so = dem.assign(stockout=dem.unmet_qty > 0).groupby("warehouse_id").agg(
        stockout_days=("stockout", "sum"), stockout_rate=("stockout", "mean"), unmet_units=("unmet_qty", "sum"))
    so = so.join(wh.set_index("warehouse_id").city)
    for w, r in so.iterrows():
        flag = "  <- under-supplied" if w == UNDERSUPPLIED_WH else ""
        print(f"  {w} {r.city:<10} {r.stockout_days:>5} days ({r.stockout_rate:.1%}), "
              f"{r.unmet_units:>6,} units unmet{flag}")

    print(f"\nPlanted scenario 3 - demand spike: {SPIKE_PRODUCT} {names[SPIKE_PRODUCT]}")
    sp = dem[dem.product_id == SPIKE_PRODUCT].assign(month=lambda d: d.date_key // 100)
    sp = sp.groupby("month").agg(demand=("demand_qty", "sum"), unmet=("unmet_qty", "sum"),
                                 stockout_days=("unmet_qty", lambda s: (s > 0).sum()))
    for m, r in sp.loc[202605:].iterrows():
        print(f"  {m}: demand {r.demand:>6,}  unmet {r.unmet:>6,}  stock-out days {r.stockout_days:>3}")

    print()
    if failures:
        print(f"VALIDATION FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
