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
--
-- 【知识点：SQL 的四大类语句】
--   DDL（数据定义）：CREATE / ALTER / DROP —— 管"结构"（表、索引、约束）
--   DML（数据操作）：INSERT / UPDATE / DELETE / SELECT —— 管"数据"
--   DCL（数据控制）：GRANT / REVOKE —— 管"权限"
--   TCL（事务控制）：BEGIN / COMMIT / ROLLBACK —— 管"事务"
-- 本文件全是 DDL。
--
-- 【知识点：-- 是 SQL 的单行注释】MySQL 要求 -- 后面必须跟一个空格，
-- 否则会被当成"减减"运算符。多行注释用 /* ... */。
-- =====================================================================

-- 设置本次会话的字符集为 utf8mb4，保证脚本里的中文注释、中文数据不乱码
SET NAMES utf8mb4;

-- 如果已经存在同名数据库，先删掉
DROP DATABASE IF EXISTS drug_db;

-- 建库，并指定默认字符集与排序规则
CREATE DATABASE drug_db
  -- utf8mb4 才是完整的 UTF-8（3 字节的 utf8 存不了 emoji 和部分生僻字）
  DEFAULT CHARACTER SET utf8mb4
  -- 排序规则：general_ci 表示不区分大小写（ci = case insensitive），
  -- 所以 'ABC' 和 'abc' 在这里算相等
  DEFAULT COLLATE utf8mb4_general_ci;

-- 切换到刚建的库，后面的建表语句都落在这个库里
USE drug_db;

-- ---------------------------------------------------------------------
-- 1. 药品分类表
--    药品的上级分类，一个分类下可以有多种药品（1-N）。
-- ---------------------------------------------------------------------
-- 【知识点：建表语句的骨架】
--   CREATE TABLE 表名 (
--       列名 类型 [约束] [默认值] [注释],
--       ...
--       [表级约束：主键/唯一/外键/CHECK/索引]
--   ) COMMENT='表说明';  -- 表注释：显示在 Workbench 里，说明这张表的用途
CREATE TABLE drug_category (

  -- INT：整数，4 字节，范围约 ±21 亿，做主键够用
  -- AUTO_INCREMENT：自增。插入时不写这一列，数据库自动 +1 分配
  -- COMMENT：写在数据库里的中文说明，information_schema 能查出来（本项目动态表单就靠它）
  `category_id`   INT AUTO_INCREMENT COMMENT '分类ID',

  -- VARCHAR(50)：变长字符串，最多 50 个字符，实际占多少存多少（省空间）
  -- NOT NULL：不允许为空，插入时必须给值
  `category_name` VARCHAR(50)  NOT NULL COMMENT '分类名称',

  -- 分类编码，如 CAT01
  `category_code` VARCHAR(20)  NOT NULL COMMENT '分类编码',

  -- 没有 NOT NULL 就表示"可以为空"，不填就是 NULL（注意 NULL ≠ 空字符串）
  `description`   VARCHAR(200) COMMENT '分类说明',

  -- TINYINT：1 字节整数，范围 -128~127，用来存"状态"最省空间
  -- DEFAULT 1：插入时不给值就默认 1
  `status`        TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',

  -- 【知识点：PRIMARY KEY 主键】唯一标识一行，不能重复、不能为 NULL，
  -- 且 InnoDB 会自动为主键建"聚簇索引"——数据按主键顺序物理存放，按主键查最快
  PRIMARY KEY (`category_id`),

  -- 【知识点：UNIQUE KEY 唯一约束】这一列的值全表不能重复。
  -- 和主键的区别：一张表只能有一个主键，但可以有多个唯一约束；唯一约束允许 NULL
  UNIQUE KEY uk_category_name (category_name),
  UNIQUE KEY uk_category_code (category_code)
) COMMENT='药品分类表';

-- ---------------------------------------------------------------------
-- 2. 供应商表
--    采购业务的往来单位。名称与许可证号唯一，防止同一家供应商重复建档。
-- ---------------------------------------------------------------------
CREATE TABLE supplier (

  -- 自增主键
  `supplier_id`    INT AUTO_INCREMENT COMMENT '供应商ID',

  -- 公司名称，必填且唯一
  `supplier_name`  VARCHAR(100) NOT NULL COMMENT '供应商名称',

  -- 联系人、电话、地址：允许为空（有的供应商只留个电话）
  `contact_person` VARCHAR(50) COMMENT '联系人',
  `phone`          VARCHAR(20) COMMENT '联系电话',
  `address`        VARCHAR(200) COMMENT '联系地址',

  -- 药品经营许可证号，唯一：一家公司只该有一条档案
  `license_no`     VARCHAR(50) COMMENT '药品经营许可证号',

  -- 信用等级：A/B/C 三档，默认 B
  `credit_level`   VARCHAR(10) NOT NULL DEFAULT 'B' COMMENT '信用等级：A/B/C',

  -- DATE：只存年月日（2024-03-01），不带时分秒，比 DATETIME 省 2 字节
  `cooperate_date` DATE COMMENT '合作起始日期',

  -- 是否还在合作
  `status`         TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1合作中 0已停用',

  -- 主键
  PRIMARY KEY (`supplier_id`),

  -- 名称唯一、许可证唯一
  UNIQUE KEY uk_supplier_name (supplier_name),
  UNIQUE KEY uk_supplier_license (license_no),

  -- 【知识点：KEY / INDEX 普通索引】没有唯一限制，纯粹为了查得快。
  -- 这里给信用等级建索引，是为了"按等级筛选供应商"这类查询不用全表扫描
  KEY idx_supplier_credit (credit_level)

) COMMENT='供应商表';

-- ---------------------------------------------------------------------
-- 3. 仓库表
--    药品按仓库分开存放，库存、批次、采购入库、销售出库都落到具体仓库。
-- ---------------------------------------------------------------------
CREATE TABLE warehouse (

  `warehouse_id`   INT AUTO_INCREMENT COMMENT '仓库ID',

  -- 仓库名唯一，防止建两个"一号仓"
  `warehouse_name` VARCHAR(50) NOT NULL COMMENT '仓库名称',

  `location`       VARCHAR(100) COMMENT '仓库地址',

  -- 库容，默认 0
  `capacity`       INT NOT NULL DEFAULT 0 COMMENT '库容(件)',

  `manager`        VARCHAR(50) COMMENT '仓库负责人',

  `status`         TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用 0停用',

  PRIMARY KEY (`warehouse_id`),
  UNIQUE KEY uk_warehouse_name (warehouse_name),

  -- 【知识点：CHECK 约束】数据库层的"业务规则校验"。
  -- MySQL 8.0.16 之后才真正强制执行（之前的版本只解析不生效）。
  -- 有了它，就算程序有 bug，也不可能往库里写负数库容
  CONSTRAINT ck_warehouse_capacity CHECK (capacity >= 0)

) COMMENT='仓库表';

-- ---------------------------------------------------------------------
-- 4. 员工表（warehouse_id → warehouse）
--    采购员、仓管员、销售员、质检员；每人在一个仓库任职。
-- ---------------------------------------------------------------------
CREATE TABLE employee (

  `employee_id`  INT AUTO_INCREMENT COMMENT '员工ID',

  -- 工号，唯一
  `emp_no`       VARCHAR(20) NOT NULL COMMENT '工号',

  `emp_name`     VARCHAR(50) NOT NULL COMMENT '姓名',

  -- 【知识点：CHAR 与 VARCHAR 的区别】
  -- CHAR(1) 是"定长"：不管存"男"还是空，都占 1 个字符，存取快；
  -- VARCHAR(50) 是"变长"：按实际长度占空间。性别固定 1 个字，用 CHAR 更合适
  `gender`       CHAR(1)     NOT NULL COMMENT '性别：男/女',

  -- 岗位决定这个人能干什么（下拉框按岗位过滤：采购员开采购单，销售员开出库单）
  `position`     VARCHAR(20) NOT NULL COMMENT '岗位：采购员/仓管员/销售员/质检员',

  -- 所属仓库，外键连到 warehouse
  `warehouse_id` INT         NOT NULL COMMENT '所属仓库ID',

  `phone`        VARCHAR(20) COMMENT '联系电话',

  `hire_date`    DATE COMMENT '入职日期',

  `status`       TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1在职 0离职',

  PRIMARY KEY (`employee_id`),
  UNIQUE KEY uk_emp_no (emp_no),

  -- 两个普通索引：按仓库查人、按岗位查人都会用到
  KEY idx_emp_warehouse (warehouse_id),
  KEY idx_emp_position (position),

  -- 【知识点：FOREIGN KEY 外键约束】
  -- CONSTRAINT 名字 FOREIGN KEY (本表列) REFERENCES 父表 (父表主键)
  -- 作用有两个：
  --   ① 插入时，warehouse_id 必须是 warehouse 表里真实存在的值（不然就是"幽灵员工"）
  --   ② 删除父表记录时，如果还有子表引用它，数据库会拒绝（防止"删了仓库留下孤儿员工"）
  -- 代价：每次写入都要检查父表，稍微慢一点；所以像 access_log 这种日志表就故意不设外键
  CONSTRAINT fk_emp_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id)

) COMMENT='员工表';

-- ---------------------------------------------------------------------
-- 5. 药品表（category_id → drug_category）
--    drug_code 唯一；同一「名称 + 规格」不得重复建档（uk_drug_name_spec）。
--    采购价 / 零售价非负由 CHECK 约束兜底。
-- ---------------------------------------------------------------------
CREATE TABLE drug (

  -- 药品主键
  `drug_id`           INT AUTO_INCREMENT COMMENT '药品ID',

  -- 药品编码，如 YP000013，唯一
  `drug_code`         VARCHAR(20)  NOT NULL COMMENT '药品编码',

  -- 药名，如"阿莫西林胶囊"
  `drug_name`         VARCHAR(100) NOT NULL COMMENT '药品名称',

  -- 分类外键
  `category_id`       INT          NOT NULL COMMENT '分类ID',

  -- 规格，如 "0.25g*24粒"
  `spec`              VARCHAR(50)  NOT NULL COMMENT '规格',

  -- 单位，默认"盒"
  `unit`              VARCHAR(10)  NOT NULL DEFAULT '盒' COMMENT '单位',

  `manufacturer`      VARCHAR(100) COMMENT '生产厂家',

  `approval_no`       VARCHAR(50)  COMMENT '批准文号',

  -- 【知识点：DECIMAL(8,2) 定点小数】
  -- 8 = 总共 8 位数字，2 = 小数占 2 位，所以整数部分最多 6 位（最大 999999.99）。
  -- 金额为什么不用 FLOAT/DOUBLE？因为浮点是"二进制近似值"，
  -- 0.1 + 0.2 可能算出 0.30000000000000004，钱会算错。
  -- DECIMAL 是"十进制精确存储"，做金额必须用它
  `purchase_price`    DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '采购价(元)',
  `retail_price`      DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '零售价(元)',

  -- 是否处方药：1 是，0 否
  `is_rx`             TINYINT      NOT NULL DEFAULT 1 COMMENT '是否处方药：1是 0否',

  -- 保质期几个月，默认 24
  `shelf_life_months` INT          NOT NULL DEFAULT 24 COMMENT '保质期(月)',

  `status`            TINYINT      NOT NULL DEFAULT 1 COMMENT '状态：1在售 0停用',

  PRIMARY KEY (`drug_id`),

  -- 编码唯一
  UNIQUE KEY uk_drug_code (drug_code),

  -- 括号里两列组合起来不能重复：同一「名称 + 规格」不得重复建档
  UNIQUE KEY uk_drug_name_spec (drug_name, spec),

  -- 分类索引（按分类筛药品）、名称索引（按药名模糊查）——名称索引能让 LIKE '阿莫%' 走范围扫描
  KEY idx_drug_category (category_id),
  KEY idx_drug_name (drug_name),

  -- 分类外键
  CONSTRAINT fk_drug_category FOREIGN KEY (category_id) REFERENCES drug_category (category_id),

  -- 价格不能为负
  CONSTRAINT ck_drug_price CHECK (purchase_price >= 0 AND retail_price >= 0)

) COMMENT='药品表';

-- ---------------------------------------------------------------------
-- 6. 采购单（supplier_id / warehouse_id / employee_id 三个外键）
--    total_amount 为明细金额汇总，由入库事务统一维护。
-- ---------------------------------------------------------------------
CREATE TABLE purchase_order (

  `po_id`          INT AUTO_INCREMENT COMMENT '采购单ID',

  -- 单号，如 PO2026110915203317，唯一
  `po_no`          VARCHAR(24)   NOT NULL COMMENT '采购单号',

  -- 三个外键：供应商、入库仓库、采购员
  `supplier_id`    INT           NOT NULL COMMENT '供应商ID',
  `warehouse_id`   INT           NOT NULL COMMENT '入库仓库ID',
  `employee_id`    INT           NOT NULL COMMENT '采购员ID',

  `order_date`     DATE          NOT NULL COMMENT '下单日期',

  `expect_date`    DATE COMMENT '预计到货日期',

  -- 单据总金额：这是"受控冗余"——本可以从明细 SUM 出来，
  -- 但每次都算太慢，所以冗余存一份，由同一个事务保证它和明细始终一致
  `total_amount`   DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '采购金额(元)',

  -- 单据状态用字符串（而不是数字），是历史设计：待入库/已入库/已取消
  `status`         VARCHAR(10)   NOT NULL DEFAULT '待入库' COMMENT '状态：待入库/已入库/已取消',

  PRIMARY KEY (`po_id`),
  UNIQUE KEY uk_po_no (po_no),

  -- 三个索引分别对应：按供应商查单、按日期排序、按状态筛选
  KEY idx_po_supplier (supplier_id),
  KEY idx_po_date (order_date),
  KEY idx_po_status (status),

  -- 三个外键
  CONSTRAINT fk_po_supplier FOREIGN KEY (supplier_id) REFERENCES supplier (supplier_id),
  CONSTRAINT fk_po_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_po_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),

  -- 金额非负
  CONSTRAINT ck_po_amount CHECK (total_amount >= 0)

) COMMENT='采购单表';

-- ---------------------------------------------------------------------
-- 7. 采购明细（po_id → purchase_order，drug_id → drug）
--    拆解「采购单 N-M 药品」；一行明细对应一个入库批次（批号/生产日期/有效期）。
-- ---------------------------------------------------------------------
-- 【知识点：N-M 关系怎么落地】
-- 一张采购单可以买多种药，一种药也可以出现在多张采购单上 —— 多对多。
-- 关系数据库不能直接表达"多对多"，必须拆一张"中间表"（也叫连接表）：
--   采购单 1--N 采购明细 N--1 药品
-- 采购明细就是那张中间表，销售明细同理
CREATE TABLE purchase_item (

  -- 明细主键
  `item_id`         INT AUTO_INCREMENT COMMENT '明细ID',

  -- 属于哪张采购单
  `po_id`           INT           NOT NULL COMMENT '采购单ID',

  -- 哪个药品
  `drug_id`         INT           NOT NULL COMMENT '药品ID',

  -- 数量、单价、金额
  -- 注意：unit_price 是"成交价快照"，不是药品表里的当前价 —— 药品调价了，历史单据不能跟着变
  `quantity`        INT           NOT NULL COMMENT '采购数量',
  `unit_price`      DECIMAL(8,2)  NOT NULL COMMENT '采购单价(元)',
  `amount`          DECIMAL(10,2) NOT NULL COMMENT '采购金额(元)',

  -- 批号/生产日期/有效期：同一次采购入库就是一个批次
  `batch_no`        VARCHAR(30)   NOT NULL COMMENT '批号',
  `production_date` DATE COMMENT '生产日期',
  `expiry_date`     DATE          NOT NULL COMMENT '有效期至',

  PRIMARY KEY (`item_id`),

  -- 按单查明细、按药查采购历史、按批号溯源，各有一个索引
  KEY idx_pi_po (po_id),
  KEY idx_pi_drug (drug_id),
  KEY idx_pi_batch (batch_no),

  -- 两个外键
  CONSTRAINT fk_pi_po FOREIGN KEY (po_id) REFERENCES purchase_order (po_id),
  CONSTRAINT fk_pi_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),

  -- 数量必须为正（采购 0 件没有意义）
  CONSTRAINT ck_pi_qty CHECK (quantity > 0),
  CONSTRAINT ck_pi_price CHECK (unit_price >= 0)

) COMMENT='采购明细表';

-- ---------------------------------------------------------------------
-- 8. 库存表（drug_id + warehouse_id 唯一）
--    一个药品在一个仓库只有一行结存；销售出库时在此行上加行锁。
-- ---------------------------------------------------------------------
-- 【知识点：为什么库存要单独一张表】
-- 如果把"库存数量"放进药品表，就只能记"总库存"，
-- 但实际业务是"同一个药在 6 个仓库各有结存"——这是多值属性，放在药品表会违反 1NF。
-- 所以拆成 stock（药品 × 仓库），一行 = 一个药在一个仓库的结存
CREATE TABLE stock (

  `stock_id`      INT AUTO_INCREMENT COMMENT '库存ID',

  -- 药品 + 仓库，这两个组合唯一
  `drug_id`       INT NOT NULL COMMENT '药品ID',
  `warehouse_id`  INT NOT NULL COMMENT '仓库ID',

  -- 当前结存数量
  `quantity`      INT NOT NULL DEFAULT 0 COMMENT '库存数量',

  -- 安全库存：低于它应该提示补货
  `safety_stock`  INT NOT NULL DEFAULT 50 COMMENT '安全库存',

  -- 最后更新时间：DEFAULT CURRENT_TIMESTAMP 表示"插入时自动填当前时间"
  `update_time`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后更新时间',

  PRIMARY KEY (`stock_id`),

  -- 【知识点：这个复合唯一约束是本项目的关键】
  -- 保证"一个药品在一个仓库只有一行库存"。并发时，两个事务想给同一个药同一个仓库
  -- 插两行库存，第二个会被数据库直接拒绝 —— 这就是"幂等"的底座。
  -- 另外，它本身就是 (drug_id, warehouse_id) 的索引，
  -- 所以销售出库那句 WHERE drug_id=? AND warehouse_id=? FOR UPDATE 能快速定位到行
  UNIQUE KEY uk_stock_drug_wh (drug_id, warehouse_id),

  KEY idx_stock_warehouse (warehouse_id),

  CONSTRAINT fk_stock_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),

  -- 【知识点：CHECK 是防超卖的最后一道防线】
  -- 程序里的行锁负责"正常情况下不超卖"；万一程序有 bug 或有人手工改库，
  -- 这条约束会让 quantity < 0 的更新直接失败，事务回滚
  CONSTRAINT ck_stock_qty CHECK (quantity >= 0),
  CONSTRAINT ck_stock_safety CHECK (safety_stock >= 0)

) COMMENT='库存表';

-- ---------------------------------------------------------------------
-- 9. 库存批次表
--    purchase_item_id 唯一 → 与采购明细构成 1-1 联系：一次采购入库生成一个批次。
--    批次保留生产日期与有效期，用于近效期预警。
-- ---------------------------------------------------------------------
-- 【知识点：1-1 联系怎么保证】
-- 理论上的 1-1，落到库里就是"在子表的外键上加 UNIQUE"：
-- 加了 UNIQUE，一条采购明细就只能被一个批次引用，绝不会一分为二。
-- 这就是老师要求的 1-1 关系在本项目里的物理保证（uk_batch_purchase）
CREATE TABLE stock_batch (

  `batch_id`         INT AUTO_INCREMENT COMMENT '批次ID',

  -- 来源采购明细，唯一 → 1-1
  `purchase_item_id` INT           NOT NULL COMMENT '采购明细ID(1-1)',

  `drug_id`          INT           NOT NULL COMMENT '药品ID',
  `warehouse_id`     INT           NOT NULL COMMENT '仓库ID',

  -- 批号：同一药同一仓同一批号不能重复
  `batch_no`         VARCHAR(30)   NOT NULL COMMENT '批号',

  `production_date`  DATE COMMENT '生产日期',

  -- 有效期：近效期先出（FEFO）就靠这一列排序
  `expiry_date`      DATE          NOT NULL COMMENT '有效期至',

  -- 这个批次还剩多少
  `quantity`         INT           NOT NULL DEFAULT 0 COMMENT '批次结存数量',

  -- 批次成本价（采购价快照，用于算毛利）
  `purchase_price`   DECIMAL(8,2)  NOT NULL COMMENT '批次采购价(元)',

  `in_date`          DATE          NOT NULL COMMENT '入库日期',

  PRIMARY KEY (`batch_id`),

  -- 1-1 的保证
  UNIQUE KEY uk_batch_purchase (purchase_item_id),

  -- 同药同仓同批号唯一，防止同一批货被重复入库
  UNIQUE KEY uk_batch_no (drug_id, warehouse_id, batch_no),

  -- 效期索引：查"180 天内到期的批次"要用它（不然就是全表扫描）
  KEY idx_batch_expiry (expiry_date),
  KEY idx_batch_drug (drug_id),

  CONSTRAINT fk_batch_item FOREIGN KEY (purchase_item_id) REFERENCES purchase_item (item_id),
  CONSTRAINT fk_batch_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_batch_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),

  -- 批次结存不能为负（扣批次时超扣会被拒绝）
  CONSTRAINT ck_batch_qty CHECK (quantity >= 0)

) COMMENT='库存批次表';

-- ---------------------------------------------------------------------
-- 10. 销售单（warehouse_id / employee_id）
--     客户为连锁药店、医院、诊所或个人；出库减少对应仓库库存。
-- ---------------------------------------------------------------------
CREATE TABLE sale_order (

  `so_id`         INT AUTO_INCREMENT COMMENT '销售单ID',

  -- 销售单号，唯一
  `so_no`         VARCHAR(24)   NOT NULL COMMENT '销售单号',

  -- 客户名称与类型（客户不是本系统的建档实体，只记名字，属于"弱实体"）
  `customer_name` VARCHAR(100)  NOT NULL COMMENT '客户名称',
  `customer_type` VARCHAR(20)   NOT NULL COMMENT '客户类型：连锁药店/医院/诊所/个人',

  -- 出库仓库、销售员
  `warehouse_id`  INT           NOT NULL COMMENT '出库仓库ID',
  `employee_id`   INT           NOT NULL COMMENT '销售员ID',

  `sale_date`     DATE          NOT NULL COMMENT '销售日期',

  -- 单据总额（受控冗余，由出库事务维护）
  `total_amount`  DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '销售金额(元)',

  `status`        VARCHAR(10)   NOT NULL DEFAULT '已出库' COMMENT '状态：已出库/已退货',

  PRIMARY KEY (`so_id`),
  UNIQUE KEY uk_so_no (so_no),

  -- 三个索引：按客户查、按日期排、按仓库统计
  KEY idx_so_customer (customer_name),
  KEY idx_so_date (sale_date),
  KEY idx_so_warehouse (warehouse_id),

  CONSTRAINT fk_so_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_so_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),

  CONSTRAINT ck_so_amount CHECK (total_amount >= 0)

) COMMENT='销售单表';

-- ---------------------------------------------------------------------
-- 11. 销售明细（so_id → sale_order，drug_id → drug）
--     拆解「销售单 N-M 药品」。
-- ---------------------------------------------------------------------
CREATE TABLE sale_item (

  `item_id`    INT AUTO_INCREMENT COMMENT '明细ID',

  -- 属于哪张销售单
  `so_id`      INT           NOT NULL COMMENT '销售单ID',

  -- 卖的是哪个药
  `drug_id`    INT           NOT NULL COMMENT '药品ID',

  -- 数量、成交单价、金额
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

) COMMENT='销售明细表';

-- ---------------------------------------------------------------------
-- 12. 库存流水表
--     每次库存变动记一条：正数为入库、负数为出库；
--     CHECK (after_qty = before_qty + change_qty) 保证账面连续可追溯。
--     这是行数最多的表（5.7 万条），也是索引对比演示的主表。
-- ---------------------------------------------------------------------
CREATE TABLE stock_movement (

  -- 【知识点：BIGINT】8 字节整数。流水表增长最快，用 BIGINT 防止 ID 用光
  `move_id`      BIGINT AUTO_INCREMENT COMMENT '流水ID',

  `drug_id`      INT          NOT NULL COMMENT '药品ID',
  `warehouse_id` INT          NOT NULL COMMENT '仓库ID',

  -- 变动类型：采购入库/销售出库/盘点调整/退货入库
  `move_type`    VARCHAR(20)  NOT NULL COMMENT '变动类型：采购入库/销售出库/盘点调整/退货入库',

  -- 关联的业务单号（采购单号或销售单号），用来溯源"这笔库存变动是哪张单造成的"
  `ref_no`       VARCHAR(24)  COMMENT '关联单号',

  -- 正数入库、负数出库
  `change_qty`   INT          NOT NULL COMMENT '变动数量(正入负出)',

  -- 变动前后的库存快照
  `before_qty`   INT          NOT NULL COMMENT '变动前库存',
  `after_qty`    INT          NOT NULL COMMENT '变动后库存',

  `batch_no`     VARCHAR(30)  COMMENT '批号',

  -- 经办人
  `employee_id`  INT          NOT NULL COMMENT '经办人ID',

  -- 发生时间，插入时自动填
  `move_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发生时间',

  PRIMARY KEY (`move_id`),

  -- 【知识点：复合索引的"最左前缀"原则】
  -- idx_move_drug_time (drug_id, move_time) 可以用来查：
  --   ① WHERE drug_id=?                      ✓
  --   ② WHERE drug_id=? ORDER BY move_time   ✓
  --   ③ WHERE move_time=?                    ✗ （跳过了最左列 drug_id，用不上）
  -- 所以还需要单独给 move_time 建一个索引（下一行）
  KEY idx_move_drug_time (drug_id, move_time),

  -- 按变动类型 + 时间统计（报表用）
  KEY idx_move_type_time (move_type, move_time),

  -- 单纯按时间查（第 ③ 种情况）
  KEY idx_move_time (move_time),

  CONSTRAINT fk_move_drug FOREIGN KEY (drug_id) REFERENCES drug (drug_id),
  CONSTRAINT fk_move_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouse (warehouse_id),
  CONSTRAINT fk_move_employee FOREIGN KEY (employee_id) REFERENCES employee (employee_id),

  -- 库存不能为负
  CONSTRAINT ck_move_after CHECK (after_qty >= 0),

  -- 【知识点：跨列 CHECK】流水必须"自洽"：
  -- 变动后 = 变动前 + 变动量。写错了数据库直接拒绝，
  -- 这样 5.7 万条流水不可能出现"账对不上"的情况
  CONSTRAINT ck_move_balance CHECK (after_qty = before_qty + change_qty)

) COMMENT='库存流水表';

-- ---------------------------------------------------------------------
-- 13. 访问性能日志表
--     多客户端并发访问时逐次记录响应耗时。刻意不设外键：
--     日志只做弱关联，避免影响药品主数据的维护；drug_id 建普通索引。
-- ---------------------------------------------------------------------
-- 【知识点：为什么日志表不建外键】
--   ① 外键会在每次插入时检查父表，并发压测时白白多一次开销
--   ② 药品被删了，历史日志也不该消失（日志是审计证据）
--   ③ 高并发写日志的表，最怕被父表的锁牵连
CREATE TABLE access_log (

  `log_id`     BIGINT AUTO_INCREMENT COMMENT '日志ID',

  -- 场景：目前只有 sale_out（销售出库并发演示）
  `scene`      VARCHAR(20)  NOT NULL COMMENT '场景：sale_out 销售出库',

  -- 第几号终端（1~30）
  `client_no`  INT          NOT NULL COMMENT '客户端序号',

  -- 药品 id，可空、无外键（弱关联）
  `drug_id`    INT COMMENT '药品ID(弱关联，不设外键)',

  -- 本次出库数量
  `qty`        INT          NOT NULL COMMENT '本次出库数量',

  -- 成功还是失败（库存不足）
  `ok`         TINYINT      NOT NULL COMMENT '结果：1成功 0库存不足',

  -- 【知识点：这个字段就是"访问性能记录"的载体】
  -- 每次请求的真实耗时（毫秒），由程序用 perf_counter 掐表算出来写入，
  -- 不是编的。页面上的 P95、平均耗时都是从这张表/这批数据算出来的
  `elapsed_ms` DECIMAL(8,2) NOT NULL COMMENT '本次访问耗时(ms)',

  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '访问时间',

  PRIMARY KEY (`log_id`),

  -- 按时间查最近日志、按药品查该药的访问情况
  KEY idx_log_time (created_at),
  KEY idx_log_drug (drug_id)

) COMMENT='访问性能日志表';

-- =====================================================================
-- 说明：界面上的「业务表单」（如召回登记、盘点差异表）由客户端在运行时
--       动态 CREATE TABLE bizform_xxx，属于课程要求的"创建/删除数据表"演示，
--       不写入本脚本，避免污染核心 13 张业务表的结构。
-- =====================================================================
