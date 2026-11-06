-- =====================================================================
-- 数据部署校验脚本  verify.sql
-- 用法：mysql -u root -p < verify.sql
-- 作用：1) 统计各表行数  2) 关键不变量检查  3) 多表 join 业务统计演示
-- =====================================================================
USE outpatient_db;
SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 1. 各表行数与总量（对应"≥5 张表、≥10 万条数据"的验收证据）
-- ---------------------------------------------------------------------
SELECT '1. 数据量统计' AS section;
SELECT 'department' AS 表名, COUNT(*) AS 行数 FROM department
UNION ALL SELECT 'doctor',            COUNT(*) FROM doctor
UNION ALL SELECT 'patient',           COUNT(*) FROM patient
UNION ALL SELECT 'drug',              COUNT(*) FROM drug
UNION ALL SELECT 'schedule',          COUNT(*) FROM schedule
UNION ALL SELECT 'appointment',       COUNT(*) FROM appointment
UNION ALL SELECT 'visit',             COUNT(*) FROM visit
UNION ALL SELECT 'prescription',      COUNT(*) FROM prescription
UNION ALL SELECT 'prescription_item', COUNT(*) FROM prescription_item
UNION ALL SELECT 'payment',           COUNT(*) FROM payment;
-- 合计行（分开执行便于查看）
SELECT (SELECT COUNT(*) FROM department) + (SELECT COUNT(*) FROM doctor)
     + (SELECT COUNT(*) FROM patient) + (SELECT COUNT(*) FROM drug)
     + (SELECT COUNT(*) FROM schedule) + (SELECT COUNT(*) FROM appointment)
     + (SELECT COUNT(*) FROM visit) + (SELECT COUNT(*) FROM prescription)
     + (SELECT COUNT(*) FROM prescription_item) + (SELECT COUNT(*) FROM payment)
       AS 总数据量;

-- ---------------------------------------------------------------------
-- 2. 数据完整性检查（以下每条查询都应返回 0 行）
-- ---------------------------------------------------------------------
SELECT '2. 完整性检查（均应返回 0 行）' AS section;

-- 2.1 号源越界
SELECT '号源越界' AS 检查项, COUNT(*) AS 异常行数 FROM schedule
 WHERE remain_slots < 0 OR remain_slots > total_slots;

-- 2.2 号源与预约数不匹配
SELECT '号源与预约不一致' AS 检查项, COUNT(*) AS 异常行数 FROM (
  SELECT s.schedule_id
    FROM schedule s
    LEFT JOIN appointment a ON a.schedule_id = s.schedule_id AND a.status <> '已取消'
   GROUP BY s.schedule_id, s.total_slots, s.remain_slots
  HAVING s.remain_slots <> s.total_slots - COUNT(a.appt_id)
) t;

-- 2.3 就诊记录与预约不是 1-1（重复）
SELECT '预约被重复就诊' AS 检查项, COUNT(*) AS 异常行数 FROM (
  SELECT appt_id FROM visit GROUP BY appt_id HAVING COUNT(*) > 1
) t;

-- 2.4 就诊记录对应的预约状态不是"已就诊"
SELECT '就诊记录挂空预约' AS 检查项, COUNT(*) AS 异常行数
  FROM visit v JOIN appointment a ON v.appt_id = a.appt_id
 WHERE a.status <> '已就诊';

-- 2.5 处方明细单价与药品当前价不一致
SELECT '处方单价与药价不符' AS 检查项, COUNT(*) AS 异常行数
  FROM prescription_item i JOIN drug d ON i.drug_id = d.drug_id
 WHERE i.unit_price <> d.price;

-- 2.6 孤立外键（任一为 NULL 说明引用断裂）
SELECT '孤立外键(处方)' AS 检查项, COUNT(*) AS 异常行数
  FROM prescription p LEFT JOIN visit v ON p.visit_id = v.visit_id
 WHERE v.visit_id IS NULL
UNION ALL
SELECT '孤立外键(明细)', COUNT(*)
  FROM prescription_item i LEFT JOIN prescription p ON i.presc_id = p.presc_id
 WHERE p.presc_id IS NULL;

-- ---------------------------------------------------------------------
-- 3. 多表 join 业务统计演示（报告"标志性实践 5"素材）
-- ---------------------------------------------------------------------
SELECT '3. 业务统计演示' AS section;

-- 3.1 各科室就诊量排行（3 表 join）
SELECT d.dept_name AS 科室, COUNT(*) AS 就诊量,
       COUNT(DISTINCT v.doctor_id) AS 接诊医生数
  FROM visit v
  JOIN doctor doc     ON v.doctor_id = doc.doctor_id
  JOIN department d   ON doc.dept_id  = d.dept_id
 GROUP BY d.dept_name
 ORDER BY 就诊量 DESC
 LIMIT 10;

-- 3.2 开药金额 Top10 医生（5 表 join + 聚合）
SELECT doc.doctor_name AS 医生, d.dept_name AS 科室,
       COUNT(DISTINCT i.presc_id) AS 处方数,
       ROUND(SUM(i.quantity * i.unit_price), 2) AS 开药总额
  FROM prescription_item i
  JOIN prescription p ON i.presc_id = p.presc_id
  JOIN visit v        ON p.visit_id = v.visit_id
  JOIN doctor doc     ON v.doctor_id = doc.doctor_id
  JOIN department d   ON doc.dept_id = d.dept_id
 GROUP BY doc.doctor_id, doc.doctor_name, d.dept_name
 ORDER BY 开药总额 DESC
 LIMIT 10;

-- 3.3 药品消耗排行（N-M 联系反查）
SELECT d.drug_name AS 药品, d.spec AS 规格,
       SUM(i.quantity) AS 总消耗量,
       ROUND(SUM(i.quantity * i.unit_price), 2) AS 总销售额
  FROM prescription_item i
  JOIN drug d ON i.drug_id = d.drug_id
 GROUP BY d.drug_id, d.drug_name, d.spec
 ORDER BY 总消耗量 DESC
 LIMIT 10;

-- 3.4 预约状态分布
SELECT status AS 预约状态, COUNT(*) AS 数量,
       ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM appointment), 2) AS 占比百分比
  FROM appointment
 GROUP BY status
 ORDER BY 数量 DESC;
