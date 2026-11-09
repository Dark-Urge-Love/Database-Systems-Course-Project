# -*- coding: utf-8 -*-
"""
配置模块
------------------------------------------------------------------
所有配置都可以用环境变量覆盖，方便在不同机器上跑而不用改代码。

如果 Ubuntu(WSL) 重启后 IP 变了，只要改下面 DB_HOST 的默认值，
或者启动前设置环境变量：
    set DB_HOST=192.168.150.181  &&  python run.py
（在 Ubuntu 里执行 `hostname -I` 可以查看当前 IP）
"""
import os


def _env(key, default):
    """读环境变量，为空则用默认值。"""
    value = os.environ.get(key)
    return value if value not in (None, "") else default


# --------------------------------------------------------------------
# 数据库（服务器端：Ubuntu 24.04 + MySQL 8.0，通过 TCP/IP 访问）
# --------------------------------------------------------------------
# 说明：Ubuntu(WSL) 每次重启后虚拟网卡 IP 会重新分配，所以这里给出的是
# "上次用过的地址"。启动时 netprobe 会自动探测并覆盖它（见 webapp/netprobe.py）。
# 如果你希望锁定某个地址、不让程序自动探测，就用环境变量显式指定：
#     set DB_HOST=192.168.150.181 && python run.py
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
