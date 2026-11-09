# 数据库系统课程项目

2024 级《数据库系统》课程设计。包含两个作业：关系型数据库应用（个人）与 NoSQL 数据库应用（小组）。

---

## 作业一：药品进销存管理系统

> **60 分 / 个人独立完成 / 第 10 周提交**

技术栈：**MySQL 8.0（Linux 部署）+ Python Flask（B/S 客户端）**

### 场景与建模

模拟医药流通企业的仓储配送业务：供应商供货 → 采购入库（批次/效期）→ 仓库结存 → 销售出库（近效期先出）→ 库存流水 → 批次溯源。

- **13 个实体**：药品分类、供应商、仓库、员工、药品、采购单、采购明细、库存、库存批次、销售单、销售明细、库存流水、访问日志
- **21 条联系，三种基数齐备**：1-1（采购明细 ↔ 库存批次，靠 `uk_batch_purchase` 唯一约束硬保证）、1-N（19 个外键）、N-M（采购单 ↔ 药品、销售单 ↔ 药品，经 `purchase_item` / `sale_item` 拆分）
- **满足 3NF**：不冗余分类名/仓库名/供应商名；库存独立成表（多仓结存是"一药多仓"的多值属性，放药品表会违反 1NF）；价格一律用「时态快照」区分语义（当前价 / 成交价 / 批次成本）；单据头金额属受控冗余，由同一事务维护

### 数据规模

**13 张表，153,072 条真实数据**（要求 ≥10 万条），全部由脚本按业务时间轴推演生成，账实自洽：

| 表 | 行数 | 表 | 行数 |
|---|---:|---|---:|
| drug_category | 12 | purchase_item | 15,695 |
| supplier | 80 | stock | 6,360 |
| warehouse | 6 | stock_batch | 15,275 |
| employee | 60 | sale_order | 12,031 |
| drug | 1,200 | sale_item | 41,500 |
| purchase_order | 3,503 | stock_movement | 57,350 |

数据一致性由 **6 项自洽校验**保证（全部 0 异常）：库存结存 = 批次结存之和、流水 `after = before + change`、采购/销售明细金额 = 数量 × 单价、库存与批次无负数。

数据本身也是"药品"的：1,200 条真实药名（阿莫西林胶囊、丙酸氟替卡松鼻喷雾剂、甲巯咪唑片…），规格与剂型严格匹配，价格按包装量推算。

### 功能实现

按课程要求做减法，只保留必需能力，共 3 个页面：

| 模块 | 内容 | 对应要求 |
|---|---|---|
| 进销存业务 `/ops` | ① 药品目录（新增 / 调价 / 停用删除，同名同规格拦截）② 采购入库 · 销售出库（**6 张表同一事务**、行锁、近效期先出、过期批次拦截）③ 业务表单（新建表单=建表、登记=插入、删除表单=删表）④ 运营报表（5 / 5 / 6 / **7 表 JOIN**）⑤ 并发出库（10 终端不超卖 + 逐次耗时入 `access_log`） | 六项基本操作、事务、并发 |
| 索引对比 `/perf` | 同一查询"走索引 / `IGNORE INDEX` 全表扫描"，对比 `EXPLAIN` 与实测耗时 | 索引支持 |
| 数据概览 `/` | 课程要求逐条映射到功能入口，实时展示 13 表 15.3 万行、库存结存、近效期批次、服务器信息 | 数据量与关系证明 |
| 压测脚本 `tools/concurrency_test.py` | read / write / mixed 三场景，输出 TPS、P50 / P95 / P99 到 CSV | 多客户端访问性能记录 |

### 实测结果

| 项目 | 结果 |
|---|---|
| 课程要求逐条实测（`tools/selfcheck_req.py`） | **13 项全过** |
| 接口层自检（`tools/selfcheck_api.py`） | **39 项全过** |
| 建模一致性（`model_gen.py --verify`） | **13 张表结构完全一致** |
| 并发出库（10 终端，库存 8） | 成功 8 / 失败 2 / 196.96 ms / 库存 8→0 / **无超卖** |
| 压测（10 客户端 × 6 秒） | read **496.2 TPS** / write **184.2 TPS** / mixed **545.2 TPS**，成功率 100% |
| 索引对比 | 按药查流水 **13.84×**、时间范围聚合 **5.18×**（`ALL` → `range`） |

---

## 目录结构

```
.
├── 任务清单.md                      # 课程要求拆解
├── 2024级数据库系统课程项目(1).pdf   # 课程原始要求
└── 作业1_药品进销存系统/
    ├── 01_数据库设计方案.md          # ER 图、关系模式、3NF 论证、表结构、索引与事务方案
    ├── 02_Linux环境部署指南.md       # Ubuntu + MySQL 部署全流程
    ├── 03_客户端运行说明.md          # 启动方法、演示顺序、常见问题
    ├── 04_数据库部署验证记录.md      # 实测行数、账实自洽、JOIN 统计、压测数据
    ├── 05_课程要求对照表.md          # 要求 ↔ 功能逐条对照 + 实测结果
    ├── run.py                       # Flask 入口
    ├── requirements.txt
    ├── 启动客户端.bat
    ├── sql/
    │   ├── er_model.json            # 建模文件（13 实体 / 21 联系）
    │   ├── er_diagram.mmd           # Mermaid ER 源码
    │   ├── schema.sql               # 13 张表 DDL（19 外键、13 CHECK、唯一约束）
    │   ├── schema_generated.sql     # 由模型自动生成的 DDL（与上者结构一致）
    │   ├── index.sql                # 幂等索引脚本（24 个普通索引）
    │   ├── verify.sql               # 数据量 + 账实自洽 + 结构 + 业务样例 + 索引对比
    │   └── data_gen.py              # 15.3 万条数据生成器（--seed 可复现）
    ├── deploy/
    │   ├── ubuntu_mysql_setup.sh    # 服务器一键配置（bind-address、远程账号、防火墙）
    │   ├── deploy_db.sh             # 建表 → 灌数据 → 建索引 → 校验
    │   ├── wsl_oneclick.sh          # WSL Ubuntu 一键部署
    │   ├── wsl_setup_autostart.sh   # 安装 MySQL 开机自启
    │   ├── wsl_boot_start.sh        # 开机脚本（起 MySQL + 写出 wsl_ip.txt）
    │   └── bash_aliases.sh          # dbup / dbstatus 快捷命令
    ├── tools/
    │   ├── model_gen.py             # 模型 → DDL / ER 图（含 --verify 逐表比对）
    │   ├── render_er.py             # Mermaid → PNG（Chrome headless）
    │   ├── check_conn.py            # 客户端连通性自检（端口/登录/库表/join）
    │   ├── selfcheck_req.py         # 课程要求逐条实测（数据库层，13 项）
    │   ├── selfcheck_api.py         # 接口层自检（39 项）
    │   └── concurrency_test.py      # 并发性能压测
    ├── webapp/                      # Flask 分层后端
    │   ├── config.py                # 配置（支持环境变量覆盖）
    │   ├── db.py                    # 连接与事务上下文
    │   ├── dao.py                   # SQL 数据访问层（含 4 张 JOIN 报表）
    │   ├── services.py              # 业务事务（采购入库 / 销售出库 / 并发出库）
    │   ├── views.py                 # 路由 + Ajax 接口 + 动态业务表单
    │   ├── netprobe.py              # 服务器地址自动发现
    │   ├── templates/               # 3 个页面模板
    │   └── static/style.css
    └── docs/
        ├── images/er_diagram.png    # ER 图
        └── screens/                 # 7 张运行截图
```

---

## 快速开始

### 1. 准备 Linux 服务器

在 Ubuntu（虚拟机或 WSL）上执行：

```bash
bash deploy/ubuntu_mysql_setup.sh   # 安装并配置 MySQL（root 密码、远程账号 druguser、bind-address）
bash deploy/deploy_db.sh            # 建 13 张表 + 生成 15.3 万条数据 + 建索引 + 校验
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

客户端**只连 Linux 服务器上的 MySQL**（课程硬性要求），代码里没有任何"连本机 MySQL"的退路：
连不上就在页面上明确报错，不会偷偷改成连本机。

| 项 | 值 |
|---|---|
| 主机 | 自动探测（`wsl_ip.txt` → 上次缓存 → 默认 `192.168.150.181` → 扫描虚拟网段） |
| 端口 | 3306 |
| 数据库 | `drug_db` |
| 账号 / 密码 | `druguser` / `Drug@123456` |

判定"是不是那台服务器"有两个条件：账号登录成功 **且** `@@datadir` 是 Linux 路径（以 `/` 开头）。
页面「数据概览 → 数据库连接」会显示主机名与数据目录，答辩时这就是"数据在 Linux 上"的现场证据。

可用环境变量覆盖：

```bat
set DB_HOST=192.168.150.181 && python run.py     :: 锁定服务器地址
```

> 服务器上若还没有 `druguser` 账号与 `drug_db` 数据（例如之前部署的是别的系统），
> 在 Ubuntu 里执行一次 `bash deploy/init_server_db.sh` 即可补齐（建账号 + 建库 + 15.3 万条数据 + 索引 + 校验）。

### 4. 自检（交作业前跑一遍）

```bash
python tools/check_conn.py --host <服务器IP> --user druguser --password Drug@123456
python tools/selfcheck_req.py      # 13 项：表/数据量/外键/1-1/N-M/CHECK/回滚/行锁/索引
python tools/selfcheck_api.py      # 39 项：页面/CRUD/表单建删/6 表事务/报表/并发/索引对比
```

---

## 作业二：NoSQL 数据库应用

> **40 分 / 小组 3~5 人 / 第 17 周答辩**

待开展。

---

## 说明

- 本项目所有代码与 SQL 均为原创实现，未使用任何开源项目代码（Flask / PyMySQL 仅作为框架与驱动）。
- 本项目为课程作业，数据库口令仅用于本地教学环境，请勿复用到真实系统。
