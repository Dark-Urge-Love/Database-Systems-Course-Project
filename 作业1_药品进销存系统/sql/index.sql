-- =====================================================================
-- 索引脚本  index.sql（可重复执行，不会因索引已存在而报错）
-- 用途：
--   1) 补建查询性能关键索引（与 schema.sql 中声明的二级索引保持一致，
--      也可用于"删掉索引后重建"的恢复演示）
--   2) 作为"索引优化与性能对比"的对照实验脚本
--
-- 【知识点：索引到底是什么】
-- 索引就像书的目录。没有目录，找"阿莫西林"要一页页翻（全表扫描，type=ALL）；
-- 有目录，直接翻到那一页（索引查找，type=ref/range）。
-- 代价：索引本身要占磁盘、每次 INSERT/UPDATE 都要维护索引（写变慢），
-- 所以不是越多越好，只在"经常用来查/排序/连接"的列上建
-- =====================================================================

USE drug_db;
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 辅助存储过程：索引不存在才创建
-- （MySQL 的 CREATE INDEX 不支持 IF NOT EXISTS，故用存储过程封装）
-- ---------------------------------------------------------------------
-- 【知识点：存储过程 PROCEDURE】
-- 把一段 SQL 逻辑存进数据库，起个名字，以后用 CALL 名字(...) 调用。
-- 类似编程语言里的"函数"，能带参数、能用 IF/循环。这里用它实现"幂等"（重复执行不报错）
DROP PROCEDURE IF EXISTS add_index_if_missing;

-- 【知识点：DELIMITER 改分隔符】
-- MySQL 默认用分号 ; 表示"一条语句结束"。但存储过程内部有多条语句、也有分号，
-- 直接写会让客户端以为"到第一个分号就结束了"。
-- 所以这里临时把结束符改成 $$，等存储过程整个写完，再改回分号
DELIMITER $$

-- 创建存储过程，三个入参：表名、索引名、列（列可能是 "drug_id, move_time" 这种多列）
-- IN 表示"输入参数"
CREATE PROCEDURE add_index_if_missing(
    IN p_table VARCHAR(64), IN p_index VARCHAR(64), IN p_cols VARCHAR(255))
BEGIN

    -- 【知识点：IF NOT EXISTS (...) THEN ... END IF】
    -- 查询 information_schema.statistics（记录所有索引的元数据表）：
    -- 如果查不到这个索引，说明还没建，就去建
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
         WHERE table_schema = DATABASE()   -- 只在当前库里找
           AND table_name   = p_table      -- 表名匹配
           AND index_name   = p_index      -- 索引名匹配
    ) THEN

        -- 【知识点：动态 SQL】
        -- 索引名、列名不能用参数占位符（DDL 不支持），只能拼字符串。
        -- CONCAT 把各段拼成一条完整语句，如：CREATE INDEX idx_x ON drug (drug_name)
        SET @ddl = CONCAT('CREATE INDEX ', p_index, ' ON ', p_table, ' (', p_cols, ')');

        -- 【知识点：PREPARE / EXECUTE / DEALLOCATE 三件套】
        -- MySQL 不能直接执行"字符串形式的 SQL"，必须先 PREPARE（预编译成语句），
        -- 再 EXECUTE（执行），最后 DEALLOCATE（释放），否则会一直占着资源
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

        -- 输出一行结果，方便执行脚本时看进度
        SELECT CONCAT('已创建索引 ', p_index, ' ON ', p_table) AS 执行结果;

    -- 索引已存在
    ELSE

        -- 跳过，只提示一下
        SELECT CONCAT('索引已存在，跳过 ', p_index) AS 执行结果;

    -- 结束 IF
    END IF;

-- 存储过程结束（$$ 是刚才设置的结束符）
END$$

-- 把结束符改回默认的分号
DELIMITER ;

-- ---------------------------------------------------------------------
-- 1. 主数据表
-- ---------------------------------------------------------------------
-- 调用上面建的存储过程，逐个建索引（已存在会自动跳过）
-- 供应商信用等级（按等级筛供应商）
CALL add_index_if_missing('supplier',      'idx_supplier_credit',  'credit_level');

-- 员工所属仓库（按仓库查人）
CALL add_index_if_missing('employee',      'idx_emp_warehouse',    'warehouse_id');

-- 员工岗位（按岗位筛销售员/采购员）
CALL add_index_if_missing('employee',      'idx_emp_position',     'position');

-- 药品分类（按分类筛药品）
CALL add_index_if_missing('drug',          'idx_drug_category',    'category_id');

-- 药品名称（药名模糊查询 / 排序）
CALL add_index_if_missing('drug',          'idx_drug_name',        'drug_name');

-- ---------------------------------------------------------------------
-- 2. 单据表
-- ---------------------------------------------------------------------
-- 采购单：按供应商查、按日期排序、按状态筛选
CALL add_index_if_missing('purchase_order', 'idx_po_supplier', 'supplier_id');
CALL add_index_if_missing('purchase_order', 'idx_po_date',     'order_date');
CALL add_index_if_missing('purchase_order', 'idx_po_status',   'status');

-- 采购明细：按单查明细、按药查采购历史、按批号溯源
CALL add_index_if_missing('purchase_item',  'idx_pi_po',       'po_id');
CALL add_index_if_missing('purchase_item',  'idx_pi_drug',     'drug_id');
CALL add_index_if_missing('purchase_item',  'idx_pi_batch',    'batch_no');

-- 销售单：按客户查、按日期排序、按仓库统计
CALL add_index_if_missing('sale_order',     'idx_so_customer', 'customer_name');
CALL add_index_if_missing('sale_order',     'idx_so_date',     'sale_date');
CALL add_index_if_missing('sale_order',     'idx_so_warehouse','warehouse_id');

-- 销售明细：按单查明细、按药统计销量
CALL add_index_if_missing('sale_item',      'idx_si_so',       'so_id');
CALL add_index_if_missing('sale_item',      'idx_si_drug',     'drug_id');

-- ---------------------------------------------------------------------
-- 3. 库存与流水（性能对比实验的主战场）
--    idx_move_drug_time：按药品 + 时间查流水（复合索引，最左前缀）
--    idx_move_time     ：按时间范围统计（索引对比用例 2）
-- ---------------------------------------------------------------------
-- 库存：按仓库统计
CALL add_index_if_missing('stock',          'idx_stock_warehouse', 'warehouse_id');

-- 批次：按有效期查近效期、按药品查批次
CALL add_index_if_missing('stock_batch',    'idx_batch_expiry',    'expiry_date');
CALL add_index_if_missing('stock_batch',    'idx_batch_drug',      'drug_id');

-- 【知识点：复合索引的列顺序很关键】
-- 'drug_id, move_time' 这个顺序，适合"先按药品过滤、再按时间范围/排序"的查询。
-- 如果把顺序写成 'move_time, drug_id'，那"只按 drug_id 查"就用不上它了（最左前缀原则）
CALL add_index_if_missing('stock_movement', 'idx_move_drug_time',  'drug_id, move_time');

-- 按变动类型 + 时间统计（报表）
CALL add_index_if_missing('stock_movement', 'idx_move_type_time',  'move_type, move_time');

-- 单纯按时间范围查（没有 drug_id 条件时用这个）
CALL add_index_if_missing('stock_movement', 'idx_move_time',       'move_time');

-- 访问日志：按时间查最近记录、按药品查访问情况
CALL add_index_if_missing('access_log',     'idx_log_time',        'created_at');
CALL add_index_if_missing('access_log',     'idx_log_drug',        'drug_id');

-- ---------------------------------------------------------------------
-- 清理辅助存储过程
-- ---------------------------------------------------------------------
-- 用完就删，不在库里留垃圾（下次执行脚本会重新建）
DROP PROCEDURE IF EXISTS add_index_if_missing;

-- ---------------------------------------------------------------------
-- 当前索引一览
-- ---------------------------------------------------------------------
-- 查一下现在到底有哪些索引，确认脚本生效
SELECT table_name AS 表, index_name AS 索引名,
       -- 【知识点：GROUP_CONCAT】把分组内多行的值拼成一个字符串。
       -- 复合索引有多列，这里按索引内列的顺序（seq_in_index）拼成 "drug_id,move_time"
       GROUP_CONCAT(column_name ORDER BY seq_in_index) AS 列,
       -- 数一下这个索引有几列（1 = 单列索引，2+ = 复合索引）
       COUNT(DISTINCT seq_in_index) AS 列数
FROM information_schema.statistics
-- 排除主键（主键索引单独算）
WHERE table_schema = DATABASE() AND index_name <> 'PRIMARY'
-- 按表名 + 索引名分组，一个索引一行
GROUP BY table_name, index_name
-- 按表名、索引名排序，看起来整齐
ORDER BY table_name, index_name;
