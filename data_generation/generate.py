"""Synthetic data generator for the pharma inventory analytics project.

Simulates one year (2025-10-01 .. 2026-09-30) of daily demand, FEFO fulfilment,
replenishment, customer returns and expiry write-offs for 20 products across
4 warehouses, and writes star-schema CSVs to data/.

Transaction granularity
-----------------------
Demand is simulated (and stored in fact_daily_demand) per day. Stock movements
in fact_inventory_txn are posted the way a distributor's ERP typically does:
  * PURCHASE  - one row per batch, on the day it is received
  * SALE      - one row per batch per week (weekly dispatch posting), dated the
                week-ending Sunday, or the day before the batch expires, or the
                last day of the period - whichever comes first
  * RETURN    - one row per return event, 1-7 days after the sale posting
  * EXPIRED   - one row per batch that still holds stock on its expiry date
Posting a sale on or after the days it physically happened means the ledger
balance in date order is never lower than the true physical balance, so the
batch-level "never negative" rule holds on the posted ledger as well.

Run:  python data_generation/generate.py
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

SEED = 42
START = dt.date(2025, 10, 1)
END = dt.date(2026, 9, 30)
AS_OF = END
OUT_DIR = Path(__file__).resolve().parent.parent / "data"

rng = np.random.default_rng(SEED)
Faker.seed(SEED)
fake = Faker("en_IN")

ONE_DAY = dt.timedelta(days=1)

# --------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------
# name, category, shelf_life_days, lead_time_days, unit_cost (INR),
# base daily demand per warehouse, seasonality profile
PRODUCTS = [
    ("Paracetamol 650mg Tablets (Strip of 15)", "OTC", 730, 7, 18.50, 34, "fever"),
    ("Cetirizine 10mg Tablets (Strip of 10)", "OTC", 730, 7, 11.00, 18, "cold"),
    ("Ambroxol Cough Syrup 100ml", "OTC", 270, 10, 52.00, 13, "cold"),
    ("Dextromethorphan Cough Syrup 100ml", "OTC", 240, 10, 61.00, 10, "cold"),
    ("ORS Electrolyte Powder 21g (Pack of 10)", "OTC", 540, 8, 38.00, 15, "summer"),
    ("Antacid Mint Gel 170ml", "OTC", 365, 9, 74.00, 3, "flat"),
    ("Amoxicillin 500mg Capsules (Strip of 10)", "Rx", 730, 12, 48.00, 15, "cold"),
    ("Azithromycin 500mg Tablets (Strip of 3)", "Rx", 730, 12, 66.00, 10, "cold"),
    ("Metformin 500mg Tablets (Strip of 20)", "Rx", 730, 14, 22.00, 28, "flat"),
    ("Amlodipine 5mg Tablets (Strip of 15)", "Rx", 730, 14, 19.00, 22, "flat"),
    ("Atorvastatin 10mg Tablets (Strip of 15)", "Rx", 730, 14, 58.00, 17, "flat"),
    ("Pantoprazole 40mg Tablets (Strip of 15)", "Rx", 730, 12, 45.00, 20, "flat"),
    ("Telmisartan 40mg Tablets (Strip of 15)", "Rx", 730, 14, 62.00, 14, "flat"),
    ("Montelukast 10mg Tablets (Strip of 10)", "Rx", 540, 12, 84.00, 9, "cold"),
    ("Ondansetron 4mg Tablets (Strip of 10)", "Rx", 730, 12, 36.00, 6, "flat"),
    ("Vitamin D3 60000 IU Capsules (Pack of 4)", "Supplement", 730, 15, 88.00, 7, "flat"),
    ("Probiotic Lactobacillus Capsules (Strip of 10)", "Supplement", 180, 10, 95.00, 2, "flat"),
    ("Multivitamin with Minerals Tablets (Strip of 15)", "Supplement", 545, 15, 72.00, 10, "flat"),
    ("Omega-3 Fish Oil 1000mg Capsules (Bottle of 30)", "Supplement", 450, 21, 210.00, 2, "flat"),
    ("Zinc + Vitamin C Chewable Tablets (Strip of 15)", "Supplement", 365, 10, 42.00, 8, "cold"),
]

WAREHOUSES = [
    # id, name, city, region, demand scale
    ("W01", "Delhi Central Distribution Centre", "Delhi", "North", 1.15),
    ("W02", "Mumbai Bhiwandi Distribution Centre", "Mumbai", "West", 1.25),
    ("W03", "Bengaluru Hoskote Distribution Centre", "Bengaluru", "South", 1.00),
    ("W04", "Kolkata Dankuni Distribution Centre", "Kolkata", "East", 0.85),
]

# Planted scenarios ----------------------------------------------------------
SLOW_MOVERS = ["P006", "P017", "P019"]          # overstocked, expiring soon after as-of
SLOW_EXPIRY_WINDOW = (30, 90)                   # days after AS_OF
SPIKE_PRODUCT = "P001"                          # dengue/viral-fever season spike
SPIKE_START = dt.date(2026, 8, 1)
SPIKE_MULTIPLIER = 2.6
UNDERSUPPLIED_WH = "W04"                        # Kolkata: slow, delayed supply

# Replenishment policy (what the planner *thinks* the lead time is = nominal)
SAFETY_DAYS = 7
COVER_DAYS = 45
UNDERSUPPLIED_COVER_DAYS = 30                   # smaller allocations to Kolkata
# Manufacturer minimum order quantities: on low-volume / short-shelf-life lines
# these force more cover than demand can absorb, producing expiry write-offs.
MOQ = {"P003": 1300, "P004": 1500, "P006": 400, "P014": 800, "P015": 600,
       "P016": 700, "P017": 500, "P019": 300, "P020": 1400}
RETURN_PROB = 0.12                              # share of weekly sale postings with a return
RETURN_SHARE = (0.10, 0.25)                     # fraction of the posting that comes back
                                                # -> ~2% of sold units are returned

WEEKDAY_FACTOR = [1.05, 1.00, 1.00, 1.00, 1.05, 1.05, 0.65]  # Mon..Sun

SEASONALITY = {
    "flat":   {m: 1.0 + 0.05 * math.sin(2 * math.pi * (m - 1) / 12) for m in range(1, 13)},
    "cold":   {1: 1.40, 2: 1.30, 3: 1.10, 4: 0.95, 5: 0.85, 6: 0.85,
               7: 0.90, 8: 0.95, 9: 1.00, 10: 1.10, 11: 1.35, 12: 1.45},
    "summer": {1: 0.80, 2: 0.85, 3: 1.05, 4: 1.30, 5: 1.45, 6: 1.35,
               7: 1.10, 8: 1.00, 9: 0.95, 10: 0.90, 11: 0.80, 12: 0.80},
    "fever":  {1: 1.15, 2: 1.10, 3: 1.00, 4: 0.95, 5: 0.95, 6: 1.00,
               7: 1.10, 8: 1.15, 9: 1.15, 10: 1.10, 11: 1.10, 12: 1.15},
}

TXN_ORDER = {"PURCHASE": 0, "RETURN": 1, "SALE": 2, "EXPIRED": 3}


def build_dim_product() -> pd.DataFrame:
    suffixes = ["Pharmaceuticals", "Lifesciences", "Healthcare", "Laboratories", "Remedies", "Biotech"]
    manufacturers = []
    while len(manufacturers) < 8:
        name = f"{fake.last_name()} {suffixes[len(manufacturers) % len(suffixes)]} Ltd"
        if name not in manufacturers:
            manufacturers.append(name)

    rows = []
    for i, (name, cat, shelf, lead, cost, *_rest) in enumerate(PRODUCTS, start=1):
        margin = {"OTC": (1.25, 1.45), "Rx": (1.30, 1.60), "Supplement": (1.40, 1.80)}[cat]
        rows.append({
            "product_id": f"P{i:03d}",
            "product_name": name,
            "category": cat,
            "manufacturer": manufacturers[int(rng.integers(len(manufacturers)))],
            "unit_cost": cost,
            "unit_price": round(cost * rng.uniform(*margin), 2),
            "shelf_life_days": shelf,
            "lead_time_days": lead,
        })
    return pd.DataFrame(rows)


def build_dim_warehouse() -> pd.DataFrame:
    return pd.DataFrame(
        [{"warehouse_id": w, "warehouse_name": n, "city": c, "region": r} for w, n, c, r, _ in WAREHOUSES]
    )


def build_dim_date() -> pd.DataFrame:
    dates = pd.date_range(START, END, freq="D")
    return pd.DataFrame({
        "date_key": dates.strftime("%Y%m%d").astype(int),
        "full_date": dates.strftime("%Y-%m-%d"),
        "day": dates.day,
        "month": dates.month,
        "month_name": dates.strftime("%B"),
        "quarter": dates.quarter,
        "year": dates.year,
        "day_of_week": dates.strftime("%A"),
        "is_weekend": (dates.dayofweek >= 5).astype(int),
    })


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
class Ledger:
    """Collects batches, transactions and daily demand across all simulations."""

    def __init__(self):
        self.batches: list[dict] = []
        self.txns: list[dict] = []
        self.demand: list[dict] = []

    def add_txn(self, date, ttype, batch, qty_change, unit_cost):
        self.txns.append({
            "txn_date": date, "product_id": batch["product_id"], "warehouse_id": batch["warehouse_id"],
            "batch_id": batch["batch_id"], "txn_type": ttype, "qty_change": int(qty_change),
            "unit_cost": unit_cost,
        })

    def new_batch(self, pid, wid, purchase_date, qty, shelf_life, unit_cost, mfg_date=None):
        if mfg_date is None:
            # stock is already 1-4 months old when it reaches the warehouse
            mfg_date = purchase_date - dt.timedelta(days=int(rng.integers(30, 121)))
        batch = {
            "batch_id": f"BT{len(self.batches) + 1:05d}",
            "product_id": pid,
            "warehouse_id": wid,
            "manufacture_date": mfg_date,
            "expiry_date": mfg_date + dt.timedelta(days=shelf_life),
            "purchase_date": purchase_date,
            "quantity_received": int(qty),
        }
        self.batches.append(batch)
        self.add_txn(purchase_date, "PURCHASE", batch, qty, unit_cost)
        # live state used by the simulation only
        return {**batch, "qty": int(qty)}


def round_up(x, step=10):
    return int(math.ceil(max(x, 1) / step) * step)


def simulate(product: pd.Series, wh: tuple, season: str, base_rate: float, ledger: Ledger):
    pid, wid = product.product_id, wh[0]
    shelf, lead, cost = int(product.shelf_life_days), int(product.lead_time_days), float(product.unit_cost)
    rate = base_rate * wh[4]
    undersupplied = wid == UNDERSUPPLIED_WH

    rop = rate * (lead + SAFETY_DAYS)
    order_qty = round_up(rate * (UNDERSUPPLIED_COVER_DAYS if undersupplied else COVER_DAYS))
    order_qty = max(order_qty, MOQ.get(pid, 0))

    def actual_lead():
        if undersupplied:   # transport delays + late allocations
            days = int(round(lead * 1.4)) + int(rng.integers(2, 8))
            delay_prob = 0.25
        else:
            days = lead + int(rng.integers(0, 4))
            delay_prob = 0.10
        if rng.random() < delay_prob:   # occasional supplier slip
            days += int(rng.integers(5, 13))
        return days

    live: list[dict] = []
    on_order: list[tuple[dt.date, int]] = []
    returns_due: dict[dt.date, list] = defaultdict(list)
    unposted: dict[str, int] = defaultdict(int)       # batch_id -> units sold since last posting
    by_id: dict[str, dict] = {}

    def receive(date, qty, mfg=None):
        b = ledger.new_batch(pid, wid, date, qty, shelf, cost, mfg)
        live.append(b)
        by_id[b["batch_id"]] = b

    def post_sales(batch_id, date, allow_returns):
        q = unposted.pop(batch_id, 0)
        if q <= 0:
            return
        b = by_id[batch_id]
        ledger.add_txn(date, "SALE", b, -q, cost)
        if allow_returns and rng.random() < RETURN_PROB:
            rq = max(1, int(round(q * rng.uniform(*RETURN_SHARE))))
            rdate = date + dt.timedelta(days=int(rng.integers(1, 8)))
            if rdate <= END and rdate < b["expiry_date"]:
                returns_due[rdate].append((batch_id, rq))

    # Opening stock: initial purchase batch on day 1
    receive(START, round_up(rate * rng.uniform(30, 45)))

    # Planted: slow mover gets one large opportunistic buy whose expiry lands
    # 30-90 days after the as-of date
    bulk = None
    if pid in SLOW_MOVERS:
        expiry = AS_OF + dt.timedelta(days=int(rng.integers(SLOW_EXPIRY_WINDOW[0] + 5, SLOW_EXPIRY_WINDOW[1] - 5)))
        mfg = expiry - dt.timedelta(days=shelf)
        purchase = mfg + dt.timedelta(days=int(rng.integers(30, 121)))
        purchase = max(purchase, START + dt.timedelta(days=30))
        days_left = (END - purchase).days + 1
        qty = round_up(rate * (0.95 * days_left + rng.uniform(130, 180)))
        bulk = (purchase, qty, mfg)

    d = START
    while d <= END:
        # 1. receipts
        for arr in [o for o in on_order if o[0] == d]:
            receive(d, arr[1])
            on_order.remove(arr)
        if bulk and bulk[0] == d:
            receive(d, bulk[1], bulk[2])

        # 2. customer returns go back into their original batch
        for batch_id, rq in returns_due.pop(d, []):
            b = by_id[batch_id]
            b["qty"] += rq
            ledger.add_txn(d, "RETURN", b, rq, cost)

        # 3. expiry: post outstanding sales (dated the day before), write off the rest
        for b in [b for b in live if b["expiry_date"] == d]:
            post_sales(b["batch_id"], d - ONE_DAY, allow_returns=False)
            if b["qty"] > 0:
                ledger.add_txn(d, "EXPIRED", b, -b["qty"], cost)
                b["qty"] = 0
        live = [b for b in live if b["expiry_date"] > d]

        # 4. demand, fulfilled FEFO
        lam = rate * WEEKDAY_FACTOR[d.weekday()] * SEASONALITY[season][d.month]
        if pid == SPIKE_PRODUCT and d >= SPIKE_START:
            lam *= SPIKE_MULTIPLIER
        demand = int(rng.poisson(lam))
        remaining = demand
        for b in sorted(live, key=lambda x: (x["expiry_date"], x["batch_id"])):
            if remaining == 0:
                break
            take = min(b["qty"], remaining)
            if take > 0:
                b["qty"] -= take
                unposted[b["batch_id"]] += take
                remaining -= take
            assert b["qty"] >= 0
        ledger.demand.append({
            "date_key": int(d.strftime("%Y%m%d")), "product_id": pid, "warehouse_id": wid,
            "demand_qty": demand, "fulfilled_qty": demand - remaining, "unmet_qty": remaining,
        })

        # 5. replenishment on inventory position
        position = sum(b["qty"] for b in live) + sum(q for _, q in on_order)
        if position < rop:
            on_order.append((d + dt.timedelta(days=actual_lead()), order_qty))

        # 6. weekly sales posting (Sunday) and period close
        if d.weekday() == 6 or d == END:
            for batch_id in list(unposted):
                post_sales(batch_id, d, allow_returns=True)

        d += ONE_DAY


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dim_product = build_dim_product()
    dim_warehouse = build_dim_warehouse()
    dim_date = build_dim_date()

    ledger = Ledger()
    for (_, product), (*_p, base_rate, season) in zip(dim_product.iterrows(), PRODUCTS):
        for wh in WAREHOUSES:
            simulate(product, wh, season, base_rate, ledger)

    dim_batch = pd.DataFrame(ledger.batches)

    txn = pd.DataFrame(ledger.txns)
    txn["_order"] = txn["txn_type"].map(TXN_ORDER)
    txn = txn.sort_values(["txn_date", "_order", "product_id", "warehouse_id", "batch_id"]).reset_index(drop=True)
    txn.insert(0, "txn_id", np.arange(1, len(txn) + 1))
    txn.insert(2, "date_key", txn["txn_date"].map(lambda x: int(x.strftime("%Y%m%d"))))
    txn["txn_value"] = (txn["qty_change"] * txn["unit_cost"]).round(2)
    txn = txn[["txn_id", "txn_date", "date_key", "product_id", "warehouse_id", "batch_id",
               "txn_type", "qty_change", "unit_cost", "txn_value"]]

    demand = pd.DataFrame(ledger.demand)

    outputs = {
        "dim_product": dim_product,
        "dim_warehouse": dim_warehouse,
        "dim_date": dim_date,
        "dim_batch": dim_batch,
        "fact_inventory_txn": txn,
        "fact_daily_demand": demand,
    }
    for name, df in outputs.items():
        df.to_csv(OUT_DIR / f"{name}.csv", index=False)
        print(f"{name:<20} {len(df):>7,} rows")


if __name__ == "__main__":
    main()
