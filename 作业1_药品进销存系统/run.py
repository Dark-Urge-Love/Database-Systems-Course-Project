# -*- coding: utf-8 -*-
"""
启动入口（客户端）
------------------------------------------------------------------
课程要求的是 B/S 结构：本文件跑在 Windows（客户端），
浏览器访问 http://127.0.0.1:5000，数据访问全部通过网络发到
Ubuntu 服务器上的 MySQL 8.0。

用法：
    pip install -r requirements.txt
    python run.py
切换数据库服务器（例如 Ubuntu 重启后 IP 变了）：
    set DB_HOST=192.168.150.181 && python run.py
"""
import sys

from webapp import create_app, config


def main():
    app = create_app()

    # ---------- 启动前自动定位数据库服务器 ----------
    from webapp import netprobe
    netprobe.ensure_server(config, force=False, verbose=True)

    print("=" * 62)
    print(" %s  v%s" % (config.APP_TITLE, config.APP_VERSION))
    print("=" * 62)
    print(" 浏览器访问   : http://%s:%d" % (
        "127.0.0.1" if config.WEB_HOST in ("0.0.0.0", "127.0.0.1") else config.WEB_HOST,
        config.WEB_PORT))
    print(" 数据库服务器 : %s:%d  (库 %s / 用户 %s)" % (
        config.DB_HOST, config.DB_PORT, config.DB_NAME, config.DB_USER))
    print("=" * 62)

    # 再自检一次，连不上给出明确提示，但页面照常启动（页面顶部会显示红色告警）
    try:
        from webapp import dao
        info = dao.server_info()
        print(" [自检] 数据库连接成功：MySQL %s @ %s" % (info["version"], info["hostname"]))
    except Exception as e:                                     # noqa: BLE001
        print(" [自检] 数据库连接失败：%s" % e)
        print("        常见原因：")
        print("          1) Ubuntu 没在运行 —— 打开开始菜单里的 Ubuntu 图标即可")
        print("          2) MySQL 没启动 —— 在 Ubuntu 里执行： sudo service mysql start")
        print("          3) 地址不对 —— 在 Ubuntu 里执行 hostname -I 查看 IP，")
        print("             然后用 set DB_HOST=那个IP && python run.py 启动")
        print("        （服务仍会启动，页面顶部会显示告警，修好后刷新即可）")
    print("=" * 62)

    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=config.WEB_DEBUG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
