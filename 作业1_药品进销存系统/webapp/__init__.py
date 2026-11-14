# -*- coding: utf-8 -*-

"""
应用工厂（application factory）
------------------------------------------------------------------
分层结构：
    webapp/
      config.py    配置（数据库地址、端口）
      db.py        连接与事务底层
      dao.py       所有 SQL
      services.py  业务事务（采购入库 / 销售出库 / 并发出库）
      views.py     页面路由与 Ajax 接口
      templates/   Jinja2 页面
      static/      样式与脚本

【知识点：为什么要"工厂函数"】
不是"一 import 就创建 app"，而是写一个 create_app() 函数，谁来要、谁调用它造一个。
好处：测试时可以造多个互不干扰的实例；也能避免模块之间的循环导入。
"""

from flask import Flask

from . import config

# 从 views 模块导入名叫 bp 的对象。
# bp 是 Blueprint（蓝图）实例，可以理解为"一组路由的集合"，
# 把路由写进蓝图、再注册到 app 上，这样 views.py 再大也不会和入口文件搅在一起
from .views import bp


# 定义工厂函数：调用一次，返回一个配置好的 Flask 应用
def create_app():

    # 【知识点：创建 Flask 应用实例】
    # 第一个参数 __name__ 让 Flask 知道"当前模块在哪"，从而能正确找到 templates/static 目录
    # static_folder     —— 静态资源（css/js/图片）的目录名
    # template_folder   —— 页面模板（.html）的目录名
    app = Flask(__name__, static_folder="static", template_folder="templates")

    # 关掉 JSON 的中文转义。默认 Flask 会把中文转成 \u4e2d\u6587 这种形式，
    # 设为 False 后接口直接返回可读的中文，方便调试时肉眼看
    app.config["JSON_AS_ASCII"] = False          # 让接口直接返回中文

    # 【知识点：SECRET_KEY】
    # Flask 用这个密钥来签名 session（登录态、提示消息等）。这里只是课程演示，
    # 所以写死一个简单字符串；真实项目必须用随机长密钥，且不能写进代码库
    app.config["SECRET_KEY"] = "drug-inventory-demo"

    # 把蓝图注册到应用上：这一步执行后，views.py 里定义的所有网址才真正生效
    app.register_blueprint(bp)

    # 把"造好的"应用交出去。run.py 拿到它后调用 app.run() 启动服务
    return app
