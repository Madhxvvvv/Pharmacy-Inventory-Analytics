USE pharma_inventory;

-- Q1. Current stock per product x warehouse
SELECT p.product_name, w.city, SUM(t.qty_change) AS current_stock
FROM fact_inventory_txn t
JOIN dim_product p   ON t.product_id = p.product_id
JOIN dim_warehouse w ON t.warehouse_id = w.warehouse_id
GROUP BY p.product_name, w.city
ORDER BY current_stock DESC;

-- Q2. Running stock balance (window function)
WITH daily_change AS (
    SELECT product_id, warehouse_id, txn_date, SUM(qty_change) AS net_change
    FROM fact_inventory_txn
    GROUP BY product_id, warehouse_id, txn_date
)
SELECT product_id, warehouse_id, txn_date, net_change,
       SUM(net_change) OVER (
           PARTITION BY product_id, warehouse_id
           ORDER BY txn_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS running_stock
FROM daily_changeq2 
WHERE product_id = 'P012' AND warehouse_id = 'W03'   -- remove this line to see every product
ORDER BY txn_date;

-- Q3. Days of inventory cover
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
SELECT p.product_name, w.city,
       s.current_stock,
       ROUND(dm.avg_daily_demand, 1) AS avg_daily_demand,
       ROUND(s.current_stock / NULLIF(dm.avg_daily_demand, 0), 0) AS days_of_cover
FROM stock s
JOIN demand dm       ON s.product_id = dm.product_id AND s.warehouse_id = dm.warehouse_id
JOIN dim_product p   ON s.product_id = p.product_id
JOIN dim_warehouse w ON s.warehouse_id = w.warehouse_id
ORDER BY days_of_cover DESC;

-- Q4. Reorder flag
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
SELECT p.product_name, w.city,
       s.current_stock,
       p.lead_time_days,
       ROUND(dm.avg_daily_demand * p.lead_time_days) AS lead_time_demand,
       CASE
           WHEN s.current_stock < dm.avg_daily_demand * p.lead_time_days        THEN 'Reorder now'
           WHEN s.current_stock < dm.avg_daily_demand * (p.lead_time_days + 14) THEN 'Reorder soon'
           ELSE 'OK'
       END AS reorder_status
FROM stock s
JOIN demand dm       ON s.product_id = dm.product_id AND s.warehouse_id = dm.warehouse_id
JOIN dim_product p   ON s.product_id = p.product_id
JOIN dim_warehouse w ON s.warehouse_id = w.warehouse_id
ORDER BY FIELD(reorder_status, 'Reorder now', 'Reorder soon', 'OK'), w.city;


-- Q5. Value of stock nearing expiry (batch level)
WITH batch_stock AS (
    SELECT batch_id, SUM(qty_change) AS remaining_qty
    FROM fact_inventory_txn
    GROUP BY batch_id
    HAVING remaining_qty > 0
)
SELECT p.product_name, p.category, w.city, b.batch_id, b.expiry_date,
       DATEDIFF(b.expiry_date, DATE '2026-09-30') AS days_to_expiry,
       bs.remaining_qty,
       ROUND(bs.remaining_qty * p.unit_cost) AS value_at_risk,
       CASE
           WHEN DATEDIFF(b.expiry_date, DATE '2026-09-30') <= 30 THEN '0-30 days'
           WHEN DATEDIFF(b.expiry_date, DATE '2026-09-30') <= 60 THEN '31-60 days'
           ELSE '61-90 days'
       END AS expiry_bucket
FROM batch_stock bs
JOIN dim_batch b     ON bs.batch_id = b.batch_id
JOIN dim_product p   ON b.product_id = p.product_id
JOIN dim_warehouse w ON b.warehouse_id = w.warehouse_id
WHERE DATEDIFF(b.expiry_date, DATE '2026-09-30') BETWEEN 0 AND 90
ORDER BY value_at_risk DESC;

-- Q5b. Total value at risk by product
WITH batch_stock AS (
    SELECT batch_id, SUM(qty_change) AS remaining_qty
    FROM fact_inventory_txn
    GROUP BY batch_id
    HAVING remaining_qty > 0
)
SELECT p.product_name,
       SUM(bs.remaining_qty) AS units_at_risk,
       ROUND(SUM(bs.remaining_qty * p.unit_cost)) AS value_at_risk
FROM batch_stock bs
JOIN dim_batch b   ON bs.batch_id = b.batch_id
JOIN dim_product p ON b.product_id = p.product_id
WHERE DATEDIFF(b.expiry_date, DATE '2026-09-30') BETWEEN 0 AND 90
GROUP BY p.product_name
ORDER BY value_at_risk DESC;

-- Q6. Stock-out rate by warehouse
SELECT w.city,
       COUNT(*) AS product_days,
       SUM(d.unmet_qty > 0) AS stockout_days,
       ROUND(100 * SUM(d.unmet_qty > 0) / COUNT(*), 1) AS stockout_rate_pct,
       SUM(d.unmet_qty) AS units_unmet
FROM fact_daily_demand d
JOIN dim_warehouse w ON d.warehouse_id = w.warehouse_id
GROUP BY w.city
ORDER BY stockout_rate_pct DESC;

-- Q7. Month-over-month revenue growth (LAG)
WITH monthly AS (
    SELECT dt.year, dt.month,
           SUM(d.fulfilled_qty * p.unit_price) AS revenue
    FROM fact_daily_demand d
    JOIN dim_date dt   ON d.date_key = dt.date_key
    JOIN dim_product p ON d.product_id = p.product_id
    GROUP BY dt.year, dt.month
)
SELECT year, month,
       ROUND(revenue) AS revenue,
       ROUND(LAG(revenue) OVER (ORDER BY year, month)) AS prev_month_revenue,
       ROUND(100 * (revenue - LAG(revenue) OVER (ORDER BY year, month))
             / LAG(revenue) OVER (ORDER BY year, month), 1) AS mom_growth_pct
FROM monthly
ORDER BY year, month;

-- Q7b. Month-over-month growth, normalized per day
WITH monthly AS (
    SELECT dt.year, dt.month,
           SUM(d.fulfilled_qty * p.unit_price) AS revenue,
           COUNT(DISTINCT dt.full_date) AS days_in_month
    FROM fact_daily_demand d
    JOIN dim_date dt   ON d.date_key = dt.date_key
    JOIN dim_product p ON d.product_id = p.product_id
    GROUP BY dt.year, dt.month
),
per_day AS (
    SELECT year, month, days_in_month,
           revenue / days_in_month AS revenue_per_day
    FROM monthly
)
SELECT year, month, days_in_month,
       ROUND(revenue_per_day) AS revenue_per_day,
       ROUND(100 * (revenue_per_day - LAG(revenue_per_day) OVER (ORDER BY year, month))
             / LAG(revenue_per_day) OVER (ORDER BY year, month), 1) AS mom_growth_per_day_pct
FROM per_day
ORDER BY year, month;