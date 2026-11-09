-- =====================================================================
-- 数据部署校验脚本  verify.sql
-- 用法：mysql -u root -p < verify.sql
-- 作用：1) 统计各表行数  2) 账实自洽检查  3) 多表 join 业务统计演示
-- =====================================================================
USE drug_db;
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 1. 各表行数与总量（对应"≥5 张表、≥10 万条数据"的验收证据）
-- ---------------------------------------------------------------------
SELECT '1. 数据量统计' AS section;
SELECT 'drug_category' AS 表名, '药品分类' AS 业务, COUNT(*) AS 行数 FROM drug_category
UNION ALL SELECT 'supplier',       '供应商',   COUNT(*) FROM supplier
UNION ALL SELECT 'warehouse',      '仓库',     COUNT(*) FROM warehouse
UNION ALL SELECT 'employee',       '员工',     COUNT(*) FROM employee
UNION ALL SELECT 'drug',           '药品',     COUNT(*) FROM drug
UNION ALL SELECT 'purchase_order', '采购单',   COUNT(*) FROM purchase_order
UNION ALL SELECT 'purchase_item',  '采购明细', COUNT(*) FROM purchase_item
UNION ALL SELECT 'stock',          '库存',     COUNT(*) FROM stock
UNION ALL SELECT 'stock_batch',    '库存批次', COUNT(*) FROM stock_batch
UNION ALL SELECT 'sale_order',     '销售单',   COUNT(*) FROM sale_order
UNION ALL SELECT 'sale_item',      '销售明细', COUNT(*) FROM sale_item
UNION ALL SELECT 'stock_movement', '库存流水', COUNT(*) FROM stock_movement;

SELECT (SELECT COUNT(*) FROM drug_category) + (SELECT COUNT(*) FROM supplier)
     + (SELECT COUNT(*) FROM warehouse) + (SELECT COUNT(*) FROM employee)
     + (SELECT COUNT(*) FROM drug) + (SELECT COUNT(*) FROM purchase_order)
     + (SELECT COUNT(*) FROM purchase_item) + (SELECT COUNT(*) FROM stock)
     + (SELECT COUNT(*) FROM stock_batch) + (SELECT COUNT(*) FROM sale_order)
     + (SELECT COUNT(*) FROM sale_item) + (SELECT COUNT(*) FROM stock_movement)
       AS 业务数据总量;

-- ---------------------------------------------------------------------
-- 2. 账实自洽检查（全部应为 0）
-- ---------------------------------------------------------------------
SELECT '2. 账实自洽检查（全部应为 0）' AS section;
SELECT '库存结存 ≠ 批次结存之和' AS 检查项, COUNT(*) AS 异常行数
FROM stock s
WHERE s.quantity <> (SELECT COALESCE(SUM(b.quantity), 0) FROM stock_batch b
                     WHERE b.drug_id = s.drug_id AND b.warehouse_id = s.warehouse_id)
UNION ALL
SELECT '流水 after ≠ before + change', COUNT(*)
FROM stock_movement WHERE after_qty <> before_qty + change_qty
UNION ALL
SELECT '采购明细金额 ≠ 数量 × 单价', COUNT(*)
FROM purchase_item WHERE amount <> ROUND(quantity * unit_price, 2)
UNION ALL
SELECT '销售明细金额 ≠ 数量 × 单价', COUNT(*)
FROM sale_item WHERE amount <> ROUND(quantity * unit_price, 2)
UNION ALL
SELECT '库存出现负数', COUNT(*) FROM stock WHERE quantity < 0
UNION ALL
SELECT '批次结存出现负数', COUNT(*) FROM stock_batch WHERE quantity < 0;

-- ---------------------------------------------------------------------
-- 3. 结构统计：外键 / CHECK / 二级索引
-- ---------------------------------------------------------------------
SELECT '3. 结构统计' AS section;
SELECT '外键约束' AS 项目, COUNT(*) AS 数量 FROM information_schema.table_constraints
WHERE table_schema = DATABASE() AND constraint_type = 'FOREIGN KEY'
UNION ALL
SELECT 'CHECK 约束', COUNT(*) FROM information_schema.table_constraints
WHERE table_schema = DATABASE() AND constraint_type = 'CHECK'
UNION ALL
SELECT '二级索引', COUNT(DISTINCT index_name) FROM information_schema.statistics
WHERE table_schema = DATABASE() AND index_name <> 'PRIMARY'
UNION ALL
SELECT '数据表', COUNT(*) FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE';

-- ---------------------------------------------------------------------
-- 4. 业务样例：采购 → 库存 → 销售链路
-- ---------------------------------------------------------------------
SELECT '4. 业务样例' AS section;

-- 4.1 采购金额最高的 5 家供应商（5 表 JOIN）
SELECT s.supplier_name AS 供应商, s.credit_level AS 信用,
       COUNT(DISTINCT po.po_id) AS 采购单数,
       ROUND(SUM(pi.amount), 2) AS 采购金额
FROM purchase_item pi
JOIN purchase_order po ON po.po_id = pi.po_id
JOIN supplier s ON s.supplier_id = po.supplier_id
JOIN warehouse w ON w.warehouse_id = po.warehouse_id
JOIN employee e ON e.employee_id = po.employee_id
GROUP BY s.supplier_id, s.supplier_name, s.credit_level
ORDER BY 采购金额 DESC LIMIT 5;

-- 4.2 销售额最高的 5 种药品（5 表 JOIN）
SELECT dr.drug_name AS 药品, dr.spec AS 规格, c.category_name AS 分类,
       SUM(si.quantity) AS 销量, ROUND(SUM(si.amount), 2) AS 销售额
FROM sale_item si
JOIN sale_order so ON so.so_id = si.so_id
JOIN drug dr ON dr.drug_id = si.drug_id
JOIN drug_category c ON c.category_id = dr.category_id
JOIN warehouse w ON w.warehouse_id = so.warehouse_id
GROUP BY dr.drug_id, dr.drug_name, dr.spec, c.category_name
ORDER BY 销售额 DESC LIMIT 5;

-- 4.3 各仓库库存分布（3 表 JOIN + 聚合）
SELECT w.warehouse_name AS 仓库, w.capacity AS 库容,
       COUNT(DISTINCT s.drug_id) AS 药品品种,
       SUM(s.quantity) AS 库存总量,
       SUM(s.quantity < s.safety_stock) AS 低于安全库存
FROM warehouse w
LEFT JOIN stock s ON s.warehouse_id = w.warehouse_id
GROUP BY w.warehouse_id, w.warehouse_name, w.capacity
ORDER BY 库存总量 DESC;

-- 4.4 90 天内到期的批次（近效期预警，6 表 JOIN）
SELECT dr.drug_name AS 药品, w.warehouse_name AS 仓库, sb.batch_no AS 批号,
       sb.quantity AS 结存, sb.expiry_date AS 有效期至,
       DATEDIFF(sb.expiry_date, CURDATE()) AS 剩余天数
FROM stock_batch sb
JOIN drug dr ON dr.drug_id = sb.drug_id
JOIN warehouse w ON w.warehouse_id = sb.warehouse_id
JOIN purchase_item pi ON pi.item_id = sb.purchase_item_id
JOIN purchase_order po ON po.po_id = pi.po_id
JOIN supplier s ON s.supplier_id = po.supplier_id
WHERE sb.quantity > 0 AND sb.expiry_date < DATE_ADD(CURDATE(), INTERVAL 90 DAY)
ORDER BY sb.expiry_date LIMIT 10;

-- ---------------------------------------------------------------------
-- 5. 索引对比实验（对应"索引支持"要求）
-- ---------------------------------------------------------------------
SELECT '5. 索引对比（走 idx_move_drug_time）' AS section;
EXPLAIN SELECT move_id, move_time, change_qty FROM stock_movement
WHERE drug_id = 1 AND move_time >= '2025-01-01';
SELECT '5.2 全表扫描对照' AS section;
EXPLAIN SELECT move_id, move_time, change_qty FROM stock_movement
IGNORE INDEX (idx_move_drug_time, idx_move_time)
WHERE drug_id = 1 AND move_time >= '2025-01-01';
