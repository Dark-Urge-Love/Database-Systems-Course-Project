# 02 · Linux 服务器端部署指南

> 目标：把一台 Linux（Ubuntu）变成 **Windows 能远程访问的 MySQL 服务器**，并灌入 15.3 万条药品进销存数据。
> 这是作业 1 的"地基"——这一步通了，后面的 Flask 客户端与并发测试才有意义。
> 对应课程要求：*服务器端全部在 Linux 部署* + *B/S 模式，客户端与服务器端包含独立 IP*

---

## 为什么先"打通环境"而不是先"写代码"

依赖链是这样的：

```
MySQL 服务 → 网络可达 → 账号授权 → 建表灌数据 → Flask 能连上 → 并发测试
```

任何一环没通，最终都会以"Flask 报错"的形式暴露出来，但根因其实在网络或授权上，非常难排查。
所以先把环境打通、并且**用工具证明它是通的**，是最省时间的做法。

本项目提供了两条路径，脚本完全相同：

| 路径 | 适用 | 说明 |
|---|---|---|
| **A. 真机 / 虚拟机（推荐答辩用）** | 有 VMware / VirtualBox / 双系统 | 服务器端是"另一台机器 + 独立 IP"，最符合课程描述 |
| **B. WSL2 Ubuntu** | 只有一台 Windows | 本项目实测环境；独立网段 IP（如 `192.168.150.181`），Windows 通过 TCP 3306 访问，同样满足"服务器端在 Linux + 独立 IP"；配有开机自启脚本 |

---

## 前置检查

### 1. 确认 Linux 能跑起来

- **ISO 文件**：需要虚拟机软件，选一个：

  | 软件 | 说明 |
  |---|---|
  | VMware Workstation Player | 个人免费，网络配置最省心 |
  | VirtualBox | 完全免费开源 |
  | Hyper-V | Windows 专业版自带，网络配置相对麻烦 |

- **WSL2**：管理员 PowerShell 执行 `wsl --install -d Ubuntu-24.04`，装完打开一次 Ubuntu 完成初始化（设用户名/密码）。

### 2. 选择网络模式（决定 IP 是否独立、是否互通）

| 模式 | Linux 有独立 IP | Windows → Linux | 推荐度 |
|---|---|---|---|
| **桥接（Bridged）** | ✅ 与主机同网段，独立 IP | ✅ 直接访问 | ⭐ 首选 |
| NAT（VMware） | ✅ 独立 IP（vmnet8 网段） | ✅ 直接访问 | ⭐ 可用 |
| NAT（VirtualBox） | ✅ 独立 IP（10.0.2.x） | ⚠️ 需配端口转发 | 可用 |
| 仅主机（Host-only） | ✅ 独立 IP | ✅ 直接访问 | 可用（但虚拟机不能上网，装软件麻烦） |
| **WSL2** | ✅ 独立虚拟网段 IP | ✅ 直接访问（无需端口转发） | ⭐ 单机开发首选 |

> VirtualBox 若只能用 NAT，需配端口转发：`设置 → 网络 → 高级 → 端口转发`，新增
> 名称 `mysql`，协议 `TCP`，主机端口 `3306`，子系统端口 `3306`，子系统 IP 留空。

---

## 步骤 1：把项目传到 Linux

Ubuntu 先装 SSH 服务（方便传文件）：

```bash
sudo apt-get install -y openssh-server
```

在 **Windows 的 Git Bash 或 PowerShell** 里执行 `scp`（Windows 10+ 自带 scp）：

```bash
# 把整个项目目录传到 Linux 的家目录（IP 和用户名换成你的）
scp -r "D:/lyl coding/大三上/数据库课设/作业1_药品进销存系统" 你的用户名@192.168.1.100:/home/你的用户名/
```

> WSL2 不需要传：项目就在 `/mnt/d/lyl coding/大三上/数据库课设/作业1_药品进销存系统`，
> 脚本会自动识别这个路径（也可用 `PROJ=` 指定，见 `deploy/wsl_oneclick.sh` 开头）。

---

## 步骤 2：Linux 内一键配置 MySQL

```bash
cd ~/作业1_药品进销存系统/deploy
chmod +x ubuntu_mysql_setup.sh
./ubuntu_mysql_setup.sh
```

脚本会自动完成 6 件事：

| 步骤 | 内容 |
|---|---|
| 1 | `apt` 安装 mysql-server |
| 2 | 启动服务并设置开机自启 |
| 3 | 设置 root 密码、创建远程账号 `druguser`@`%`、建库 `drug_db`、授权 |
| 4 | 改 `bind-address = 0.0.0.0`（允许远程连接）并重启服务 |
| 5 | 若 ufw 启用则放行 3306/tcp |
| 6 | 打印 Linux IP、监听状态和连接参数 |

**默认账号**（可在命令前加环境变量覆盖）：

```bash
ROOT_PW='Root@123456' APP_USER='druguser' APP_PW='Drug@123456' DB_NAME='drug_db' ./ubuntu_mysql_setup.sh
```

| 项 | 默认值 |
|---|---|
| root 密码 | `Root@123456` |
| 应用账号 | `druguser`@`%` |
| 应用密码 | `Drug@123456` |
| 数据库 | `drug_db` |

跑完请**记下输出的 IP 和账号**，Windows 端要用。

> **WSL2 用户**：可直接跑 `deploy/wsl_oneclick.sh`，它把"换清华源 → 装 MySQL → 建账号 →
> 改 bind-address → 建表灌数据 → 建索引 → 校验"合并为一次执行，日志写到 `~/db_deploy.log`。
> ```bash
> bash /mnt/d/lyl\ coding/大三上/数据库课设/作业1_药品进销存系统/deploy/wsl_oneclick.sh
> ```

---

## 步骤 2.5：已有 Ubuntu / MySQL，只是缺账号或数据（最常见）

如果你**之前部署过别的系统**（比如旧的 `clinic` 账号 + `outpatient_db`），
或者只是重新灌了一次库，那么 `druguser` 账号与 `drug_db` 数据可能并不存在。
此时客户端会报 **1045 Access denied** 或直接显示"数据库未连接"。

在 Ubuntu 里一条命令补齐（建账号 + 建库 + 13 张表 + 15.3 万条数据 + 索引 + 校验）：

```bash
cd ~/作业1_药品进销存系统          # 或 /mnt/d/lyl\ coding/.../作业1_药品进销存系统
bash deploy/init_server_db.sh
```

脚本做的事：

| 步骤 | 内容 |
|---|---|
| 0 | 探测管理员连接方式（`sudo mysql` 免密 / `root` 密码） |
| 1 | 建 `druguser`@`%`（密码 `Drug@123456`）+ 建库 `drug_db` + 只授权 `drug_db.*` |
| 2 | 执行 `sql/schema.sql`（13 张表、19 外键、13 CHECK） |
| 3 | 执行 `sql/data_gen.py`（153,072 条，末尾自动 6 项自洽校验） |
| 4 | 执行 `sql/index.sql`（建索引，幂等） |
| 5 | 执行 `sql/verify.sql`（数据量 / 账实自洽 / 结构统计 / 业务样例） |

两种常见用法：

```bash
# root 已设过密码的情况
ROOT_PW='你的root密码' bash deploy/init_server_db.sh

# 只想补账号、数据已经灌过了
SKIP_DATA=1 bash deploy/init_server_db.sh
```

> 只建账号不灌数据也行：`sudo mysql < deploy/create_app_user.sql`。
>
> 脚本可重复执行：账号用 `CREATE USER IF NOT EXISTS` + `ALTER` 覆盖密码，
> 库表由 `schema.sql` 开头 `DROP DATABASE` 重建，**执行前请确认库里没有要保留的数据**。

跑完后在 Windows 端验证：

```bash
python tools/check_conn.py --host 192.168.150.181 --user druguser --password Drug@123456
```

---

## 步骤 3：Windows 端验证连通性（关键一步）

先装依赖：

```bash
pip install pymysql
```

再跑检测工具（Windows 端，IP 换成上一步得到的）：

```bash
cd "D:/lyl coding/大三上/数据库课设/作业1_药品进销存系统"
python tools/check_conn.py --host 192.168.1.100 --user druguser --password Drug@123456
```

它依次检查 **TCP 端口 → 账号登录 → 库表与数据量 → 一次真实多表 JOIN**，失败时直接给出排查方向：

```
[1/4] 检测 TCP 端口 192.168.1.100:3306 ...
      [通过] 端口可达，耗时 3 ms
[2/4] 检测 MySQL 账号登录 ...
      [通过] 登录成功，MySQL 版本 8.0.45
[3/4] 检测数据库 drug_db 与数据 ...
      [通过] 共 13 张表：access_log, drug, drug_category, ...
      [通过] 数据量 153,072 条，满足 ≥100,000 要求
[4/4] 执行一次多表 join 业务查询 ...
      [通过] 查询成功，耗时 12 ms，采购金额 Top3 供应商：...
```

> 此时数据库里还没有表（步骤 2 只建了空库），`check_conn.py` 会提示"库存在但没有表"，
> 这是**正常的**——继续步骤 4 灌数据即可。

**这一步通过后，环境就彻底没问题了**，截图保存，是报告"实现环境配置说明"的现成素材。

---

## 步骤 4：建表 + 灌入 15.3 万条数据

在 **Linux** 里执行：

```bash
cd ~/作业1_药品进销存系统/deploy
chmod +x deploy_db.sh
./deploy_db.sh
```

执行顺序：

| 步骤 | 脚本 | 内容 |
|---|---|---|
| 1 | `sql/schema.sql` | 建库 `drug_db` + 13 张表（19 外键 / 13 CHECK / 唯一约束） |
| 2 | `sql/data_gen.py` | 生成 153,072 条数据，末尾自动跑 6 项自洽校验 |
| 3 | `sql/index.sql` | 建 24 个普通索引（幂等，可重复执行） |
| 4 | `sql/verify.sql` | 数据量统计 + 账实自洽 + 结构统计 + 4 个业务样例 + 索引对比 |

预期输出（数据生成部分）：

```
[6/6] 写入数据库
  drug_category          12 行
  warehouse               6 行
  supplier               80 行
  employee               60 行
  drug                 1200 行
  purchase_order       3503 行
  purchase_item       15695 行
  stock_batch         15275 行
  sale_order          12031 行
  sale_item           41500 行
  stock_movement      57350 行
  stock                6360 行

合计写入 153072 行，耗时 6.3 秒

自洽性校验：
  库存结存 = 批次结存之和                通过 ✔
  流水 after = before + change          通过 ✔
  采购明细金额 = 数量 × 单价              通过 ✔
  销售明细金额 = 数量 × 单价              通过 ✔
  库存无负数                            通过 ✔
  批次结存无负数                         通过 ✔
```

---

## 步骤 5：再次验证（这次应该全绿）

回到 Windows 重跑：

```bash
python tools/check_conn.py --host 192.168.1.100 --user druguser --password Drug@123456
```

这次应看到 13 张表行数、总量 153,072，并成功返回"采购金额 Top3 供应商"。

再跑一遍数据库层的课程要求自检（13 项）：

```bash
python tools/selfcheck_req.py
```

```
  [PASS] 0) MySQL 服务器可连接        MySQL 8.0.45 @ 192.168.1.100:3306/drug_db
  [PASS] 1) 数据表 ≥ 5 张             12 张业务表（另 1 张访问日志表）
  [PASS]    数据总量 ≥ 100,000 条      合计 153,072 条
  [PASS] 2) 1-N 联系（外键）           19 个外键约束
  [PASS]    1-1 联系                   stock_batch.purchase_item_id 唯一索引 uk_batch_purchase
  [PASS]    N-M 联系                   purchase_item / sale_item 双外键桥接
  [PASS] 3) CHECK 约束兜底             13 个 CHECK
  ...
结果：通过 13 项，失败 0 项
```

---

## 常见故障速查表

| 现象 | 原因 | 解决 |
|---|---|---|
| 连接超时 | `bind-address` 还是 `127.0.0.1` | 改为 `0.0.0.0` 后 `sudo systemctl restart mysql` |
| 连接超时 | 防火墙拦截 | `sudo ufw allow 3306/tcp` |
| 连接超时 | 虚拟网络模式不对 | 换桥接，或配 NAT 端口转发 |
| 连接超时 | WSL2 IP 变了 | 在 Ubuntu 里 `hostname -I` 看新 IP；客户端会自动重新探测 |
| 错误码 **1045** `druguser` | 服务器上还没建这个账号（换系统后最常见） | 在 Ubuntu 里 `bash deploy/init_server_db.sh` |
| 错误码 **1045**（其他账号） | 账号密码不对 | 重建账号（`check_conn.py` 会给出提示） |
| 错误码 **1130** | 账号主机名是 `localhost` | 必须是 `'druguser'@'%'` 才能从外部连 |
| 错误码 **1049** | 库不存在 | 先跑 `deploy/deploy_db.sh` |
| 错误码 **3819** | CHECK 约束被违反 | 业务上等价于"库存不能为负"，客户端已翻译成中文提示 |
| 错误码 **1451** | 外键引用导致删不掉 | 先删子表数据，或改为"停用" |
| `pip` 报 externally-managed | Ubuntu 24.04 的 PEP 668 限制 | `python3 -m pip install pymysql --break-system-packages` |
| 数据生成报"批次与库存不符" | 生成逻辑被改动 | 回滚 `sql/data_gen.py`，6 项校验必须全 0 |
| Windows 侧 `ping` 不通 | 网络模式问题 | 先解决网络，再谈数据库 |

---

## WSL2 开机自启（本项目实际使用）

WSL 每次开机 MySQL 不会自己起来，用两个脚本解决：

```bash
# 1) 安装开机自启（写 /etc/wsl.conf 的 [boot] command）
bash deploy/wsl_setup_autostart.sh

# 2) 开机脚本本体（被复制为 ~/drug-db-up.sh，负责起 MySQL + 写出 wsl_ip.txt）
bash deploy/wsl_boot_start.sh
```

装完之后：**每次重启电脑，只要打开一次 Ubuntu 图标，MySQL 自动启动**，并把本机 IP 写到
`~/wsl_ip.txt` 与 Windows 项目目录下的 `wsl_ip.txt`，供客户端自动定位服务器。

`deploy/bash_aliases.sh` 额外提供两个快捷命令（放进 `~/.bash_aliases` 即可）：

```bash
dbup        # 启动 MySQL
dbstatus    # MySQL 运行中 · 192.168.x.x:3306
```

---

## 本步骤要留的证据（报告用）

1. Linux 终端 `ip a` 输出 → 证明**服务器端在 Linux + 有独立 IP**
2. Linux 终端 `mysql --version` 与 `sudo ss -tlnp | grep 3306` → 证明**数据库服务在运行**
3. Windows `ipconfig` → 证明**客户端有独立 IP**（与服务器端不同）
4. `check_conn.py` 全绿输出 → 证明**跨网络的 B/S 访问链路打通**
5. `verify.sql` 输出 → 证明**数据量达标 + 账实自洽**
6. `selfcheck_req.py` 全 PASS → 证明**课程要求逐条落实**

这 6 张截图基本覆盖报告里"实现环境配置说明"一章。

---

## 完成后

环境通了之后，剩下的就是纯代码与文档：

1. **Flask 客户端**：进销存业务单页（5 个页签：药品目录 / 采购入库·销售出库 / 业务表单 / 运营报表 / 并发出库）
2. **并发性能测试**：`tools/concurrency_test.py`（read / write / mixed 三场景）
3. **文档**：`01`~`05` 五份 + `README.md`
