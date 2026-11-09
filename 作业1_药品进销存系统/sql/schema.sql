-- =====================================================================
-- 药品进销存管理系统
-- 数据库建表脚本  schema.sql
-- 目标环境：MySQL 8.0（Linux 虚拟机） / 兼容 MySQL 5.7
-- 字符集：utf8mb4    引擎：InnoDB
--
-- 说明：本文件为手工排版版；sql/schema_generated.sql 由
--       tools/model_gen.py 依据 sql/er_model.json 自动生成。
--       两者结构可用以下命令逐表比对：
--       python tools/model_gen.py --verify 127.0.0.1 3306 root 123456
-- =====================================================================

SET NAMES utf8mb4;

DROP DATABASE IF EXISTS drug_db;
CREATE DATABASE drug_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;
USE drug_db;

-- ---------------------------------------------------------------------
-- 1. 药品分类表
--    药品的上级分类，一个分类下可以有多种药品（1-N）。
-- ---------------------------------------------------------------------
CREATE TABLE drug_category (
  `category_id`   INT AUTO_INCREMENT COMMENT '分类ID',
  `category_name` VARCHAR(50)  NOT NULL COMMENT '分类名称',
  `category_code` VARCHAR(20)  NOT NULL COMMENT '分类编码',
  `description`   VARCHAR(200) COMMENT '分类说明',
  `status`        TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',
  PRIMARY KEY (`category_id`),
  UNIQUE KEY uk_category_name (category_name),
  UNIQUE KEY uk_category_code (category_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品分类表';

-- ---------------------------------------------------------------------
-- 2. 供应商表
--    采购业务的往来单位。名称与许可证号唯一，防止同一家供应商重复建档。
-- ---------------------------------------------------------------------
CREATE TABLE supplier (
  `supplier_id`    INT AUTO_INCREMENT COMMENT '供应商ID',
  `supplier_name`  VARCHAR(100) NOT NULL COMMENT '供应商名称',
  `contact_person` VARCHAR(50) COMMENT '联系人',
  `phone`          VARCHAR(20) COMMENT '联系电话',
  `address`        VARCHAR(200) COMMENT '联系地址',
  `license_no`     VARCHAR(50) COMMENT '药品经营许可证号',
  `credit_level`   VARCHAR(10) NOT NULL DEFAULT 'B' COMMENT '信用等级：A/B/C',
  `cooperate_date` DATE COMMENT '合作起始日期',
  `status`         TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1合作中 0已停用',
  PRIMARY KEY (`supplier_id`),
  UNIQUE KEY uk_supplier_name (supplier_name),
  UNIQUE KEY uk_supplier_license (license_no),
  KEY idx_supplier_credit (credit_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商表';

-- ---------------------------------------------------------------------
-- 3. 仓库表
--    药品按仓库分开存放，库存、批次、采购入库、销售出库都落到具体仓库。
-- ---------------------------------------------------------------------
CREATE TABLE warehouse (
  `warehouse_id`   INT AUTO_INCREMENT COMMENT '仓库ID',
  `warehouse_name` VARCHAR(50) NOT NULL COMMENT '仓库名称',
  `location`       VARCHAR(100) COMMENT '仓库地址',
  `capacity`       INT NOT NULL DEFAULT 0 COMMENT '库容(件)',
  `manager`        VARCHAR(50) COMMENT '仓库负责人',
  `status`         TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',
  PRIMARY KEY (`warehouse_id`),
  UNIQUE KEY uk_warehouse_name (warehouse_name),
  CONSTRAINT ck_warehouse_capacity CHECK (capacity >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库表';

-- ---------------------------------------------------------------------
-- 4. 员工表（warehouse_id → warehouse）
--    采购员、仓管员、销售员、质检员；每人在一个仓库任职。
-- ---------------------------------------------------------------------
CREATE TABLE employee (
  `employee_id`  INT AUTO_INCREMENT COMMENT '员工ID',
  `emp_no`       VARCHAR(20) NOT NULL COMMENT '工号',
  `emp_name`     VARCHAR(50) NOT NULL COMMENT '姓名',
  `gender`       CHAR(1)     NOT NULL COMMENT '性别：男/女',
  `position`     VARCHAR(20) NOT NULL COMMENT '岗位：采购员/仓管员/销售员/质检员',
  `warehouse_id` INT         NOT NULL COMMENT '所属仓库ID',
  `phone`        VARCHAR(20) COMMENT '联系电话',
  `hire_date`    DATE COMMENT '入职日期',
  `status`       TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1在职 0离职',
  PRIMARY KEY (`employee_id`),
  UNIQUE KEY uk_emp_no (emp_no),
  KEY idx_emp_warehouse (warehouse_id),
  KEY idx_emp_position (position),
  CONSTRAINT fk_emp_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='员工表';

-- ---------------------------------------------------------------------
-- 5. 药品表（category_id → drug_category）
--    drug_code 唯一；同一「名称 + 规格」不得重复建档（uk_drug_name_spec）。
--    采购价 / 零售价非负由 CHECK 约束兜底。
-- ---------------------------------------------------------------------
CREATE TABLE drug (
  `drug_id`           INT AUTO_INCREMENT COMMENT '药品ID',
  `drug_code`         VARCHAR(20)  NOT NULL COMMENT '药品编码',
  `drug_name`         VARCHAR(100) NOT NULL COMMENT '药品名称',
  `category_id`       INT          NOT NULL COMMENT '分类ID',
  `spec`              VARCHAR(50)  NOT NULL COMMENT '规格',
  `unit`              VARCHAR(10)  NOT NULL DEFAULT '盒' COMMENT '单位',
  `manufacturer`      VARCHAR(100) COMMENT '生产厂家',
  `approval_no`       VARCHAR(50)  COMMENT '批准文号',
  `purchase_price`    DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '采购价(元)',
  `retail_price`      DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '零售价(元)',
  `is_rx`             TINYINT      NOT NULL DEFAULT 1 COMMENT '是否处方药：1是 0否',
  `shelf_life_months` INT          NOT NULL DEFAULT 24 COMMENT '保质期(月)',
  `status`            TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：1在售 0停用',
  PRIMARY KEY (`drug_id`),
  UNIQUE KEY uk_drug_code (drug_code),
  UNIQUE KEY uk_drug_name_spec (drug_name, spec),
  KEY idx_drug_category (category_id),
  KEY idx_drug_name (drug_name),
  CONSTRAINT fk_drug_category FOREIGN KEY (category_id) REFERENCES drug_category (category_id),
  CONSTRAINT ck_drug_price CHECK (purchase_price >= 0 AND retail_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品表';

-- ---------------------------------------------------------------------
-- 6. 采购单（supplier_id / warehouse_id / employee_id 三个外键）
--    total_amount 为明细金额汇总，由入库事务统一维护。
-- ---------------------------------------------------------------------
CREATE TABLE purchase_order (
  `po_id`          INT AUTO_INCREMENT COMMENT '采购单ID',
  `po_no`          VARCHAR(24)   NOT NULL COMMENT '采购单号',
  `supplier_id`    INT           NOT NULL COMMENT '供应商ID',
  `warehouse_id`   INT           NOT NULL COMMENT '入库仓库ID',
  `employee_id`    INT           NOT NULL COMMENT '采购员ID',
  `order_date`     DATE          NOT NULL COMMENT '下单日期',
  `expect_date`    DATE COMMENT '预计到货日期',
  `total_amount`   DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '采购金额(元)',
  `status`         VARCHAR(10)   NOT NULL DEFAULT '待入库' COMMENT '状态：待入库/已入库/已取消',
  PRIMARY KEY (`po_id`),
  UNIQUE KEY uk_po_no (po_no),
  KEY idx_po_supplier (supplier_id),
  KEY idx_po_date (order_date),
  KEY idx_po_status (status),
  CONSTRAINT fk_po_supplier FOREIGN KEY (supplier_id) REFERENCES supplier (supplier_id),
  CONSTRAINT fk_po_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_po_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),
  CONSTRAINT ck_po_amount CHECK (total_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购单表';

-- ---------------------------------------------------------------------
-- 7. 采购明细（po_id → purchase_order，drug_id → drug）
--    拆解「采购单 N-M 药品」；一行明细对应一个入库批次（批号/生产日期/有效期）。
-- ---------------------------------------------------------------------
CREATE TABLE purchase_item (
  `item_id`         INT AUTO_INCREMENT COMMENT '明细ID',
  `po_id`           INT           NOT NULL COMMENT '采购单ID',
  `drug_id`         INT           NOT NULL COMMENT '药品ID',
  `quantity`        INT           NOT NULL COMMENT '采购数量',
  `unit_price`      DECIMAL(8,2)  NOT NULL COMMENT '采购单价(元)',
  `amount`          DECIMAL(10,2) NOT NULL COMMENT '采购金额(元)',
  `batch_no`        VARCHAR(30)   NOT NULL COMMENT '批号',
  `production_date` DATE COMMENT '生产日期',
  `expiry_date`     DATE          NOT NULL COMMENT '有效期至',
  PRIMARY KEY (`item_id`),
  KEY idx_pi_po (po_id),
  KEY idx_pi_drug (drug_id),
  KEY idx_pi_batch (batch_no),
  CONSTRAINT fk_pi_po FOREIGN KEY (po_id) REFERENCES purchase_order (po_id),
  CONSTRAINT fk_pi_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT ck_pi_qty CHECK (quantity > 0),
  CONSTRAINT ck_pi_price CHECK (unit_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购明细表';

-- ---------------------------------------------------------------------
-- 8. 库存表（drug_id + warehouse_id 唯一）
--    一个药品在一个仓库只有一行结存；销售出库时在此行上加行锁。
-- ---------------------------------------------------------------------
CREATE TABLE stock (
  `stock_id`      INT AUTO_INCREMENT COMMENT '库存ID',
  `drug_id`       INT NOT NULL COMMENT '药品ID',
  `warehouse_id`  INT NOT NULL COMMENT '仓库ID',
  `quantity`      INT NOT NULL DEFAULT 0 COMMENT '库存数量',
  `safety_stock`  INT NOT NULL DEFAULT 50 COMMENT '安全库存',
  `update_time`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`stock_id`),
  UNIQUE KEY uk_stock_drug_wh (drug_id, warehouse_id),
  KEY idx_stock_warehouse (warehouse_id),
  CONSTRAINT fk_stock_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT ck_stock_qty CHECK (quantity >= 0),
  CONSTRAINT ck_stock_safety CHECK (safety_stock >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存表';

-- ---------------------------------------------------------------------
-- 9. 库存批次表
--    purchase_item_id 唯一 → 与采购明细构成 1-1 联系：一次采购入库生成一个批次。
--    批次保留生产日期与有效期，用于近效期预警。
-- ---------------------------------------------------------------------
CREATE TABLE stock_batch (
  `batch_id`         INT AUTO_INCREMENT COMMENT '批次ID',
  `purchase_item_id` INT           NOT NULL COMMENT '采购明细ID(1-1)',
  `drug_id`          INT           NOT NULL COMMENT '药品ID',
  `warehouse_id`     INT           NOT NULL COMMENT '仓库ID',
  `batch_no`         VARCHAR(30)   NOT NULL COMMENT '批号',
  `production_date`  DATE COMMENT '生产日期',
  `expiry_date`      DATE          NOT NULL COMMENT '有效期至',
  `quantity`         INT           NOT NULL DEFAULT 0 COMMENT '批次结存数量',
  `purchase_price`   DECIMAL(8,2)  NOT NULL COMMENT '批次采购价(元)',
  `in_date`          DATE          NOT NULL COMMENT '入库日期',
  PRIMARY KEY (`batch_id`),
  UNIQUE KEY uk_batch_purchase (purchase_item_id),
  UNIQUE KEY uk_batch_no (drug_id, warehouse_id, batch_no),
  KEY idx_batch_expiry (expiry_date),
  KEY idx_batch_drug (drug_id),
  CONSTRAINT fk_batch_item FOREIGN KEY (purchase_item_id) REFERENCES purchase_item (item_id),
  CONSTRAINT fk_batch_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_batch_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT ck_batch_qty CHECK (quantity >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存批次表';

-- ---------------------------------------------------------------------
-- 10. 销售单（warehouse_id / employee_id）
--     客户为连锁药店、医院、诊所或个人；出库减少对应仓库库存。
-- ---------------------------------------------------------------------
CREATE TABLE sale_order (
  `so_id`         INT AUTO_INCREMENT COMMENT '销售单ID',
  `so_no`         VARCHAR(24)   NOT NULL COMMENT '销售单号',
  `customer_name` VARCHAR(100)  NOT NULL COMMENT '客户名称',
  `customer_type` VARCHAR(20)   NOT NULL COMMENT '客户类型：连锁药店/医院/诊所/个人',
  `warehouse_id`  INT           NOT NULL COMMENT '出库仓库ID',
  `employee_id`   INT           NOT NULL COMMENT '销售员ID',
  `sale_date`     DATE          NOT NULL COMMENT '销售日期',
  `total_amount`  DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '销售金额(元)',
  `status`        VARCHAR(10)   NOT NULL DEFAULT '已出库' COMMENT '状态：已出库/已退货',
  PRIMARY KEY (`so_id`),
  UNIQUE KEY uk_so_no (so_no),
  KEY idx_so_customer (customer_name),
  KEY idx_so_date (sale_date),
  KEY idx_so_warehouse (warehouse_id),
  CONSTRAINT fk_so_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_so_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),
  CONSTRAINT ck_so_amount CHECK (total_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售单表';

-- ---------------------------------------------------------------------
-- 11. 销售明细（so_id → sale_order，drug_id → drug）
--     拆解「销售单 N-M 药品」。
-- ---------------------------------------------------------------------
CREATE TABLE sale_item (
  `item_id`    INT AUTO_INCREMENT COMMENT '明细ID',
  `so_id`      INT           NOT NULL COMMENT '销售单ID',
  `drug_id`    INT           NOT NULL COMMENT '药品ID',
  `quantity`   INT           NOT NULL COMMENT '销售数量',
  `unit_price` DECIMAL(8,2)  NOT NULL COMMENT '销售单价(元)',
  `amount`     DECIMAL(10,2) NOT NULL COMMENT '销售金额(元)',
  PRIMARY KEY (`item_id`),
  KEY idx_si_so (so_id),
  KEY idx_si_drug (drug_id),
  CONSTRAINT fk_si_so FOREIGN KEY (so_id) REFERENCES sale_order (so_id),
  CONSTRAINT fk_si_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT ck_si_qty CHECK (quantity > 0),
  CONSTRAINT ck_si_amount CHECK (amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售明细表';

-- ---------------------------------------------------------------------
-- 12. 库存流水表
--     每次库存变动记一条：正数为入库、负数为出库；
--     CHECK (after_qty = before_qty + change_qty) 保证账面连续可追溯。
--     这是行数最多的表，也是索引对比演示的主表。
-- ---------------------------------------------------------------------
CREATE TABLE stock_movement (
  `move_id`      BIGINT AUTO_INCREMENT COMMENT '流水ID',
  `drug_id`      INT          NOT NULL COMMENT '药品ID',
  `warehouse_id` INT          NOT NULL COMMENT '仓库ID',
  `move_type`    VARCHAR(20)  NOT NULL COMMENT '变动类型：采购入库/销售出库/盘点调整/退货入库',
  `ref_no`       VARCHAR(24)  COMMENT '关联单号',
  `change_qty`   INT          NOT NULL COMMENT '变动数量(正入负出)',
  `before_qty`   INT          NOT NULL COMMENT '变动前库存',
  `after_qty`    INT          NOT NULL COMMENT '变动后库存',
  `batch_no`     VARCHAR(30)  COMMENT '批号',
  `employee_id`  INT          NOT NULL COMMENT '经办人ID',
  `move_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
  PRIMARY KEY (`move_id`),
  KEY idx_move_drug_time (drug_id, move_time),
  KEY idx_move_type_time (move_type, move_time),
  KEY idx_move_time (move_time),
  CONSTRAINT fk_move_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_move_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_move_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),
  CONSTRAINT ck_move_after CHECK (after_qty >= 0),
  CONSTRAINT ck_move_balance CHECK (after_qty = before_qty + change_qty)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存流水表';

-- ---------------------------------------------------------------------
-- 13. 访问性能日志表
--     多客户端并发访问时逐次记录响应耗时。刻意不设外键：
--     日志只做弱关联，避免影响药品主数据的维护；drug_id 建普通索引。
-- ---------------------------------------------------------------------
CREATE TABLE access_log (
  `log_id`     BIGINT AUTO_INCREMENT COMMENT '日志ID',
  `scene`      VARCHAR(20)  NOT NULL COMMENT '场景：sale_out 销售出库',
  `client_no`  INT          NOT NULL COMMENT '客户端序号',
  `drug_id`    INT COMMENT '药品ID(弱关联，不设外键)',
  `qty`        INT          NOT NULL COMMENT '本次出库数量',
  `ok`         TINYINT      NOT NULL COMMENT '结果：1成功 0库存不足',
  `elapsed_ms` DECIMAL(8,2) NOT NULL COMMENT '本次访问耗时(ms)',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '访问时间',
  PRIMARY KEY (`log_id`),
  KEY idx_log_time (created_at),
  KEY idx_log_drug (drug_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='访问性能日志表';

-- =====================================================================
-- 说明：界面上的「业务表单」（如召回登记、盘点差异表）由客户端在运行时
--       动态 CREATE TABLE bizform_xxx，属于课程要求的"创建/删除数据表"演示，
--       不写入本脚本，避免污染核心 13 张业务表的结构。
-- =====================================================================
