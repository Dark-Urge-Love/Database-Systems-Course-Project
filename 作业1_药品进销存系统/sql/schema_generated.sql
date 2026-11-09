-- =============================================================
-- 本文件由 tools/model_gen.py 根据 sql/er_model.json 自动生成，
-- 请勿手改；修改模型后重新生成。
-- =============================================================

SET NAMES utf8mb4;

DROP DATABASE IF EXISTS drug_db;
CREATE DATABASE drug_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;
USE drug_db;

-- ---- drug_category（药品分类）----
CREATE TABLE drug_category (
  `category_id` INT AUTO_INCREMENT COMMENT '分类ID',
  `category_name` VARCHAR(50) NOT NULL COMMENT '分类名称',
  `category_code` VARCHAR(20) NOT NULL COMMENT '分类编码',
  `description` VARCHAR(200) COMMENT '分类说明',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',
  PRIMARY KEY (`category_id`),
  UNIQUE KEY uk_category_name (category_name),
  UNIQUE KEY uk_category_code (category_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品分类表';

-- ---- supplier（供应商）----
CREATE TABLE supplier (
  `supplier_id` INT AUTO_INCREMENT COMMENT '供应商ID',
  `supplier_name` VARCHAR(100) NOT NULL COMMENT '供应商名称',
  `contact_person` VARCHAR(50) COMMENT '联系人',
  `phone` VARCHAR(20) COMMENT '联系电话',
  `address` VARCHAR(200) COMMENT '联系地址',
  `license_no` VARCHAR(50) COMMENT '药品经营许可证号',
  `credit_level` VARCHAR(10) NOT NULL DEFAULT 'B' COMMENT '信用等级：A/B/C',
  `cooperate_date` DATE COMMENT '合作起始日期',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1合作中 0已停用',
  PRIMARY KEY (`supplier_id`),
  UNIQUE KEY uk_supplier_name (supplier_name),
  UNIQUE KEY uk_supplier_license (license_no),
  KEY idx_supplier_credit (credit_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商表';

-- ---- warehouse（仓库）----
CREATE TABLE warehouse (
  `warehouse_id` INT AUTO_INCREMENT COMMENT '仓库ID',
  `warehouse_name` VARCHAR(50) NOT NULL COMMENT '仓库名称',
  `location` VARCHAR(100) COMMENT '仓库地址',
  `capacity` INT NOT NULL DEFAULT 0 COMMENT '库容(件)',
  `manager` VARCHAR(50) COMMENT '仓库负责人',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',
  PRIMARY KEY (`warehouse_id`),
  UNIQUE KEY uk_warehouse_name (warehouse_name),
  CONSTRAINT ck_warehouse_capacity CHECK (capacity >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库表';

-- ---- employee（员工）----
CREATE TABLE employee (
  `employee_id` INT AUTO_INCREMENT COMMENT '员工ID',
  `emp_no` VARCHAR(20) NOT NULL COMMENT '工号',
  `emp_name` VARCHAR(50) NOT NULL COMMENT '姓名',
  `gender` CHAR(1) NOT NULL COMMENT '性别：男/女',
  `position` VARCHAR(20) NOT NULL COMMENT '岗位：采购员/仓管员/销售员/质检员',
  `warehouse_id` INT NOT NULL COMMENT '所属仓库ID',
  `phone` VARCHAR(20) COMMENT '联系电话',
  `hire_date` DATE COMMENT '入职日期',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1在职 0离职',
  PRIMARY KEY (`employee_id`),
  UNIQUE KEY uk_emp_no (emp_no),
  KEY idx_emp_warehouse (warehouse_id),
  KEY idx_emp_position (position),
  CONSTRAINT fk_emp_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='员工表';

-- ---- drug（药品）----
CREATE TABLE drug (
  `drug_id` INT AUTO_INCREMENT COMMENT '药品ID',
  `drug_code` VARCHAR(20) NOT NULL COMMENT '药品编码',
  `drug_name` VARCHAR(100) NOT NULL COMMENT '药品名称',
  `category_id` INT NOT NULL COMMENT '分类ID',
  `spec` VARCHAR(50) NOT NULL COMMENT '规格',
  `unit` VARCHAR(10) NOT NULL DEFAULT '盒' COMMENT '单位',
  `manufacturer` VARCHAR(100) COMMENT '生产厂家',
  `approval_no` VARCHAR(50) COMMENT '批准文号',
  `purchase_price` DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '采购价(元)',
  `retail_price` DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '零售价(元)',
  `is_rx` TINYINT NOT NULL DEFAULT 1 COMMENT '是否处方药：1是 0否',
  `shelf_life_months` INT NOT NULL DEFAULT 24 COMMENT '保质期(月)',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1在售 0停用',
  PRIMARY KEY (`drug_id`),
  UNIQUE KEY uk_drug_code (drug_code),
  UNIQUE KEY uk_drug_name_spec (drug_name, spec),
  KEY idx_drug_category (category_id),
  KEY idx_drug_name (drug_name),
  CONSTRAINT fk_drug_category FOREIGN KEY (category_id) REFERENCES drug_category (category_id),
  CONSTRAINT ck_drug_price CHECK (purchase_price >= 0 AND retail_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品表';

-- ---- purchase_order（采购单）----
CREATE TABLE purchase_order (
  `po_id` INT AUTO_INCREMENT COMMENT '采购单ID',
  `po_no` VARCHAR(24) NOT NULL COMMENT '采购单号',
  `supplier_id` INT NOT NULL COMMENT '供应商ID',
  `warehouse_id` INT NOT NULL COMMENT '入库仓库ID',
  `employee_id` INT NOT NULL COMMENT '采购员ID',
  `order_date` DATE NOT NULL COMMENT '下单日期',
  `expect_date` DATE COMMENT '预计到货日期',
  `total_amount` DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '采购金额(元)',
  `status` VARCHAR(10) NOT NULL DEFAULT '待入库' COMMENT '状态：待入库/已入库/已取消',
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

-- ---- purchase_item（采购明细）----
CREATE TABLE purchase_item (
  `item_id` INT AUTO_INCREMENT COMMENT '明细ID',
  `po_id` INT NOT NULL COMMENT '采购单ID',
  `drug_id` INT NOT NULL COMMENT '药品ID',
  `quantity` INT NOT NULL COMMENT '采购数量',
  `unit_price` DECIMAL(8,2) NOT NULL COMMENT '采购单价(元)',
  `amount` DECIMAL(10,2) NOT NULL COMMENT '采购金额(元)',
  `batch_no` VARCHAR(30) NOT NULL COMMENT '批号',
  `production_date` DATE COMMENT '生产日期',
  `expiry_date` DATE NOT NULL COMMENT '有效期至',
  PRIMARY KEY (`item_id`),
  KEY idx_pi_po (po_id),
  KEY idx_pi_drug (drug_id),
  KEY idx_pi_batch (batch_no),
  CONSTRAINT fk_pi_po FOREIGN KEY (po_id) REFERENCES purchase_order (po_id),
  CONSTRAINT fk_pi_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT ck_pi_qty CHECK (quantity > 0),
  CONSTRAINT ck_pi_price CHECK (unit_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购明细表';

-- ---- stock（库存）----
CREATE TABLE stock (
  `stock_id` INT AUTO_INCREMENT COMMENT '库存ID',
  `drug_id` INT NOT NULL COMMENT '药品ID',
  `warehouse_id` INT NOT NULL COMMENT '仓库ID',
  `quantity` INT NOT NULL DEFAULT 0 COMMENT '库存数量',
  `safety_stock` INT NOT NULL DEFAULT 50 COMMENT '安全库存',
  `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`stock_id`),
  UNIQUE KEY uk_stock_drug_wh (drug_id, warehouse_id),
  KEY idx_stock_warehouse (warehouse_id),
  CONSTRAINT fk_stock_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT ck_stock_qty CHECK (quantity >= 0),
  CONSTRAINT ck_stock_safety CHECK (safety_stock >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存表';

-- ---- stock_batch（库存批次）----
CREATE TABLE stock_batch (
  `batch_id` INT AUTO_INCREMENT COMMENT '批次ID',
  `purchase_item_id` INT NOT NULL COMMENT '采购明细ID(1-1)',
  `drug_id` INT NOT NULL COMMENT '药品ID',
  `warehouse_id` INT NOT NULL COMMENT '仓库ID',
  `batch_no` VARCHAR(30) NOT NULL COMMENT '批号',
  `production_date` DATE COMMENT '生产日期',
  `expiry_date` DATE NOT NULL COMMENT '有效期至',
  `quantity` INT NOT NULL DEFAULT 0 COMMENT '批次结存数量',
  `purchase_price` DECIMAL(8,2) NOT NULL COMMENT '批次采购价(元)',
  `in_date` DATE NOT NULL COMMENT '入库日期',
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

-- ---- sale_order（销售单）----
CREATE TABLE sale_order (
  `so_id` INT AUTO_INCREMENT COMMENT '销售单ID',
  `so_no` VARCHAR(24) NOT NULL COMMENT '销售单号',
  `customer_name` VARCHAR(100) NOT NULL COMMENT '客户名称',
  `customer_type` VARCHAR(20) NOT NULL COMMENT '客户类型：连锁药店/医院/诊所/个人',
  `warehouse_id` INT NOT NULL COMMENT '出库仓库ID',
  `employee_id` INT NOT NULL COMMENT '销售员ID',
  `sale_date` DATE NOT NULL COMMENT '销售日期',
  `total_amount` DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '销售金额(元)',
  `status` VARCHAR(10) NOT NULL DEFAULT '已出库' COMMENT '状态：已出库/已退货',
  PRIMARY KEY (`so_id`),
  UNIQUE KEY uk_so_no (so_no),
  KEY idx_so_customer (customer_name),
  KEY idx_so_date (sale_date),
  KEY idx_so_warehouse (warehouse_id),
  CONSTRAINT fk_so_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_so_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),
  CONSTRAINT ck_so_amount CHECK (total_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售单表';

-- ---- sale_item（销售明细）----
CREATE TABLE sale_item (
  `item_id` INT AUTO_INCREMENT COMMENT '明细ID',
  `so_id` INT NOT NULL COMMENT '销售单ID',
  `drug_id` INT NOT NULL COMMENT '药品ID',
  `quantity` INT NOT NULL COMMENT '销售数量',
  `unit_price` DECIMAL(8,2) NOT NULL COMMENT '销售单价(元)',
  `amount` DECIMAL(10,2) NOT NULL COMMENT '销售金额(元)',
  PRIMARY KEY (`item_id`),
  KEY idx_si_so (so_id),
  KEY idx_si_drug (drug_id),
  CONSTRAINT fk_si_so FOREIGN KEY (so_id) REFERENCES sale_order (so_id),
  CONSTRAINT fk_si_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT ck_si_qty CHECK (quantity > 0),
  CONSTRAINT ck_si_amount CHECK (amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售明细表';

-- ---- stock_movement（库存流水）----
CREATE TABLE stock_movement (
  `move_id` BIGINT AUTO_INCREMENT COMMENT '流水ID',
  `drug_id` INT NOT NULL COMMENT '药品ID',
  `warehouse_id` INT NOT NULL COMMENT '仓库ID',
  `move_type` VARCHAR(20) NOT NULL COMMENT '变动类型：采购入库/销售出库/盘点调整/退货入库',
  `ref_no` VARCHAR(24) COMMENT '关联单号',
  `change_qty` INT NOT NULL COMMENT '变动数量(正入负出)',
  `before_qty` INT NOT NULL COMMENT '变动前库存',
  `after_qty` INT NOT NULL COMMENT '变动后库存',
  `batch_no` VARCHAR(30) COMMENT '批号',
  `employee_id` INT NOT NULL COMMENT '经办人ID',
  `move_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',
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

-- ---- access_log（访问性能日志）----
CREATE TABLE access_log (
  `log_id` BIGINT AUTO_INCREMENT COMMENT '日志ID',
  `scene` VARCHAR(20) NOT NULL COMMENT '场景：sale_out 销售出库',
  `client_no` INT NOT NULL COMMENT '客户端序号',
  `drug_id` INT COMMENT '药品ID(弱关联，不设外键)',
  `qty` INT NOT NULL COMMENT '本次出库数量',
  `ok` TINYINT NOT NULL COMMENT '结果：1成功 0库存不足',
  `elapsed_ms` DECIMAL(8,2) NOT NULL COMMENT '本次访问耗时(ms)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '访问时间',
  PRIMARY KEY (`log_id`),
  KEY idx_log_time (created_at),
  KEY idx_log_drug (drug_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='访问性能日志表';
