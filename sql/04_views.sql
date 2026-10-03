USE pharma_inventory;

-- View 1: current stock position per product x warehouse
CREATE OR REPLACE VIEW v_inventory_status AS
WITH stock AS (
    SELECT product_id, warehouse_id, SUM(qty_change) AS current_stock
    FROM fact_inventory_txn
    GROUP BY product_id, warehouse_id
),
demand AS (
    SELECT d.product_id, d.warehouse_id, AVG(d.demand_qty) AS avg_daily_demand
    FROM fact_daily_demand d
    JOIN dim_date dt ON d.date_key = dt.date_key
    WHERE dt.full_date > DATE '2026-09-30' - INTERVAL 90 DAY
    GROUP BY d.product_id, d.warehouse_id
)
SELECT s.product_id,
       s.warehouse_id,
       s.current_stock,
       ROUND(dm.avg_daily_demand, 2) AS avg_daily_demand,
       p.lead_time_days,
       ROUND(s.current_stock / NULLIF(dm.avg_daily_demand, 0)) AS days_of_cover,
       ROUND(s.current_stock * p.unit_cost) AS stock_value,
       CASE
           WHEN s.current_stock < dm.avg_daily_demand * p.lead_time_days        THEN 'Reorder now'
           WHEN s.current_stock < dm.avg_daily_demand * (p.lead_time_days + 14) THEN 'Reorder soon'
           ELSE 'OK'
       END AS reorder_status
FROM stock s
JOIN demand dm     ON s.product_id = dm.product_id AND s.warehouse_id = dm.warehouse_id
JOIN dim_product p ON s.product_id = p.product_id;

-- View 2: batch-level expiry risk
CREATE OR REPLACE VIEW v_batch_expiry AS
WITH batch_stock AS (
    SELECT batch_id, SUM(qty_change) AS remaining_qty
    FROM fact_inventory_txn
    GROUP BY batch_id
    HAVING remaining_qty > 0
)
SELECT b.batch_id,
       b.product_id,
       b.warehouse_id,
       b.expiry_date,
       DATEDIFF(b.expiry_date, DATE '2026-09-30') AS days_to_expiry,
       bs.remaining_qty,
       ROUND(bs.remaining_qty * p.unit_cost) AS value_at_risk,
       CASE
           WHEN DATEDIFF(b.expiry_date, DATE '2026-09-30') <= 30 THEN '0-30 days'
           WHEN DATEDIFF(b.expiry_date, DATE '2026-09-30') <= 60 THEN '31-60 days'
           WHEN DATEDIFF(b.expiry_date, DATE '2026-09-30') <= 90 THEN '61-90 days'
           ELSE '90+ days'
       END AS expiry_bucket
FROM batch_stock bs
JOIN dim_batch b   ON bs.batch_id = b.batch_id
JOIN dim_product p ON b.product_id = p.product_id;

-- View 3: running stock balance over time
CREATE OR REPLACE VIEW v_stock_running AS
WITH daily_change AS (
    SELECT product_id, warehouse_id, txn_date, date_key,
           SUM(qty_change) AS net_change
    FROM fact_inventory_txn
    GROUP BY product_id, warehouse_id, txn_date, date_key
)
SELECT product_id,
       warehouse_id,
       txn_date,
       date_key,
       net_change,
       SUM(net_change) OVER (
           PARTITION BY product_id, warehouse_id
           ORDER BY txn_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS running_stock
FROM daily_change;

SELECT * FROM v_inventory_status LIMIT 10;
SELECT * FROM v_batch_expiry ORDER BY value_at_risk DESC LIMIT 10;
SELECT * FROM v_stock_running WHERE product_id = 'P012' AND warehouse_id = 'W03';

-- Consistency check: should equal 901467 (the Q5b total)
SELECT SUM(value_at_risk) FROM v_batch_expiry WHERE days_to_expiry BETWEEN 0 AND 90;