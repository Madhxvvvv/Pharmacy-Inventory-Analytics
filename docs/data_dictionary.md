# Data Dictionary — Pharma Inventory Analytics

Synthetic data for a pharmaceutical distributor with 20 products and 4 warehouses, covering **2025-10-01 to 2026-09-30**. The **as-of date** for analysis is **2026-09-30**.

The data is produced by `data_generation/generate.py` (seed 42), checked by `data_generation/validate.py`, and loaded into MySQL database `pharma_inventory` by `data_generation/load.py` using `sql/01_schema.sql`.

```
dim_product ─┐                    ┌─ dim_date
dim_warehouse ┼─ dim_batch ── fact_inventory_txn
              └──────────────── fact_daily_demand ─ dim_date
```

All quantities are in **selling units** (strips, bottles or packs, as named in `product_name`). All money is in **INR**.

---

## dim_product
One row per product (20 rows). The names are generic Indian pharma-style names, not real brands.

| Column | Type | Description |
|---|---|---|
| product_id | CHAR(4), PK | Product code, `P001`–`P020`. |
| product_name | VARCHAR(100) | Generic name, strength and pack, e.g. *Paracetamol 650mg Tablets (Strip of 15)*. |
| category | VARCHAR(20) | `OTC` (over the counter), `Rx` (prescription) or `Supplement`. |
| manufacturer | VARCHAR(100) | Fictional manufacturer name. |
| unit_cost | DECIMAL(10,2) | Purchase cost per unit (INR). Used to value every inventory transaction. |
| unit_price | DECIMAL(10,2) | Selling price per unit (INR). Margin depends on category (OTC ~25–45%, Rx ~30–60%, Supplement ~40–80%). |
| shelf_life_days | SMALLINT | Days from manufacture to expiry. Short (180–270) for syrups and probiotics; 365–730 for most others. |
| lead_time_days | SMALLINT | Nominal supplier lead time in days, as used by the replenishment planner. Actual delivery times vary (see Notes). |

## dim_warehouse
One row per distribution centre (4 rows).

| Column | Type | Description |
|---|---|---|
| warehouse_id | CHAR(3), PK | `W01`–`W04`. |
| warehouse_name | VARCHAR(100) | Distribution centre name. |
| city | VARCHAR(50) | Delhi, Mumbai, Bengaluru, Kolkata. |
| region | VARCHAR(10) | North, West, South, East. |

## dim_date
One row per calendar day in the period (365 rows).

| Column | Type | Description |
|---|---|---|
| date_key | INT, PK | Date as `YYYYMMDD`, e.g. `20260930`. Join key for both fact tables. |
| full_date | DATE | Calendar date (unique). |
| day | TINYINT | Day of month, 1–31. |
| month | TINYINT | Month number, 1–12. |
| month_name | VARCHAR(10) | `January` … `December`. |
| quarter | TINYINT | Calendar quarter, 1–4. |
| year | SMALLINT | Calendar year. |
| day_of_week | VARCHAR(10) | `Monday` … `Sunday`. |
| is_weekend | TINYINT(1) | 1 for Saturday or Sunday, otherwise 0. |

## dim_batch
One row per received batch (lot) of a product at a warehouse. A batch belongs to exactly one product × warehouse.

| Column | Type | Description |
|---|---|---|
| batch_id | CHAR(7), PK | Batch code, `BT00001`, … |
| product_id | CHAR(4), FK → dim_product | Product in the batch. |
| warehouse_id | CHAR(3), FK → dim_warehouse | Warehouse that received the batch. |
| manufacture_date | DATE | Manufacture date, 30–120 days (1–4 months) before `purchase_date`, so stock is already aged when it arrives. |
| expiry_date | DATE | `manufacture_date + shelf_life_days`. Stock cannot be sold on or after this date. Some batches expire after the as-of date. |
| purchase_date | DATE | Date the batch was received into the warehouse. Equals the date of its `PURCHASE` transaction. The opening stock batches are dated 2025-10-01. |
| quantity_received | INT | Units received. Equals the `qty_change` of its `PURCHASE` transaction. |

## fact_inventory_txn
The stock-movement ledger: one row per movement of a batch. Summing `qty_change` up to a date gives the stock on hand at that date, by batch, product, warehouse or in total.

| Column | Type | Description |
|---|---|---|
| txn_id | INT, PK | Sequence number in chronological order. On the same date, the order is PURCHASE → RETURN → SALE → EXPIRED. Order by `txn_id` to get running balances. |
| txn_date | DATE | Date the movement is posted. |
| date_key | INT, FK → dim_date | `txn_date` as `YYYYMMDD`. |
| product_id | CHAR(4), FK → dim_product | Product (always matches the batch's product). |
| warehouse_id | CHAR(3), FK → dim_warehouse | Warehouse (always matches the batch's warehouse). |
| batch_id | CHAR(7), FK → dim_batch | Batch the stock moved in or out of. |
| txn_type | VARCHAR(10) | `PURCHASE`, `SALE`, `RETURN` or `EXPIRED`. See below. |
| qty_change | INT | Signed units: **+** for PURCHASE and RETURN, **−** for SALE and EXPIRED. Never zero. |
| unit_cost | DECIMAL(10,2) | Product unit cost at the time of the movement (INR). |
| txn_value | DECIMAL(14,2) | `qty_change × unit_cost`, valued at cost and signed like `qty_change`. |

**Transaction types**

| txn_type | Rows | Meaning |
|---|---|---|
| PURCHASE | one per batch | A batch received from the supplier, dated `purchase_date`. Includes the opening stock on 2025-10-01. |
| SALE | one per batch per week | **Weekly dispatch posting.** Units sold from the batch during the week, dated the week-ending Sunday. If the batch expires mid-week, the posting is dated the day before expiry; the last posting is dated 2026-09-30. Use `fact_daily_demand` for daily sales. |
| RETURN | one per return event | Customer return put back into the same batch, 1–7 days after a sale posting. About 2% of units sold come back. Returns that would arrive on or after the batch's expiry date are not accepted. |
| EXPIRED | at most one per batch | Write-off of all remaining units on the batch's `expiry_date`. Afterwards the batch has zero stock. |

Guarantees (checked by `validate.py`):
- The running balance of every batch, in `txn_id` order, is never negative.
- A batch has no movements on or after its expiry date apart from its EXPIRED write-off.
- For each product × warehouse, total SALE units equal total `fulfilled_qty` in `fact_daily_demand`.

## fact_daily_demand
One row per date × product × warehouse (365 × 20 × 4 = 29,200 rows), including days with no demand.

| Column | Type | Description |
|---|---|---|
| date_key | INT, PK, FK → dim_date | Day. |
| product_id | CHAR(4), PK, FK → dim_product | Product. |
| warehouse_id | CHAR(3), PK, FK → dim_warehouse | Warehouse. |
| demand_qty | INT | Units customers ordered that day. |
| fulfilled_qty | INT | Units shipped, taken from stock first-expiry-first-out (FEFO). |
| unmet_qty | INT | Units not shipped because there was no stock (lost sales). `fulfilled_qty + unmet_qty = demand_qty`. |

A **stock-out day** is a row with `unmet_qty > 0`.

---

## Notes on how the data was simulated

- **Demand** is Poisson-distributed around a base rate per product, scaled by warehouse (Mumbai > Delhi > Bengaluru > Kolkata). It has a weekly pattern (Sundays are about 35% lower) and a monthly pattern: cough, cold and allergy products peak November–February, ORS peaks April–June, and Paracetamol rises in the monsoon and winter.
- **Replenishment**: when the stock position (on hand plus on order) falls below the reorder point (`daily rate × (lead_time_days + 7)`), the planner orders about 45 days of cover. Some low-volume or short-shelf-life products have a manufacturer **minimum order quantity**, which leads to some expiry write-offs. Actual delivery takes the nominal lead time plus 0–3 days, and about 10% of orders are delayed a further 5–12 days.
- **Planted scenarios** for the dashboard:
  1. **Slow movers at expiry risk**: P006 (Antacid Mint Gel), P017 (Probiotic Capsules) and P019 (Omega-3 Fish Oil). Each had a large one-off purchase whose expiry falls 30–90 days after the as-of date, so they end the period with several months of cover that is about to expire.
  2. **Kolkata (W04) is under-supplied**. Effective lead times are about 1.4× nominal plus 2–7 days, 25% of orders are delayed further, and order sizes are smaller (30 days of cover). The planner still uses nominal lead times, so Kolkata has far more stock-out days than the other warehouses.
  3. **Demand spike**: from 2026-08-01, demand for P001 (Paracetamol 650mg) is about 2.6× normal (fever season). Reorder points are not updated, so it runs out of stock repeatedly in August and September.
