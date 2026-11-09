# 02 · Linux 服务器端部署指南

> 目标：把 Ubuntu 变成一台 **Windows 能远程访问的 MySQL 服务器**，并灌入 22 万条数据。
> 这是作业 1 的"地基"——这一步通了，后面 Flask 客户端和并发测试才能跑。
> 对应课程要求：*服务器端全部在 Linux 部署* + *B/S 模式，客户端与服务器端包含独立 IP*

---

## 为什么下一步是"环境打通"而不是"写代码"

依赖链是这样的：**MySQL 服务 → 网络可达 → 账号授权 → 建表灌数据 → Flask 能连上 → 并发测试**。
任何一环没通，后面都会以"Flask 报错"的形式暴露出来，但根因其实在网络或授权上，非常难排查。
所以先把环境打通、并且**用工具证明它是通的**，是最省时间的做法。

---

## 前置检查

### 1. 确认 Ubuntu 真的能跑起来

- 如果下载的是 **ISO 文件**：还需要虚拟机软件才能装。选一个：
  | 软件 | 说明 |
  |---|---|
  | VMware Workstation Player | 个人免费，网络配置最省心 |
  | VirtualBox | 完全免费开源 |
  | Hyper-V | Windows 专业版自带，但网络配置相对麻烦 |
- 如果已经有可运行的 Ubuntu（虚拟机 / 双系统 / WSL2），跳到下一步。
  > ⚠️ **WSL2 不推荐**用于本作业：它的网络与独立 IP 要求不好满足，且答辩时"Linux 部署"的说服力弱。优先用真正的虚拟机。

### 2. 选择网络模式（决定 IP 是否独立、是否互通）

| 模式 | Ubuntu 有独立 IP | Windows → Ubuntu | 推荐度 |
|---|---|---|---|
| **桥接（Bridged）** | ✅ 与主机同网段，独立 IP | ✅ 直接访问 | ⭐ 首选 |
| NAT（VMware） | ✅ 独立 IP（vmnet8 网段） | ✅ 直接访问 | ⭐ 可用 |
| NAT（VirtualBox） | ✅ 独立 IP（10.0.2.x） | ⚠️ 需配端口转发 | 可用 |
| 仅主机（Host-only） | ✅ 独立 IP | ✅ 直接访问 | 可用（但虚拟机不能上网，装软件麻烦） |

**建议用桥接**。若校园网限制桥接，VMware 的 NAT 也可直接互通。

> VirtualBox 若只能用 NAT，需配置端口转发：`设置 → 网络 → 高级 → 端口转发`，新增
> 名称 `mysql`，协议 `TCP`，主机端口 `3306`，子系统端口 `3306`，子系统 IP 留空。

---

## 步骤 1：把项目传到 Ubuntu

Ubuntu Desktop 建议先装 SSH 服务（方便传文件）：
```bash
sudo apt-get install -y openssh-server
```

然后在 **Windows 的 Git Bash 或 PowerShell** 里执行 `scp` 传输（Windows 10+ 自带 scp）：

```bash
# 把整个项目目录传到 Ubuntu 的家目录（把 IP 和用户名换成你的）
scp -r "D:/lyl coding/大三上/数据库课设/作业1_智慧医疗门诊预约系统" 你的用户名@192.168.1.100:/home/你的用户名/
```

传不进去也没关系——脚本内容不长，也可以在 Ubuntu 里用 `nano` 手动创建。

---

## 步骤 2：Ubuntu 内一键配置 MySQL

```bash
cd ~/作业1_智慧医疗门诊预约系统/deploy
chmod +x ubuntu_mysql_setup.sh
./ubuntu_mysql_setup.sh
```

脚本会自动完成 6 件事：

| 步骤 | 内容 |
|---|---|
| 1 | `apt` 安装 mysql-server |
| 2 | 启动服务并设置开机自启 |
| 3 | 设置 root 密码、创建远程账号 `clinic`@`%`、建库、授权 |
| 4 | 改 `bind-address = 0.0.0.0`（允许远程连接）并重启服务 |
| 5 | 若 ufw 启用则放行 3306/tcp |
| 6 | 打印虚拟机 IP、监听状态和连接参数 |

**默认密码**（可在命令前加环境变量覆盖）：
```bash
ROOT_PW='Root@123456' APP_PW='Clinic@123456' ./ubuntu_mysql_setup.sh
```

跑完请**记下输出的 IP 和账号密码**，Windows 端要用。

---

## 步骤 3：Windows 端验证连通性（关键一步）

先装依赖：
```bash
pip install pymysql
```

再跑检测工具（Windows 端，把 IP 换成上一步得到的）：
```bash
cd "D:/lyl coding/大三上/数据库课设/作业1_智慧医疗门诊预约系统"
python tools/check_conn.py --host 192.168.1.100 --user clinic --password Clinic@123456
```

它会依次检查 **网络端口 → 账号登录 → 库表数据量 → 真实 join 查询**，并在失败时直接给出排查方向。
**这一步通过后，环境就彻底没问题了**，截图保存，是报告"实现环境配置说明"的现成素材。

> 此时数据库还是空的（第 3 步只建了库），`check_conn.py` 会提示"库存在但没有表"，
> 这是**正常的**——继续做步骤 4 灌数据即可。

---

## 步骤 4：建表 + 灌入 22 万条数据

在 **Ubuntu** 里执行：
```bash
cd ~/作业1_智慧医疗门诊预约系统/deploy
chmod +x deploy_db.sh
./deploy_db.sh
```

执行顺序：`schema.sql`（建 10 张表）→ `data_gen.py`（生成 220,608 条）→ `index.sql`（建索引）→ `verify.sql`（校验）。

预期输出结尾：
```
 department .......................... 30
 ...
 payment .......................... 43,035
 合计                             220,608
 耗时 xx 秒  |  ✓ 已满足 ≥100,000 条要求
```

---

## 步骤 5：再次验证（这次应该全绿）

回到 Windows 重跑：
```bash
python tools/check_conn.py --host 192.168.1.100 --user clinic --password Clinic@123456
```

这次应当看到各表行数、总量 220,608、并且多表 join 查询成功返回各科室就诊量 Top3。

---

## 常见故障速查表

| 现象 | 原因 | 解决 |
|---|---|---|
| 连接超时 | `bind-address` 还是 `127.0.0.1` | 改为 `0.0.0.0` 后 `sudo systemctl restart mysql` |
| 连接超时 | 防火墙拦截 | `sudo ufw allow 3306/tcp` |
| 连接超时 | 虚拟网络模式不对 | 换桥接，或配 NAT 端口转发 |
| 错误码 **1045** | 账号密码不对 | 重建账号（见 `check_conn.py` 输出提示） |
| 错误码 **1130** | 账号主机名是 `localhost` | 必须是 `'clinic'@'%'` 才能从外部连 |
| 错误码 **1049** | 库不存在 | 先跑 `deploy_db.sh` |
| `pip` 报 externally-managed | Ubuntu 24.04 的 PEP 668 限制 | `python3 -m pip install pymysql --break-system-packages` |
| Windows 侧 `ping` 不通 | 网络模式问题 | 先解决网络，再谈数据库 |

---

## 本步骤要留的证据（报告用）

1. Ubuntu 终端里 `ip a` 的输出截图 → 证明**服务器端在 Linux + 有独立 IP**
2. Ubuntu 终端里 `mysql --version` 和 `sudo ss -tlnp | grep 3306` 截图 → 证明**数据库服务在运行**
3. Windows 里 `ipconfig` 截图 → 证明**客户端有独立 IP**（与服务器端 IP 不同）
4. `check_conn.py` 全绿输出截图 → 证明**跨网络的 B/S 访问链路打通**
5. `verify.sql` 的输出截图 → 证明**数据量达标 + 完整性通过**

这 5 张截图基本就覆盖了报告里"实现环境配置说明"一章。

---

## 完成后

环境通了之后，剩下的工作就是纯代码了：
1. **Flask 客户端**（要求对照页 + 药房采购工作台 + 挂号页 + 索引对比页）
2. **并发性能测试** `tools/concurrency_test.py`
3. **项目报告文档**

告诉我 `check_conn.py` 是否全绿，或者卡在哪一步报什么错，我接着往下写。
