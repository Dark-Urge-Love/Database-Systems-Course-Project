-- =====================================================================
-- 索引脚本  index.sql（可重复执行，不会因索引已存在而报错）
-- 用途：
--   1) 补建查询性能关键索引
--   2) 作为"标志性实践 3：索引优化与性能对比"的对照实验脚本
-- =====================================================================
USE outpatient_db;
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 辅助存储过程：索引不存在才创建 / 存在才删除
-- （MySQL 的 CREATE INDEX 不支持 IF NOT EXISTS，故用存储过程封装）
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS add_index_if_missing;
DROP PROCEDURE IF EXISTS drop_index_if_exists;

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

CREATE PROCEDURE drop_index_if_exists(
    IN p_table VARCHAR(64), IN p_index VARCHAR(64))
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.statistics
         WHERE table_schema = DATABASE()
           AND table_name   = p_table
           AND index_name   = p_index
    ) THEN
        SET @ddl = CONCAT('DROP INDEX ', p_index, ' ON ', p_table);
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
        SELECT CONCAT('已删除索引 ', p_index) AS 执行结果;
    ELSE
        SELECT CONCAT('索引不存在，跳过删除 ', p_index) AS 执行结果;
    END IF;
END$$
DELIMITER ;

-- =====================================================================
-- 第一部分：补建查询性能关键索引
-- =====================================================================
-- 联合索引：按医生 + 日期区间查号源（最左前缀可用于只按 doctor_id 的查询）
CALL add_index_if_missing('schedule', 'idx_schedule_doc_date', 'doctor_id, work_date');

-- 覆盖查询：按患者查预约并按时间排序
CALL add_index_if_missing('appointment', 'idx_appt_patient_time', 'patient_id, appt_time');

-- 就诊记录按时间统计
CALL add_index_if_missing('visit', 'idx_visit_time', 'visit_time');

-- 处方时间统计
CALL add_index_if_missing('prescription', 'idx_presc_time', 'presc_time');

-- 缴费按类型/时间统计
CALL add_index_if_missing('payment', 'idx_pay_type_time', 'fee_type, pay_time');

-- =====================================================================
-- 第二部分：索引优化对照实验（报告"标志性实践 3"用）
--   步骤：删除索引 → EXPLAIN 记录执行计划 → 重建索引 → EXPLAIN 对比
-- =====================================================================

-- 【2.1 实验前】删除待测索引，制造"无索引"场景
-- CALL drop_index_if_exists('visit', 'idx_visit_doctor');
-- CALL drop_index_if_exists('appointment', 'idx_appt_status_time');

-- 【2.2 无索引时】记录执行计划与耗时（type 应为 ALL，rows 很大）
-- EXPLAIN SELECT d.doctor_name, COUNT(*) AS cnt
--   FROM visit v JOIN doctor d ON v.doctor_id = d.doctor_id
--  GROUP BY d.doctor_name ORDER BY cnt DESC LIMIT 10;
-- SHOW PROFILES;

-- 【2.3 实验后】重建索引
-- CALL add_index_if_missing('visit', 'idx_visit_doctor', 'doctor_id');
-- CALL add_index_if_missing('appointment', 'idx_appt_status_time', 'status, appt_time');

-- 【2.4 有索引时】type 应为 ref/range，rows 显著下降
-- EXPLAIN SELECT d.doctor_name, COUNT(*) AS cnt
--   FROM visit v JOIN doctor d ON v.doctor_id = d.doctor_id
--  GROUP BY d.doctor_name ORDER BY cnt DESC LIMIT 10;
-- SHOW PROFILES;

-- =====================================================================
-- 第三部分：清理辅助存储过程并输出索引清单
-- =====================================================================
DROP PROCEDURE IF EXISTS add_index_if_missing;
DROP PROCEDURE IF EXISTS drop_index_if_exists;

SELECT '--- visit 索引 ---' AS info;
SELECT index_name AS 索引名, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS 列
  FROM information_schema.statistics
 WHERE table_schema = DATABASE() AND table_name = 'visit'
 GROUP BY index_name;

SELECT '--- appointment 索引 ---' AS info;
SELECT index_name AS 索引名, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS 列
  FROM information_schema.statistics
 WHERE table_schema = DATABASE() AND table_name = 'appointment'
 GROUP BY index_name;
