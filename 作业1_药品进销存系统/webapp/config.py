# -*- coding: utf-8 -*-
"""
配置模块
------------------------------------------------------------------
系统有两套可用的数据库连接目标，客户端启动时会自动选一个能用的：

  A. Linux 服务器（课程要求的正式环境）
     Ubuntu + MySQL 8.0，通过 TCP/IP 访问，库 drug_db
     账号 druguser / Drug@123456

  B. 本机 MySQL（开发调试用）
     Windows 本机 127.0.0.1，库 drug_db（数据与 A 完全一致）
     账号 root / 123456

启动顺序：A 优先，A 连不上自动回退 B，并在页面顶部与启动日志里明确标注
当前连的是哪一套 —— 答辩演示前请确认连的是 A（"数据概览 → 数据库连接"里
"数据目录"应是 /var/lib/mysql/，而不是 D:\\MySQL\\...）。

所有配置都可以用环境变量覆盖：
    set DB_HOST=192.168.150.181 && python run.py     # 锁定服务器地址
    set ALLOW_LOCAL_FALLBACK=0 && python run.py      # 禁止回退本机，只认服务器
"""
import os


def _env(key, default):
    """读环境变量，为空则用默认值。"""
    value = os.environ.get(key)
    return value if value not in (None, "") else default


# --------------------------------------------------------------------
# A. Linux 服务器（正式环境）
# --------------------------------------------------------------------
# 说明：Ubuntu(WSL) 每次重启后虚拟网卡 IP 会重新分配，所以这里给出的是
# "上次用过的地址"。启动时 netprobe 会自动探测并覆盖它（见 webapp/netprobe.py）。
SERVER_HOST = _env("SERVER_HOST", "192.168.150.181")
SERVER_USER = _env("SERVER_USER", "druguser")
SERVER_PASSWORD = _env("SERVER_PASSWORD", "Drug@123456")

# --------------------------------------------------------------------
# B. 本机 MySQL（开发环境；服务器连不上时自动回退到这里）
# --------------------------------------------------------------------
LOCAL_HOST = _env("LOCAL_HOST", "127.0.0.1")
LOCAL_USER = _env("LOCAL_USER", "root")
LOCAL_PASSWORD = _env("LOCAL_PASSWORD", "123456")

# 是否允许回退到本机。设为 0 时只认 Linux 服务器，连不上就直接报错。
ALLOW_LOCAL_FALLBACK = _env("ALLOW_LOCAL_FALLBACK", "1") == "1"

# --------------------------------------------------------------------
# 当前生效的连接参数（netprobe 探测成功后就地改写这三个值）
# --------------------------------------------------------------------
DB_HOST = _env("DB_HOST", SERVER_HOST)
DB_USER = _env("DB_USER", SERVER_USER)
DB_PASSWORD = _env("DB_PASSWORD", SERVER_PASSWORD)

DB_HOST_FROM_ENV = bool(os.environ.get("DB_HOST"))     # 是否由环境变量显式指定
AUTO_DISCOVER = _env("AUTO_DISCOVER", "1") == "1"      # 是否允许自动扫描发现服务器

DB_PORT = int(_env("DB_PORT", "3306"))
DB_NAME = _env("DB_NAME", "drug_db")
DB_CHARSET = "utf8mb4"

# 连接超时（秒），网络不通时快速失败，避免页面长时间卡住
DB_CONNECT_TIMEOUT = int(_env("DB_CONNECT_TIMEOUT", "5"))

# 当前实际连的是哪一套： "server" = Linux 服务器，"local" = 本机 MySQL
# 由 netprobe 探测成功后写入；探测前保持 "server"（默认优先级）
ACTIVE_PROFILE = "server"

# --------------------------------------------------------------------
# Web 服务（客户端：本机 Flask，浏览器访问）
# --------------------------------------------------------------------
WEB_HOST = _env("WEB_HOST", "127.0.0.1")
WEB_PORT = int(_env("WEB_PORT", "5000"))
WEB_DEBUG = _env("WEB_DEBUG", "0") == "1"

APP_TITLE = "药品进销存管理系统"
APP_VERSION = "2.0"
