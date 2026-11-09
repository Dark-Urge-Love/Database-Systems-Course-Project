-- =====================================================================
-- 在 Linux 服务器上创建应用账号与数据库
-- 用法（在 Ubuntu 里，用管理员身份执行）：
--     sudo mysql < deploy/create_app_user.sql
--     # 若 root 已设密码：
--     mysql -u root -p < deploy/create_app_user.sql
--
-- 说明：之前部署的是门诊预约系统，账号叫 clinic、库叫 outpatient_db。
--       换成药品进销存后，正式环境需要 druguser + drug_db 这一套。
--       本脚本可重复执行（IF NOT EXISTS + ALTER 覆盖密码）。
-- =====================================================================

-- 1. 应用账号：允许从任意主机连接（Windows 客户端需要远程访问）
CREATE USER IF NOT EXISTS 'druguser'@'%' IDENTIFIED WITH mysql_native_password BY 'Drug@123456';
ALTER  USER 'druguser'@'%' IDENTIFIED WITH mysql_native_password BY 'Drug@123456';

-- 2. 数据库
CREATE DATABASE IF NOT EXISTS drug_db
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_general_ci;

-- 3. 授权：只给 drug_db，不给全局权限
GRANT ALL PRIVILEGES ON `drug_db`.* TO 'druguser'@'%';
FLUSH PRIVILEGES;

-- 4. 结果确认
SELECT user AS 账号, host AS 来源, plugin AS 认证方式 FROM mysql.user WHERE user = 'druguser';
SHOW DATABASES LIKE 'drug_db';
