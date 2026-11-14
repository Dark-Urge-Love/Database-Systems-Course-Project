# -*- coding: utf-8 -*-
#  ↑ 编码声明：本文件用 UTF-8，避免中文乱码

"""
启动入口（客户端）
------------------------------------------------------------------
B/S 结构里的"Web 服务器"就是本文件：python run.py 启动 Flask，
浏览器访问 http://127.0.0.1:5000；数据访问通过网络发到
Ubuntu 服务器上的 MySQL 8.0（B/S 结构知识点详见 webapp/config.py 注释）。

用法：
    pip install -r requirements.txt
    python run.py
服务器 IP 变了（WSL 重启后常见）：
    set DB_HOST=新IP && python run.py
"""

# 从自己写的 webapp 包里导入：
#   create_app —— 工厂函数，负责"造"出一个 Flask 应用对象
#   config     —— 配置模块（数据库地址、端口等常量）
from webapp import create_app, config

# 创建 Flask 应用实例
app = create_app()


if __name__ == "__main__":                       # 只有直接运行本文件才往下执行
    # 启动前连一次数据库自检：成功则打印版本和数据目录（Linux 路径即"数据在服务器上"的证据），
    # 失败不影响页面启动，只打印一句原因
    try:
        from webapp import dao                   # 数据访问层（专门放 SQL 的模块）
        info = dao.server_info()                 # 真的去敲一下数据库的门
        print("数据库连接成功：MySQL %s，数据目录 %s" % (info["version"], info["datadir"]))
    except Exception as e:
        print("数据库连接失败：%s（确认 Ubuntu/MySQL 已启动；IP 变了用 set DB_HOST=新IP）" % e)

    # 启动 Flask 开发服务器：
    #   host  —— 127.0.0.1 只有本机能访问；0.0.0.0 局域网内其他设备也能访问
    #   port  —— 浏览器地址栏里的 :5000
    #   debug —— 代码改动后自动重启（生产环境要关）
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=config.WEB_DEBUG)
