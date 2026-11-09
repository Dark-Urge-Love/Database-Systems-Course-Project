# 数据库系统课程项目

2024 级《数据库系统》课程设计。包含两个作业：关系型数据库应用（个人）与 NoSQL 数据库应用（小组）。

---

## 作业一：智慧医疗 · 社区门诊预约与处方管理系统

> **60 分 / 个人独立完成 / 第 10 周提交**

技术栈：**MySQL 8.0（Linux 部署）+ Python Flask（B/S 客户端）**

### 场景与建模

模拟社区卫生服务中心的门诊业务：患者挂号 → 医生接诊 → 开具处方 → 缴费结算。

- **10 个实体**：科室、医生、排班、患者、预约挂号、就诊记录、处方、药品、处方明细、缴费记录
- **三种联系齐备**：1-1（预约 ↔ 就诊，靠 `visit.appt_id` 唯一约束硬保证）、1-N（科室→医生→排班→预约）、N-M（处方 ↔ 药品，经处方明细拆分）
- **满足 3NF**：不冗余科室名、不复制挂号费、不存派生列（`subtotal`/`total_amount` 均由 `SUM()` 实时计算）；`prescription_item.unit_price` 作为「开方时成交价」的时态快照保留

### 数据规模

**10 张表，220,976 条真实数据**（要求 ≥10 万条），全部由脚本生成，外键与业务逻辑自洽：

| 表 | 行数 | 表 | 行数 |
|---|---:|---|---:|
| patient | 5,000 | prescription | 21,833 |
| department | 30 | drug | 500 |
| doctor | 300 | prescription_item | 60,000 |
| schedule | 20,000 | payment | 43,035 |
| appointment | 40,000 | visit | 29,910 |

数据一致性由 16 项不变量校验保证：号源 = 总数 − 未取消预约、明细单价与药品单价一致、1-1 约束不被破坏等。

### 功能实现

按课程要求做减法，只保留必需能力，共 4 个页面：

| 模块 | 内容 | 对应要求 |
|---|---|---|
| 药房业务 | 药品目录（新药入库 / 调价 / 停用删除）+ 业务档案（新建档案=建表、录入记录=插入、删除档案=删表）+ 运营报表（5 表 / 7 表关联）；界面只出现业务对象，不写表名 | ① 六项基本操作 |
| 挂号（事务 · 并发） | 扣减号源 + 生成预约同一事务，`SELECT ... FOR UPDATE` 防超卖；10 线程并发抢号并记录耗时 | ② 事务、③ 多用户并发性能 |
| 索引对比 | 同一查询走索引 / `IGNORE INDEX` 全表扫描，对比 `EXPLAIN` 与实测耗时 | ④ 索引支持 |
| 首页 · 要求对照 | 课程要求逐条映射到功能入口，并实时展示 10 表 22 万行数据规模 | ⑤ 数据量与关系证明 |
| 压测脚本 `tools/concurrency_test.py` | read / write / mixed 三场景，输出 TPS、P50 / P95 / P99 到 CSV | 多客户端访问性能记录 |

---

## 目录结构

```
.
├── 任务清单.md                      # 课程要求拆解
├── 2024级数据库系统课程项目(1).pdf   # 课程原始要求
└── 作业1_智慧医疗门诊预约系统/
    ├── 01_数据库设计方案.md          # ER 图、关系模式、3NF 论证、DDL、索引与事务方案
    ├── 02_Linux环境部署指南.md       # Ubuntu + MySQL 部署全流程
    ├── 03_客户端运行说明.md          # 启动方法、演示顺序、常见问题
    ├── 04_数据库部署验证记录.md      # 实测行数、完整性检查、join 统计
    ├── run.py                       # Flask 入口
    ├── requirements.txt
    ├── 启动客户端.bat
    ├── sql/
    │   ├── schema.sql               # 10 张表 DDL（外键、唯一键、CHECK 约束）
    │   ├── index.sql                # 幂等索引脚本
    │   ├── verify.sql               # 完整性校验 + 业务统计
    │   └── data_gen.py              # 22 万条数据生成器（--seed 可复现）
    ├── deploy/
    │   ├── ubuntu_mysql_setup.sh    # 服务器一键配置（bind-address、远程账号、防火墙）
    │   ├── deploy_db.sh             # 建表 → 灌数据 → 建索引 → 校验
    │   ├── wsl_oneclick.sh          # WSL Ubuntu 一键部署
    │   └── ...
    ├── tools/
    │   ├── check_conn.py            # 客户端连通性自检（端口/登录/库表/join）
    │   └── concurrency_test.py      # 并发性能压测
    └── webapp/                      # Flask 分层后端
        ├── config.py                # 配置（支持环境变量覆盖）
        ├── db.py                    # 连接与事务上下文
        ├── dao.py                   # SQL 数据访问层
        ├── services.py              # 业务事务（T1 挂号 / T2 开方）
        ├── views.py                 # 路由
        ├── netprobe.py              # 服务器地址自动发现
        ├── templates/               # 5 个页面模板
        └── static/style.css
```

---

## 快速开始

### 1. 准备 Linux 服务器

在 Ubuntu（虚拟机或 WSL）上执行：

```bash
bash deploy/ubuntu_mysql_setup.sh   # 安装并配置 MySQL
bash deploy/deploy_db.sh            # 建表 + 生成 22 万条数据 + 建索引 + 校验
```

### 2. 启动客户端

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python run.py
```

浏览器打开 <http://127.0.0.1:5000>。Windows 下也可直接双击 `启动客户端.bat`。

### 3. 连接参数

默认连接配置（可用环境变量覆盖，见 `webapp/config.py`）：

| 项 | 默认值 |
|---|---|
| 主机 | 自动探测；也可用 `DB_HOST` 指定 |
| 端口 | 3306 |
| 数据库 | outpatient_db |
| 应用账号 | clinic |

> 客户端内置地址自动发现：依次尝试 `wsl_ip.txt` → 历史成功缓存 → 扫描 WSL 网段，并用真实账号登录 + 校验 `@@datadir` 为 Linux 路径，避免误连到本机 MySQL。

---

## 作业二：NoSQL 数据库应用

> **40 分 / 小组 3~5 人 / 第 17 周答辩**

待开展。

---

## 说明

- 本项目所有代码与 SQL 均为原创实现，未使用任何开源项目代码。
- 本项目为课程作业，数据库口令仅用于本地教学环境。
