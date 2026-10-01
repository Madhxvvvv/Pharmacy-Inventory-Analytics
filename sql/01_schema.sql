-- ============================================================================
-- Pharma Inventory Analytics - schema (MySQL 8.0+)
-- Star schema: 4 dimensions + 2 facts. Re-runnable: drops and recreates tables.
-- Load data with: python data_generation/load.py
-- ============================================================================

CREATE DATABASE IF NOT EXISTS pharma_inventory
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE pharma_inventory;

DROP TABLE IF EXISTS fact_daily_demand;
DROP TABLE IF EXISTS fact_inventory_txn;
DROP TABLE IF EXISTS dim_batch;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_warehouse;
DROP TABLE IF EXISTS dim_product;

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------
CREATE TABLE dim_product (
    product_id       CHAR(4)        NOT NULL,
    product_name     VARCHAR(100)   NOT NULL,
    category         VARCHAR(20)    NOT NULL,
    manufacturer     VARCHAR(100)   NOT NULL,
    unit_cost        DECIMAL(10,2)  NOT NULL,
    unit_price       DECIMAL(10,2)  NOT NULL,
    shelf_life_days  SMALLINT       NOT NULL,
    lead_time_days   SMALLINT       NOT NULL,
    CONSTRAINT pk_dim_product PRIMARY KEY (product_id),
    CONSTRAINT chk_product_category CHECK (category IN ('OTC', 'Rx', 'Supplement')),
    CONSTRAINT chk_product_cost CHECK (unit_cost > 0 AND unit_price >= unit_cost),
    CONSTRAINT chk_product_days CHECK (shelf_life_days > 0 AND lead_time_days > 0)
);

CREATE TABLE dim_warehouse (
    warehouse_id    CHAR(3)       NOT NULL,
    warehouse_name  VARCHAR(100)  NOT NULL,
    city            VARCHAR(50)   NOT NULL,
    region          VARCHAR(10)   NOT NULL,
    CONSTRAINT pk_dim_warehouse PRIMARY KEY (warehouse_id),
    CONSTRAINT chk_warehouse_region CHECK (region IN ('North', 'South', 'East', 'West'))
);

CREATE TABLE dim_date (
    date_key     INT          NOT NULL,   -- YYYYMMDD
    full_date    DATE         NOT NULL,
    day          TINYINT      NOT NULL,
    month        TINYINT      NOT NULL,
    month_name   VARCHAR(10)  NOT NULL,
    quarter      TINYINT      NOT NULL,
    year         SMALLINT     NOT NULL,
    day_of_week  VARCHAR(10)  NOT NULL,
    is_weekend   TINYINT(1)   NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_key),
    CONSTRAINT uq_dim_date_full_date UNIQUE (full_date),
    CONSTRAINT chk_date_is_weekend CHECK (is_weekend IN (0, 1))
);

CREATE TABLE dim_batch (
    batch_id           CHAR(7)  NOT NULL,
    product_id         CHAR(4)  NOT NULL,
    warehouse_id       CHAR(3)  NOT NULL,
    manufacture_date   DATE     NOT NULL,
    expiry_date        DATE     NOT NULL,
    purchase_date      DATE     NOT NULL,
    quantity_received  INT      NOT NULL,
    CONSTRAINT pk_dim_batch PRIMARY KEY (batch_id),
    CONSTRAINT fk_batch_product   FOREIGN KEY (product_id)   REFERENCES dim_product (product_id),
    CONSTRAINT fk_batch_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouse (warehouse_id),
    CONSTRAINT chk_batch_dates CHECK (manufacture_date < purchase_date AND purchase_date < expiry_date),
    CONSTRAINT chk_batch_qty CHECK (quantity_received > 0),
    INDEX ix_batch_product_warehouse (product_id, warehouse_id),
    INDEX ix_batch_expiry (expiry_date)
);

-- ---------------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------------
CREATE TABLE fact_inventory_txn (
    txn_id        INT            NOT NULL,
    txn_date      DATE           NOT NULL,
    date_key      INT            NOT NULL,
    product_id    CHAR(4)        NOT NULL,
    warehouse_id  CHAR(3)        NOT NULL,
    batch_id      CHAR(7)        NOT NULL,
    txn_type      VARCHAR(10)    NOT NULL,
    qty_change    INT            NOT NULL,
    unit_cost     DECIMAL(10,2)  NOT NULL,
    txn_value     DECIMAL(14,2)  NOT NULL,
    CONSTRAINT pk_fact_inventory_txn PRIMARY KEY (txn_id),
    CONSTRAINT fk_txn_date      FOREIGN KEY (date_key)     REFERENCES dim_date (date_key),
    CONSTRAINT fk_txn_product   FOREIGN KEY (product_id)   REFERENCES dim_product (product_id),
    CONSTRAINT fk_txn_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouse (warehouse_id),
    CONSTRAINT fk_txn_batch     FOREIGN KEY (batch_id)     REFERENCES dim_batch (batch_id),
    CONSTRAINT chk_txn_type CHECK (txn_type IN ('PURCHASE', 'SALE', 'RETURN', 'EXPIRED')),
    CONSTRAINT chk_txn_sign CHECK (
        (txn_type IN ('PURCHASE', 'RETURN') AND qty_change > 0) OR
        (txn_type IN ('SALE', 'EXPIRED')   AND qty_change < 0)
    ),
    INDEX ix_txn_product_warehouse_date (product_id, warehouse_id, txn_date),
    INDEX ix_txn_batch (batch_id, txn_id)
);

CREATE TABLE fact_daily_demand (
    date_key       INT      NOT NULL,
    product_id     CHAR(4)  NOT NULL,
    warehouse_id   CHAR(3)  NOT NULL,
    demand_qty     INT      NOT NULL,
    fulfilled_qty  INT      NOT NULL,
    unmet_qty      INT      NOT NULL,
    CONSTRAINT pk_fact_daily_demand PRIMARY KEY (date_key, product_id, warehouse_id),
    CONSTRAINT fk_demand_date      FOREIGN KEY (date_key)     REFERENCES dim_date (date_key),
    CONSTRAINT fk_demand_product   FOREIGN KEY (product_id)   REFERENCES dim_product (product_id),
    CONSTRAINT fk_demand_warehouse FOREIGN KEY (warehouse_id) REFERENCES dim_warehouse (warehouse_id),
    CONSTRAINT chk_demand_nonneg CHECK (demand_qty >= 0 AND fulfilled_qty >= 0 AND unmet_qty >= 0),
    CONSTRAINT chk_demand_balance CHECK (fulfilled_qty + unmet_qty = demand_qty),
    INDEX ix_demand_product_warehouse (product_id, warehouse_id)
);
