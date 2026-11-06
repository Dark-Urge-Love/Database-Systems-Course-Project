-- =====================================================================
-- 智慧医疗 · 社区门诊预约与处方管理系统
-- 数据库建表脚本  schema.sql
-- 目标环境：MySQL 8.0（Linux 虚拟机） / 兼容 MySQL 5.7
-- 字符集：utf8mb4
-- =====================================================================

SET NAMES utf8mb4;

DROP DATABASE IF EXISTS outpatient_db;
CREATE DATABASE outpatient_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;
USE outpatient_db;

-- ---------------------------------------------------------------------
-- 1. 科室表
-- ---------------------------------------------------------------------
CREATE TABLE department (
  dept_id    INT AUTO_INCREMENT COMMENT '科室ID',
  dept_name  VARCHAR(50)  NOT NULL COMMENT '科室名称',
  dept_type  VARCHAR(20)  NOT NULL COMMENT '科室类型：内科/外科/专科',
  location   VARCHAR(50)  COMMENT '科室位置',
  phone      VARCHAR(20)  COMMENT '科室电话',
  status     TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',
  PRIMARY KEY (dept_id),
  UNIQUE KEY uk_dept_name (dept_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科室表';

-- ---------------------------------------------------------------------
-- 2. 患者表
-- ---------------------------------------------------------------------
CREATE TABLE patient (
  patient_id      INT AUTO_INCREMENT COMMENT '患者ID',
  patient_name    VARCHAR(50) NOT NULL COMMENT '姓名',
  gender          CHAR(1)     NOT NULL COMMENT '性别：男/女',
  birth_date      DATE        COMMENT '出生日期',
  phone           VARCHAR(20) NOT NULL COMMENT '手机号',
  id_card         VARCHAR(18) NOT NULL COMMENT '身份证号',
  medical_card_no VARCHAR(20) COMMENT '就诊卡号',
  create_time     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '建档时间',
  status          TINYINT     NOT NULL DEFAULT 1 COMMENT '状态：1正常 0注销',
  PRIMARY KEY (patient_id),
  UNIQUE KEY uk_patient_phone  (phone),
  UNIQUE KEY uk_patient_idcard (id_card),
  KEY idx_patient_name (patient_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='患者表';

-- ---------------------------------------------------------------------
-- 3. 医生表（dept_id 外键 → department）
-- ---------------------------------------------------------------------
CREATE TABLE doctor (
  doctor_id   INT AUTO_INCREMENT COMMENT '医生ID',
  doctor_name VARCHAR(50) NOT NULL COMMENT '姓名',
  gender      CHAR(1)     NOT NULL COMMENT '性别：男/女',
  title       VARCHAR(20) NOT NULL COMMENT '职称：主任医师/副主任医师/主治医师/住院医师',
  dept_id     INT         NOT NULL COMMENT '所属科室ID',
  phone       VARCHAR(20) COMMENT '联系电话',
  hire_date   DATE        COMMENT '入职日期',
  intro       VARCHAR(200) COMMENT '擅长简介',
  status      TINYINT     NOT NULL DEFAULT 1 COMMENT '状态：1在职 0离职',
  PRIMARY KEY (doctor_id),
  KEY idx_doctor_dept (dept_id),
  CONSTRAINT fk_doctor_dept FOREIGN KEY (dept_id)
      REFERENCES department (dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生表';

-- ---------------------------------------------------------------------
-- 4. 排班表（doctor_id 外键 → doctor）
-- ---------------------------------------------------------------------
CREATE TABLE schedule (
  schedule_id  INT AUTO_INCREMENT COMMENT '排班ID',
  doctor_id    INT NOT NULL COMMENT '医生ID',
  work_date    DATE NOT NULL COMMENT '出诊日期',
  period       VARCHAR(10) NOT NULL COMMENT '时段：上午/下午',
  total_slots  INT NOT NULL COMMENT '总号源数',
  remain_slots INT NOT NULL COMMENT '剩余号源数',
  reg_fee      DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '挂号费(元)',
  room         VARCHAR(20) COMMENT '诊室',
  PRIMARY KEY (schedule_id),
  UNIQUE KEY uk_schedule (doctor_id, work_date, period),
  KEY idx_schedule_date (work_date),
  CONSTRAINT fk_schedule_doctor FOREIGN KEY (doctor_id)
      REFERENCES doctor (doctor_id),
  CONSTRAINT ck_schedule_slots CHECK (remain_slots >= 0 AND remain_slots <= total_slots)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生排班表';

-- ---------------------------------------------------------------------
-- 5. 预约挂号表（patient_id → patient, schedule_id → schedule）
-- ---------------------------------------------------------------------
CREATE TABLE appointment (
  appt_id     INT AUTO_INCREMENT COMMENT '预约ID',
  patient_id  INT NOT NULL COMMENT '患者ID',
  schedule_id INT NOT NULL COMMENT '排班ID',
  appt_time   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '预约时间',
  queue_no    INT NOT NULL COMMENT '就诊顺序号',
  status      VARCHAR(10) NOT NULL DEFAULT '已预约' COMMENT '状态：已预约/已就诊/已取消/爽约',
  PRIMARY KEY (appt_id),
  KEY idx_appt_patient (patient_id),
  KEY idx_appt_schedule (schedule_id),
  KEY idx_appt_status_time (status, appt_time),
  CONSTRAINT fk_appt_patient  FOREIGN KEY (patient_id)  REFERENCES patient (patient_id),
  CONSTRAINT fk_appt_schedule FOREIGN KEY (schedule_id) REFERENCES schedule (schedule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预约挂号表';

-- ---------------------------------------------------------------------
-- 6. 就诊记录表（appt_id 唯一 → 实现与 appointment 的 1-1 联系）
-- ---------------------------------------------------------------------
CREATE TABLE visit (
  visit_id        INT AUTO_INCREMENT COMMENT '就诊ID',
  appt_id         INT NOT NULL COMMENT '预约ID(唯一,1-1)',
  patient_id      INT NOT NULL COMMENT '患者ID',
  doctor_id       INT NOT NULL COMMENT '医生ID',
  visit_time      DATETIME NOT NULL COMMENT '就诊时间',
  chief_complaint VARCHAR(500) COMMENT '主诉',
  diagnosis       VARCHAR(500) COMMENT '诊断',
  advice          VARCHAR(500) COMMENT '医嘱',
  PRIMARY KEY (visit_id),
  UNIQUE KEY uk_visit_appt (appt_id),
  KEY idx_visit_patient (patient_id),
  KEY idx_visit_doctor (doctor_id),
  CONSTRAINT fk_visit_appt    FOREIGN KEY (appt_id)    REFERENCES appointment (appt_id),
  CONSTRAINT fk_visit_patient FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
  CONSTRAINT fk_visit_doctor  FOREIGN KEY (doctor_id)  REFERENCES doctor (doctor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='就诊记录表';

-- ---------------------------------------------------------------------
-- 7. 处方表（visit_id → visit）
-- ---------------------------------------------------------------------
CREATE TABLE prescription (
  presc_id   INT AUTO_INCREMENT COMMENT '处方ID',
  visit_id   INT NOT NULL COMMENT '就诊ID',
  presc_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开方时间',
  presc_type VARCHAR(10) NOT NULL COMMENT '处方类型：西药/中药',
  status     VARCHAR(10) NOT NULL DEFAULT '已开方' COMMENT '状态：已开方/已缴费/已作废',
  PRIMARY KEY (presc_id),
  KEY idx_presc_visit (visit_id),
  CONSTRAINT fk_presc_visit FOREIGN KEY (visit_id) REFERENCES visit (visit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='处方表';

-- ---------------------------------------------------------------------
-- 8. 药品表
-- ---------------------------------------------------------------------
CREATE TABLE drug (
  drug_id      INT AUTO_INCREMENT COMMENT '药品ID',
  drug_name    VARCHAR(100) NOT NULL COMMENT '药品名称',
  spec         VARCHAR(50)  COMMENT '规格',
  unit         VARCHAR(10)  COMMENT '单位',
  manufacturer VARCHAR(100) COMMENT '生产厂家',
  price        DECIMAL(8,2) NOT NULL COMMENT '当前单价(元)',
  stock        INT NOT NULL DEFAULT 0 COMMENT '库存量',
  is_rx        TINYINT NOT NULL DEFAULT 1 COMMENT '是否处方药：1是 0否',
  status       TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1在用 0停用',
  PRIMARY KEY (drug_id),
  KEY idx_drug_name (drug_name),
  CONSTRAINT ck_drug_stock CHECK (stock >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品表';

-- ---------------------------------------------------------------------
-- 9. 处方明细表（拆分 处方 N-M 药品 联系）
-- ---------------------------------------------------------------------
CREATE TABLE prescription_item (
  item_id    INT AUTO_INCREMENT COMMENT '明细ID',
  presc_id   INT NOT NULL COMMENT '处方ID',
  drug_id    INT NOT NULL COMMENT '药品ID',
  quantity   INT NOT NULL COMMENT '数量',
  usage_note VARCHAR(100) COMMENT '用法用量',
  unit_price DECIMAL(8,2) NOT NULL COMMENT '开方时单价(元)',
  PRIMARY KEY (item_id),
  KEY idx_item_presc (presc_id),
  KEY idx_item_drug  (drug_id),
  CONSTRAINT fk_item_presc FOREIGN KEY (presc_id) REFERENCES prescription (presc_id),
  CONSTRAINT fk_item_drug  FOREIGN KEY (drug_id)  REFERENCES drug (drug_id),
  CONSTRAINT ck_item_qty   CHECK (quantity > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='处方明细表';

-- ---------------------------------------------------------------------
-- 10. 缴费记录表（visit_id → visit）
-- ---------------------------------------------------------------------
CREATE TABLE payment (
  pay_id     INT AUTO_INCREMENT COMMENT '缴费ID',
  visit_id   INT NOT NULL COMMENT '就诊ID',
  fee_type   VARCHAR(10) NOT NULL COMMENT '费用类型：挂号费/药费',
  amount     DECIMAL(10,2) NOT NULL COMMENT '金额(元)',
  pay_method VARCHAR(10) COMMENT '支付方式：现金/微信/支付宝/医保',
  pay_time   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '支付时间',
  status     VARCHAR(10) NOT NULL DEFAULT '已支付' COMMENT '状态：已支付/已退款',
  PRIMARY KEY (pay_id),
  KEY idx_pay_visit (visit_id),
  CONSTRAINT fk_pay_visit FOREIGN KEY (visit_id) REFERENCES visit (visit_id),
  CONSTRAINT ck_pay_amount CHECK (amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='缴费记录表';
