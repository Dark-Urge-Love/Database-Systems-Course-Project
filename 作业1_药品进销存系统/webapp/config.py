# -*- coding: utf-8 -*-
"""
配置模块
------------------------------------------------------------------
客户端只连一台数据库：**Linux 服务器上的 MySQL 8.0**（课程硬性要求）。
本模块不提供、也不允许"连本机 MySQL"的退路 —— 连不上就明确报错，
避免出现"看起来能跑、其实数据在 Windows 上"的情况。

服务器地址（Ubuntu 每次重启后虚拟网卡 IP 会变）由 webapp/netprobe.py
自动探测：wsl_ip.txt → 上次成功的缓存 → 配置里的默认值 → 扫描虚拟网段。

所有配置都可以用环境变量覆盖：
    set DB_HOST=192.168.150.181 && python run.py     # 锁定服务器地址
    set DB_USER=druguser DB_PASSWORD=Drug@123456 && python run.py
"""
import os


def _env(key, default):
    """读环境变量，为空则用默认值。"""
    value = os.environ.get(key)
    return value if value not in (None, "") else default


# --------------------------------------------------------------------
# 数据库（服务器端：Ubuntu + MySQL 8.0，通过 TCP/IP 访问）
# --------------------------------------------------------------------
# 这里的地址是"上次用过的"，启动时 netprobe 会自动探测并覆盖它。
# 若服务器上还没有这个账号，在 Ubuntu 里执行 deploy/init_server_db.sh。
DB_HOST = _env("DB_HOST", "192.168.150.181")
DB_HOST_FROM_ENV = bool(os.environ.get("DB_HOST"))     # 是否由环境变量显式指定
AUTO_DISCOVER = _env("AUTO_DISCOVER", "1") == "1"      # 是否允许自动扫描发现服务器

DB_PORT = int(_env("DB_PORT", "3306"))
DB_USER = _env("DB_USER", "druguser")
DB_PASSWORD = _env("DB_PASSWORD", "Drug@123456")
DB_NAME = _env("DB_NAME", "drug_db")
DB_CHARSET = "utf8mb4"

# 连接超时（秒），网络不通时快速失败，避免页面长时间卡住
DB_CONNECT_TIMEOUT = int(_env("DB_CONNECT_TIMEOUT", "5"))

# --------------------------------------------------------------------
# Web 服务（客户端：本机 Flask，浏览器访问）
# --------------------------------------------------------------------
WEB_HOST = _env("WEB_HOST", "127.0.0.1")
WEB_PORT = int(_env("WEB_PORT", "5000"))
WEB_DEBUG = _env("WEB_DEBUG", "0") == "1"

APP_TITLE = "药品进销存管理系统"
APP_VERSION = "2.0"
