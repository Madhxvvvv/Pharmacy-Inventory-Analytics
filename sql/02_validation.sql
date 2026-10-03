-- A1. Stock reconciles: should return ZERO rows
USE pharma_inventory;
SELECT product_id, warehouse_id, SUM(qty_change) AS closing_stock
FROM fact_inventory_txn
GROUP BY product_id, warehouse_id
HAVING closing_stock < 0;

-- A2. No sales dated after a batch expired: should return ZERO rows
SELECT s.batch_id, e.txn_date AS expired_on, s.txn_date AS sale_dated
FROM fact_inventory_txn s
JOIN fact_inventory_txn e
  ON s.batch_id = e.batch_id AND e.txn_type = 'EXPIRED'
WHERE s.txn_type = 'SALE' AND s.txn_date > e.txn_date;