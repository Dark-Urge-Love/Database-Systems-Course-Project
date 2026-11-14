# -*- coding: utf-8 -*-

"""
配置模块
------------------------------------------------------------------
客户端只连一台数据库：**Linux 服务器上的 MySQL 8.0**（课程硬性要求）。
本模块不提供、也不允许"连本机 MySQL"的退路 —— 连不上就明确报错，
避免出现"看起来能跑、其实数据在 Windows 上"的情况。

服务器地址按这个顺序确定（谁在前用谁）：
    ① 环境变量 DB_HOST（手工锁定，优先级最高）
    ② 项目根目录的 wsl_ip.txt —— 启动客户端.bat 每次启动都会把
       Ubuntu 的最新 IP 写进这个文件，所以重启后 IP 变了也不用手改代码
    ③ 代码里的默认 IP（兜底）

所有配置都可以用环境变量覆盖：
    set DB_HOST=192.168.150.181 && python run.py     # 锁定服务器地址
    set DB_USER=druguser DB_PASSWORD=Drug@123456 && python run.py

【知识点：为什么不把配置写死成常量就完事】
写死 = 换个环境就要改代码。用环境变量（os.environ）的好处是：
同一份代码，不改一行，就能通过 `set DB_HOST=xxx` 临时指定不同的服务器。
这是"配置与代码分离"的基本做法。
"""

# os 是标准库，这里用它的 os.environ（环境变量字典）来读外部传入的配置
import os


# 定义一个小工具函数：读环境变量，没设置或设置为空串时返回默认值
def _env(key, default):
    """读环境变量，为空则用默认值。"""
    # os.environ.get(key) 取环境变量的值；键不存在时返回 None，不会报错
    value = os.environ.get(key)

    # 【知识点：三元表达式】"A if 条件 else B"：条件成立取 A，否则取 B
    # 这里的意思是：如果取到的值既不是 None 也不是空字符串，就用它；否则用默认值
    return value if value not in (None, "") else default


# --------------------------------------------------------------------
# 数据库（服务器端：Ubuntu + MySQL 8.0，通过 TCP/IP 访问）
# --------------------------------------------------------------------
# 【知识点：os.path 一家子】
#   os.path.abspath(__file__)  把"当前这个文件"变成绝对路径
#   os.path.dirname(...)       取它所在的文件夹（第一次得到 webapp/，第二次得到项目根目录）
#   os.path.join(...)          把目录和文件名拼成跨系统通用的路径（Windows/Linux 都对）
def _default_host():
    """从 wsl_ip.txt 读服务器 IP；读不到就用兜底默认值。"""
    # wsl_ip.txt 在项目根目录：从本文件(config.py)往上两层
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "wsl_ip.txt")

    # 【知识点：try/except OSError】打开文件可能失败（文件不存在等），
    # OSError 是"文件/系统相关错误"的统称，捕获它程序就不会崩
    try:

        # with open(...) 读整个文件；strip() 去掉两端的空白和换行符
        with open(path, encoding="utf-8") as f:
            ip = f.read().strip()

        # 读到了非空内容就把它当默认 IP 返回
        if ip:
            return ip

    except OSError:

        # 第一次运行时 wsl_ip.txt 还不存在，走这里，什么都不做
        pass

    # 兜底默认值：上次部署时 WSL 的 IP
    return "192.168.150.181"

# 主机地址：环境变量 DB_HOST 优先，否则读 wsl_ip.txt，再不行用兜底 IP
DB_HOST = _env("DB_HOST", _default_host())

# 端口：MySQL 默认 3306。int(...) 是因为环境变量读出来永远是字符串，要转成整数
DB_PORT = int(_env("DB_PORT", "3306"))

# 数据库用户名：应用专用账号（不是 root，权限更小更安全）
DB_USER = _env("DB_USER", "druguser")

# 数据库密码（课程演示用，真实项目绝不能明文写在代码里）
DB_PASSWORD = _env("DB_PASSWORD", "Drug@123456")

# 数据库名：本项目所有 13 张表都在这个库下面
DB_NAME = _env("DB_NAME", "drug_db")

# 字符集：utf8mb4 是 MySQL 里真正完整的 UTF-8（能存中文和 emoji），不要用 utf8
DB_CHARSET = "utf8mb4"

# 连接超时（秒），网络不通时快速失败，避免页面长时间卡住
# 【知识点：为什么要设超时】不设超时的话，IP 不通时 TCP 会一直重试，
# 页面就"转圈圈"卡几十秒，体验极差。设 5 秒，失败得快，提示也来得快
DB_CONNECT_TIMEOUT = int(_env("DB_CONNECT_TIMEOUT", "5"))

# --------------------------------------------------------------------
# Web 服务（客户端：本机 Flask，浏览器访问）
# --------------------------------------------------------------------
# 监听地址：127.0.0.1 表示"只有本机能访问"；
# 如果想局域网内其他设备（手机、同学电脑）也能访问，改成 0.0.0.0
WEB_HOST = _env("WEB_HOST", "127.0.0.1")

# Web 端口：浏览器地址栏里的 :5000
WEB_PORT = int(_env("WEB_PORT", "5000"))

# 调试模式：等于 "1" 才开启（自动重载 + 浏览器显示报错详情）
WEB_DEBUG = _env("WEB_DEBUG", "0") == "1"

# 系统标题：页面上显示的名字
APP_TITLE = "药品进销存管理系统"

# 版本号：显示在页面角落和启动日志里
APP_VERSION = "2.0"
