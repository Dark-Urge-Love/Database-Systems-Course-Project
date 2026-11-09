-- =====================================================================
-- 索引脚本  index.sql（可重复执行，不会因索引已存在而报错）
-- 用途：
--   1) 补建查询性能关键索引（与 schema.sql 中声明的二级索引保持一致，
--      也可用于"删掉索引后重建"的恢复演示）
--   2) 作为"索引优化与性能对比"的对照实验脚本
-- =====================================================================
USE drug_db;
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 辅助存储过程：索引不存在才创建
-- （MySQL 的 CREATE INDEX 不支持 IF NOT EXISTS，故用存储过程封装）
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS add_index_if_missing;

DELIMITER $$
CREATE PROCEDURE add_index_if_missing(
    IN p_table VARCHAR(64), IN p_index VARCHAR(64), IN p_cols VARCHAR(255))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
         WHERE table_schema = DATABASE()
           AND table_name   = p_table
           AND index_name   = p_index
    ) THEN
        SET @ddl = CONCAT('CREATE INDEX ', p_index, ' ON ', p_table, ' (', p_cols, ')');
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
        SELECT CONCAT('已创建索引 ', p_index, ' ON ', p_table) AS 执行结果;
    ELSE
        SELECT CONCAT('索引已存在，跳过 ', p_index) AS 执行结果;
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------
-- 1. 主数据表
-- ---------------------------------------------------------------------
CALL add_index_if_missing('supplier',      'idx_supplier_credit',  'credit_level');
CALL add_index_if_missing('employee',      'idx_emp_warehouse',    'warehouse_id');
CALL add_index_if_missing('employee',      'idx_emp_position',     'position');
CALL add_index_if_missing('drug',          'idx_drug_category',    'category_id');
CALL add_index_if_missing('drug',          'idx_drug_name',        'drug_name');

-- ---------------------------------------------------------------------
-- 2. 单据表
-- ---------------------------------------------------------------------
CALL add_index_if_missing('purchase_order', 'idx_po_supplier', 'supplier_id');
CALL add_index_if_missing('purchase_order', 'idx_po_date',     'order_date');
CALL add_index_if_missing('purchase_order', 'idx_po_status',   'status');
CALL add_index_if_missing('purchase_item',  'idx_pi_po',       'po_id');
CALL add_index_if_missing('purchase_item',  'idx_pi_drug',     'drug_id');
CALL add_index_if_missing('purchase_item',  'idx_pi_batch',    'batch_no');
CALL add_index_if_missing('sale_order',     'idx_so_customer', 'customer_name');
CALL add_index_if_missing('sale_order',     'idx_so_date',     'sale_date');
CALL add_index_if_missing('sale_order',     'idx_so_warehouse','warehouse_id');
CALL add_index_if_missing('sale_item',      'idx_si_so',       'so_id');
CALL add_index_if_missing('sale_item',      'idx_si_drug',     'drug_id');

-- ---------------------------------------------------------------------
-- 3. 库存与流水（性能对比实验的主战场）
--    idx_move_drug_time：按药品 + 时间查流水（复合索引，最左前缀）
--    idx_move_time     ：按时间范围统计（索引对比用例 2）
-- ---------------------------------------------------------------------
CALL add_index_if_missing('stock',          'idx_stock_warehouse', 'warehouse_id');
CALL add_index_if_missing('stock_batch',    'idx_batch_expiry',    'expiry_date');
CALL add_index_if_missing('stock_batch',    'idx_batch_drug',      'drug_id');
CALL add_index_if_missing('stock_movement', 'idx_move_drug_time',  'drug_id, move_time');
CALL add_index_if_missing('stock_movement', 'idx_move_type_time',  'move_type, move_time');
CALL add_index_if_missing('stock_movement', 'idx_move_time',       'move_time');
CALL add_index_if_missing('access_log',     'idx_log_time',        'created_at');
CALL add_index_if_missing('access_log',     'idx_log_drug',        'drug_id');

-- ---------------------------------------------------------------------
-- 清理辅助存储过程
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS add_index_if_missing;

-- ---------------------------------------------------------------------
-- 当前索引一览
-- ---------------------------------------------------------------------
SELECT table_name AS 表, index_name AS 索引名,
       GROUP_CONCAT(column_name ORDER BY seq_in_index) AS 列,
       COUNT(DISTINCT seq_in_index) AS 列数
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND index_name <> 'PRIMARY'
GROUP BY table_name, index_name
ORDER BY table_name, index_name;
