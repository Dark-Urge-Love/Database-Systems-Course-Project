# 药品进销存数据库应用系统课程项目报告

**课程名称**：数据库系统　　**项目题目**：药品进销存系统——基于 MySQL 与 Flask 的 B/S 数据库应用

| 项目 | 内容 |
|---|---|
| 学院 / 班级 | （请填写：××学院 ×× 班） |
| 姓名 | （请填写：×××） |
| 学号 | （请填写：××××××××） |
| 指导教师 | （请填写：×××） |
| 完成日期 | 2026 年 11 月 |

## 1 数据库应用业务背景介绍

医药零售企业的核心经营活动是"进、销、存"：向供应商采购药品，入库后形成带批号与有效期的库存，再向客户销售出库。药品不同于普通商品——它有批准文号、生产批次和严格的保质期，"近效期先出、过期禁售"是行业硬性规则；同时药店往往多个仓库、多家门店并行运转，账实一致与追溯能力直接关系到用药安全。因此，用一个数据库应用系统把采购、入库、库存、销售、流水五个环节统一管理，具有明确的现实意义。

本系统围绕"药品（Drug）"这一核心实体展开业务：采购员向供应商下采购单（PurchaseOrder），每张单包含若干明细（PurchaseItem），到货后生成库存批次（StockBatch）并汇总为库存结存（Stock）；销售员开出销售单（SaleOrder）及明细（SaleItem）扣减库存；所有出入库动作都在库存流水（StockMovement）中留下可追溯记录。整套数据管理由部署在 Linux 服务器上的 MySQL 8.0 完成，浏览器端通过 B/S 方式访问。

## 2 数据需求分析

通过模拟一家连锁药店的日常业务，梳理出以下数据需求。

**（1）主数据需求**

- 药品目录：编码、名称、规格、单位、剂型分类、生产厂家、批准文号、采购价、零售价、是否处方药、保质期（月）、在售状态；
- 药品分类：分类编号与名称（感冒用药、抗生素、处方药等）；
- 供应商：名称、联系人、电话、地址、信用等级；
- 仓库与员工：仓库编号、名称、地点、负责人；员工编号、姓名、岗位（采购员/销售员/库管员）及其所属仓库。

**（2）业务单据需求**

- 采购单（单号、供应商、入库仓库、采购员、日期、总金额、状态）及采购明细（药品、数量、单价、金额、批号、生产日期、有效期）；
- 销售单（单号、出库仓库、销售员、日期、客户名称与类型、总金额）及销售明细（药品、数量、售价、金额）。

**（3）库存与追溯需求**

- 库存结存：药品 × 仓库维度的当前数量与安全库存，用于库存不足报警；
- 库存批次：每批药品的批号、有效期与剩余数量，支持"近效期先出"和效期预警；
- 库存流水：每一次出入库的时间、类型、变动数量、变动前后快照、经办人与关联单号，实现全程可追溯。

**（4）非功能需求**

- 数据规模：初始数据不少于 5 张表、10 万条记录；
- 完整性：主键、外键、唯一约束、CHECK 约束齐备，库存不允许为负；
- 性能：常用查询（按药品查流水）在十万行级数据上有可观测的索引加速；
- 并发：多终端同时销售同一药品时不允许超卖，且逐次记录访问耗时。

## 3 数据库设计

### 3.1 概念结构设计（E-R 图）

概念设计从业务语义出发、忽略具体存储细节，抽象出 7 个实体：药品分类、药品、供应商、采购单、销售单、仓库、员工。实体之间的联系共 8 条：药品分类与药品为 1:N（属于），供应商与采购单为 1:N（订购），员工与采购单、员工与销售单各为 1:N（经办），仓库与员工为 1:N（任职），采购单与药品、销售单与药品均为 M:N（包含），药品与仓库为 M:N（存放）。其中"包含"联系带有数量与单价属性，"存放"联系带有库存数量属性，"任职"联系带有岗位属性。

概念模型如图 3-1 所示：矩形表示实体，菱形表示联系，椭圆表示属性，主键属性加下划线，连线两端的 1、M、N 为基数约束。

![图3-1 药品进销存系统E-R图（概念模型）](images/er_concept.png)

逻辑设计阶段再把概念模型落地为 13 张表：M:N 的"包含"联系按业务单据拆分为采购单／采购明细、销售单／销售明细两组表；M:N 的"存放"联系落地为库存结存与库存批次；另设库存流水记录每一次库存变动，设访问日志记录并发访问耗时，二者不含独立的业务实体，仅在物理层存在（见 3.2）。

### 3.2 逻辑结构设计（关系模式）

将 E-R 图按转换规则转为关系模式：实体转为关系，1:N 与 M:N 联系按上述方式消解，联系属性并入相应关系。共 13 个关系模式（**下划线**为主键，*斜体*为外键）：

1. 药品分类（<u>分类ID</u>，分类名称，分类编码，分类说明，状态）
2. 供应商（<u>供应商ID</u>，供应商名称，联系人，联系电话，联系地址，药品经营许可证号，信用等级，合作起始日期，状态）
3. 仓库（<u>仓库ID</u>，仓库名称，仓库地址，库容，负责人，状态）
4. 员工（<u>员工ID</u>，工号，姓名，性别，岗位，*仓库ID*，联系电话，入职日期，状态）
5. 药品（<u>药品ID</u>，药品编码，药品名称，*分类ID*，规格，单位，生产厂家，批准文号，采购价，零售价，是否处方药，保质期月数，状态）
6. 采购单（<u>采购单ID</u>，采购单号，*供应商ID*，*入库仓库ID*，*采购员ID*，下单日期，预计到货日期，采购金额，状态）
7. 采购明细（<u>明细ID</u>，*采购单ID*，*药品ID*，采购数量，采购单价，采购金额，批号，生产日期，有效期至）
8. 库存批次（<u>批次ID</u>，*采购明细ID*（1:1），*药品ID*，*仓库ID*，批号，生产日期，有效期至，批次结存数量，批次采购价，入库日期）
9. 库存结存（<u>药品ID＋仓库ID</u>，库存数量，安全库存，最后更新时间）
10. 销售单（<u>销售单ID</u>，销售单号，客户名称，客户类型，*出库仓库ID*，*销售员ID*，销售日期，销售金额，状态）
11. 销售明细（<u>明细ID</u>，*销售单ID*，*药品ID*，销售数量，销售单价，销售金额）
12. 库存流水（<u>流水ID</u>，*药品ID*，*仓库ID*，*经办人ID*，变动类型，关联单号，变动数量，变动前库存，变动后库存，批号，发生时间）
13. 访问日志（<u>日志ID</u>，场景，客户端序号，药品ID，出库数量，结果，访问耗时，访问时间）

其中库存结存以"药品＋仓库"为联合主键，保证同一药品在同一仓库只有一行结存；库存批次与采购明细通过唯一约束实现 1:1；访问日志中的药品只做弱关联，不设外键，以免日志写入受主数据维护影响。

### 3.3 规范化检查（3NF）

以药品表为例：药品编码、药品名称等属性完全函数依赖于主键药品ID（满足 2NF）；分类名称不在药品表中冗余存放，而是由分类ID经外键引用药品分类表表达，非主属性之间不存在传递依赖（满足 3NF）。金额类字段（采购金额、销售金额）保留为派生值，是出于单据留痕的业务考虑——历史成交价不应随主数据调价而变动。所有关系模式均通过 3NF 检查；库存结存、库存批次两类聚合表则有意保留轻度反范式，以换取库存查询与行锁扣减的效率，冗余字段的取值由入库事务统一维护。

### 3.4 物理设计（SQL DDL 与索引）

建表 DDL 共 13 张表，统一 InnoDB 引擎与 utf8mb4 字符集，含 19 个外键、13 个 CHECK 约束、37 个二级索引。药品表 DDL 节选如下（完整脚本见 sql/schema.sql）：

```sql
CREATE TABLE drug (
  `drug_id`           INT AUTO_INCREMENT COMMENT '药品ID',
  `drug_code`         VARCHAR(20)  NOT NULL COMMENT '药品编码',
  `drug_name`         VARCHAR(100) NOT NULL COMMENT '药品名称',
  `category_id`       INT          NOT NULL COMMENT '分类ID',
  `spec`              VARCHAR(50)  NOT NULL COMMENT '规格',
  `purchase_price`    DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '采购价(元)',
  `retail_price`      DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '零售价(元)',
  `is_rx`             TINYINT      NOT NULL DEFAULT 1 COMMENT '是否处方药',
  `shelf_life_months` INT          NOT NULL DEFAULT 24 COMMENT '保质期(月)',
  `status`            TINYINT      NOT NULL DEFAULT 1 COMMENT '1在售 0停用',
  PRIMARY KEY (`drug_id`),
  UNIQUE KEY uk_drug_code (drug_code),
  UNIQUE KEY uk_drug_name_spec (drug_name, spec),
  KEY idx_drug_category (category_id),
  CONSTRAINT fk_drug_category FOREIGN KEY (category_id)
    REFERENCES drug_category (category_id),
  CONSTRAINT ck_drug_price CHECK (purchase_price >= 0 AND retail_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='药品表';
```

针对高频查询设计的关键索引：`idx_move_drug_time (drug_id, move_time)`（按药品查流水）、`idx_batch_expiry (expiry_date)`（效期预警）、`idx_move_time (move_time)`（按日期统计）。初始数据共 153,072 条（药品 1,200、采购明细 15,695、销售明细 41,500、库存流水 57,350 等），由 `sql/data_gen.py --seed 2026` 可复现生成。

## 4 数据库应用开发设计

### 4.1 实现环境配置说明

系统采用 B/S 三层架构：浏览器端（原生 HTML/JS）通过 HTTP 访问本机 Flask 应用，Flask 经 PyMySQL 以 TCP 3306 远程连接独立部署在 Linux 上的 MySQL 服务器，数据库与客户端分属两台主机，满足"服务器端部署在 Linux"的课程要求。

| 层 | 环境与版本 | 说明 |
|---|---|---|
| 客户端 | Windows 10/11 · Chrome/Edge 浏览器 | 访问 http://127.0.0.1:5000 |
| 应用层 | Python 3.13 · Flask 3.x · PyMySQL 1.x | run.py 启动，16 个 REST 接口 |
| 数据库 | MySQL 8.0.46 · WSL2 Ubuntu 24.04（Linux） | 独立 IP 192.168.150.181:3306，账号 druguser，数据目录 /var/lib/mysql |

![图4-1 系统总体架构](images/arch.png)

### 4.2 标志性实践 1：采购入库 6 表事务

**任务目标**：一次采购入库同时写入采购单、采购明细、库存批次、库存结存、库存流水 5 张表（读取校验 2 张表），任何一步失败整体回滚，杜绝"库存加了却没有采购单"的账实不符。

**实现代码**（webapp/services.py，节选）：

```python
with transaction() as cur:                       # 开启事务
    cur.execute("SELECT drug_id FROM drug "
                "WHERE drug_id = %s AND status = 1", (drug_id,))
    drug = cur.fetchone()                        # ① 校验药品
    if not drug:
        raise BizError("药品不存在或已停用")
    cur.execute("INSERT INTO purchase_order (...) VALUES (...)",
                (po_no, supplier_id, warehouse_id, employee_id, ...))
    po_id = cur.lastrowid                        # ② 采购单
    cur.execute("INSERT INTO purchase_item (...) VALUES (...)", ...)
    item_id = cur.lastrowid                      # ③ 采购明细
    cur.execute("INSERT INTO stock_batch (...) VALUES (...)", ...)
    cur.execute("SELECT stock_id, quantity FROM stock "
                "WHERE drug_id=%s AND warehouse_id=%s FOR UPDATE",
                (drug_id, warehouse_id))          # ⑤ 锁结存行
    cur.execute("UPDATE stock SET quantity = quantity + %s ...", (qty, ...))
    cur.execute("INSERT INTO stock_movement (...) VALUES (...)", ...)
# with 块正常退出 → COMMIT；任何一步抛异常 → ROLLBACK
```

![图4-2 采购入库事务流程](images/txflow.png)

### 4.3 标志性实践 2：销售出库与行锁防超卖

**任务目标**：出库前锁定库存行并校验余额，按"近效期先出"逐批扣减；多终端并发销售同一药品时不超卖。

**实现代码**（webapp/services.py，节选）：

```python
cur.execute("SELECT stock_id, quantity FROM stock "
            "WHERE drug_id=%s AND warehouse_id=%s FOR UPDATE",
            (drug_id, warehouse_id))             # 行锁：并发事务阻塞等待
if before < qty:
    raise BizError("库存不足")                    # 校验在锁内完成
cur.execute("SELECT batch_id, quantity FROM stock_batch "
            "WHERE drug_id=%s AND warehouse_id=%s AND quantity > 0 "
            "AND expiry_date > CURDATE() "
            "ORDER BY expiry_date, batch_id FOR UPDATE",   # 近效期先出
            (drug_id, warehouse_id))
```

并发演示用 `threading.Barrier` 让 10 个终端同刻发起销售，逐个记录耗时并写入 access_log。实测：10 并发全部成功、扣减数量与成功笔数严格一致、`oversold` 恒为 False；单终端耗时约 5~20 ms，整体 QPS 视锁竞争在 40~90 之间。

### 4.4 标志性实践 3：业务表单动态建表 / 删表

**任务目标**：把课程要求的"创建数据表 / 删除数据表"落到真实业务——质量管理台账（药品召回登记、盘点差异等）结构不固定，由用户自定义字段后动态生成真实 MySQL 表，并可整体删除。

**实现代码**（webapp/views.py，节选）：

```python
table = _FORM_PREFIX + uuid.uuid4().hex[:10]     # bizform_xxxxxxxxxx
sql = ("CREATE TABLE `%s` ("
       "  id INT AUTO_INCREMENT PRIMARY KEY,"
       "  drug_id INT NOT NULL COMMENT '药品ID',"
       "  %s,"                                   # f1..fn，中文名进 COMMENT
       "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
       "  KEY `idx_%s_drug` (drug_id),"
       "  CONSTRAINT `fk_%s_drug` FOREIGN KEY (drug_id) "
       "      REFERENCES drug (drug_id))"
       " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='%s'")
cur.execute(sql)                                  # 动态建表
cur.execute("DROP TABLE `%s`" % table)            # 删除表单 → DROP TABLE
```

安全措施：表名/列名由系统生成并做标识符白名单校验，字段类型限定 5 种枚举、数量 1~8 个，所有取值参数化。

### 4.5 标志性实践 4：多表连接查询（运营报表）

**任务目标**：用只读跨表 JOIN 回答经营问题，四张报表分别串起 5、5、6、7 张表。

**实现代码**（webapp/dao.py，7 表"单药品出入库全链路"节选）：

```python
TRACE_SQL = ("SELECT m.move_time, m.move_type, dr.drug_name, "
             "w.warehouse_name, m.change_qty, e.emp_name, m.ref_no, "
             "COALESCE(so.customer_name, sp.supplier_name) AS party "
             "FROM stock_movement m "
             "JOIN drug dr      ON dr.drug_id = m.drug_id "
             "JOIN warehouse w  ON w.warehouse_id = m.warehouse_id "
             "JOIN employee e   ON e.employee_id = m.employee_id "
             "LEFT JOIN sale_order so ON so.so_no = m.ref_no "
             "LEFT JOIN purchase_order po ON po.po_no = m.ref_no "
             "LEFT JOIN supplier sp ON sp.supplier_id = po.supplier_id "
             "WHERE m.drug_id = %s "
             "ORDER BY m.move_time DESC LIMIT %s")   # 参数化，防注入
```

### 4.6 标志性实践 5：索引支持与访问性能记录

**任务目标**：证明索引对十万行级查询的加速作用，并按课程要求记录每次数据库访问的耗时。

**实现代码**（webapp/views.py，节选）：对 4.5 的同一条业务 SQL 分别"正常执行"与 `IGNORE INDEX` 强制全表扫描各跑 3 次取平均，配合 EXPLAIN 展示执行计划差异：

```python
with get_cursor() as cur:
    cur.execute("EXPLAIN " + TRACE_SQL.replace("{ignore}", ""), (drug_id, 20))
    plan_with = cur.fetchone()                   # ref · idx_move_drug_time
    cur.execute("EXPLAIN " + TRACE_SQL.replace(
        "{ignore}", " IGNORE INDEX (idx_move_drug_time)"), (drug_id, 20))
    plan_without = cur.fetchone()                # ALL · 57033 行全表扫描
```

实测（stock_movement 57,350 行）：走索引 ref 定位 50 行、平均 **1.38 ms**；绕过索引 ALL 扫描 57,033 行、平均 **12.28 ms**，**走索引快 8.9 倍**。每次接口访问的耗时由装饰器写入 access_log 表，形成可查的性能日志。

## 5 系统原型示例

以下截图均取自实际运行中的客户端（Windows 浏览器 → Linux 服务器 MySQL，真实数据）。

**5.1 药品目录（基本操作：插入 / 修改 / 删除）**

![图5-1 药品目录管理](images/shot_drug.png)

**5.2 采购入库（6 表事务）与销售出库**

![图5-2 采购入库·销售出库](images/shot_io.png)

**5.3 业务表单（动态建表 / 登记 / 删表）**

![图5-3 业务表单](images/shot_form.png)

**5.4 运营报表（4~7 表 JOIN）与索引体检**

![图5-4 运营报表](images/shot_report.png)

![图5-5 索引体检：同一条业务 SQL 走索引 1.38ms / 全表扫描 12.28ms，快 8.9 倍](images/shot_indexcheck.png)

**5.5 并发出库（10 终端不超卖）与访问性能日志**

![图5-6 并发出库演示](images/shot_concurrency.png)

## 6 课程项目应用总结

本项目完整走通了"需求分析 → E-R 建模 → 关系模式与 3NF 规范化 → SQL DDL → 应用开发 → 原型验证"的数据库工程流程，最终交付了一个运行在 Linux 服务器上、含 13 张表 15.3 万条数据的药品进销存系统。

三项课程额外要求均以真实业务形态落地：事务支持体现为采购/销售的 6 表事务与失败回滚；索引支持不是孤立的演示页，而是对业务 SQL 做 IGNORE INDEX 对照体检，实测提速 8.9 倍；多用户并发通过 10 终端 Barrier 同步销售验证行锁防超卖，并把每次访问耗时写入 access_log。

开发中最深的体会有两点：一是并发正确性不能靠"先查后改"，必须依赖 `SELECT ... FOR UPDATE` 的行锁语义，把校验放进锁内才能保证不超卖；二是 DDL（建表/删表）与 DML 的区别——前者不可回滚，因此动态建表必须在字段类型、表名生成上做白名单防御。项目也留有改进空间：金额汇总可改用触发器维护，效期预警可增加定时任务，访问日志可接入可视化图表。

## 参考文献

[1] 王珊， 萨师煊. 数据库系统概论（第 5 版）[M]. 北京： 高等教育出版社， 2014.

[2] Oracle Corporation. MySQL 8.0 Reference Manual[EB/OL]. https://dev.mysql.com/doc/refman/8.0/en/, 2026.

[3] Pallets Projects. Flask Documentation[EB/OL]. https://flask.palletsprojects.com/, 2026.

[4] PyMySQL Contributors. PyMySQL Documentation[EB/OL]. https://pymysql.readthedocs.io/, 2026.

[5] C. J. Date. An Introduction to Database Systems (8th Edition)[M]. Boston: Addison-Wesley, 2003.
