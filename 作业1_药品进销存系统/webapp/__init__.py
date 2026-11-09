# -*- coding: utf-8 -*-
"""
应用工厂
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
"""
from flask import Flask

from . import config
from .views import bp


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_AS_ASCII"] = False          # 让接口直接返回中文
    app.config["SECRET_KEY"] = "drug-inventory-demo"
    app.register_blueprint(bp)
    return app
