# -*- coding: utf-8 -*-
#  ↑ 编码声明：本文件用 UTF-8，避免中文乱码

"""
Views 层：页面路由与 Ajax 接口
------------------------------------------------------------------
对应课程要求的 B/S 结构：浏览器（客户端）→ Flask（本机）→ 通过 TCP/IP
访问 Linux 服务器上的 MySQL。

【知识点：路由(route) 是什么】
浏览器发来一个网址请求，服务器要决定"这段请求交给哪个函数处理"。
这个"网址 → 函数"的对应关系就叫路由。
@bp.route("/api/drugs") 这句话的意思是：
    谁访问 /api/drugs，就执行下面这个函数，把返回值当响应发回去。

【知识点：Ajax 接口 vs 页面】
  · 页面（/ops）：返回一整张 HTML，浏览器渲染成看得见的界面
  · 接口（/api/xxx）：返回 JSON 数据（一堆 {"键": 值}），由页面里的 JS 拿去用，
    局部刷新，不用整页跳转
"""

# datetime 标准库：处理日期时间（把数据库返回的时间格式化成字符串）
import datetime

# re 标准库：正则表达式，这里用来校验"表名是不是合法标识符"
import re

# time 标准库：perf_counter 掐表测耗时
import time

# uuid 标准库：生成随机唯一字符串（动态建表时当表名后缀，保证不重名）
import uuid

# traceback 标准库：把异常堆栈格式化成文字，方便在接口里回传前几行给出错定位
import traceback

# pymysql：MySQL 驱动。这里主要用它定义的异常类 MySQLError
import pymysql

# 从 Flask 导入本次要用的几个工具：
#   Blueprint       —— 蓝图，一组路由的集合
#   jsonify         —— 把 Python 字典转成 JSON 响应
#   redirect        —— 让浏览器跳转到另一个地址
#   render_template —— 渲染 templates/ 下的 HTML 模板
#   request         —— 封装了"这次请求"的所有信息（参数、表单、JSON 体）
#   url_for         —— 按"函数名"反查它对应的网址（改了路由也不用到处改链接）
from flask import Blueprint, jsonify, redirect, render_template, request, url_for

# 导入本项目自己的三个模块：配置、SQL 层、业务层
from . import config, dao, services

# 导入业务异常和只读游标
from .db import BizError, get_cursor

# 创建蓝图对象。第一个参数 "main" 是蓝图名（url_for 里会用到），
# 第二个 __name__ 让 Flask 知道这个蓝图定义在哪个模块
bp = Blueprint("main", __name__)


# =====================================================================
# 公共：把当前连接信息塞给所有模板
# =====================================================================
def connection_banner():
    # 先把"配置里的连接参数"装进字典
    base = {
        "host": config.DB_HOST,      # 服务器 IP
        "port": config.DB_PORT,      # 端口
        "user": config.DB_USER,      # 账号
        "db": config.DB_NAME,        # 库名
    }

    # 再试着真的连一次数据库，拿到服务器版本和主机名
    try:

        # dao.server_info() 会执行 SELECT VERSION(), @@hostname, @@datadir 等
        info = dao.server_info()

        # dict.update() 把新键值对合并进字典
        base.update({
            "ok": True,                      # 连接成功标记
            "version": info["version"],      # MySQL 版本
            "hostname": info["hostname"],    # 服务器主机名（证明是 Ubuntu）
            "datadir": info["datadir"],      # 数据目录（Linux 路径）
        })

        # 返回装好的字典
        return base

    # 连不上也要让页面能打开，所以捕获异常
    except Exception as e:                                     # noqa: BLE001

        # 标记连接失败，并把错误原因带上，页面顶部就会显示红条告警
        base.update({"ok": False, "error": str(e)})

        # 返回
        return base


# 【知识点：@bp.app_context_processor 上下文处理器】
# 被它装饰的函数，会在"每次渲染模板"之前自动执行，返回值会被塞进模板的全局变量里。
# 这样模板里直接写 {{ conn.host }} 就能用，不用每个 route 都手动传一遍
@bp.app_context_processor
def inject_globals():

    # APP_TITLE 给页面标题用；conn 给页面顶部的"数据库连接"信息卡用
    return {"APP_TITLE": config.APP_TITLE, "conn": connection_banner()}


# 【知识点：@bp.after_request 请求后钩子】
# 每个请求处理完之后都会走这个函数，可以在返回前改响应头
@bp.after_request
def no_cache_api(resp):
    """接口数据永远新鲜：库存、日志这类数字不允许被浏览器缓存。"""

    # 只对 /api/ 开头的接口做处理（页面本身可以缓存）
    if request.path.startswith("/api/"):

        # no-store 告诉浏览器"这个响应别存，下次重新来问"
        resp.headers["Cache-Control"] = "no-store"

    # 必须把响应对象返回，否则请求会卡住
    return resp


# 统一成功响应的格式：{"ok": true, "data": ...}
def _ok(data=None, **extra):

    # 先放一个成功标记
    payload = {"ok": True}

    # 有数据才放进去（None 表示这次没有数据体）
    if data is not None:
        payload["data"] = data

    # 【知识点：**extra 可变关键字参数】调用 _ok(x, need_confirm=True) 时，
    # extra 就是一个 {"need_confirm": True} 的字典，update 到 payload 里
    payload.update(extra)

    # jsonify 把字典转成 JSON 响应对象返回
    return jsonify(payload)


# 统一失败响应的格式
def _err(message, **extra):

    # ok=false + 错误说明
    payload = {"ok": False, "error": message}

    # 附加字段（比如 need_confirm、count）
    payload.update(extra)

    # 【知识点：返回元组】return 响应对象, 200 —— 第二个元素是 HTTP 状态码。
    # 这里故意用 200 而不是 400/500：前端统一按"业务是否成功"判断，
    # 不走 HTTP 错误分支，处理起来更简单
    return jsonify(payload), 200


# 判断"当前请求是不是接口请求"
def _is_api():

    # 路径以 /api/ 开头就是接口，否则是页面
    return request.path.startswith("/api/")


# 【知识点：错误处理器】给整个蓝图注册"遇到某种异常就调用这个函数"
@bp.errorhandler(pymysql.MySQLError)
def handle_mysql_error(e):                                     # noqa: ANN001
    # 拼一条带错误码的提示，e.args[0] 一般是 MySQL 错误码
    msg = "数据库错误（%s）：%s" % (e.args[0] if e.args else "?", str(e))

    # 接口请求 → 返回 JSON
    if _is_api():
        return _err(msg)

    # 页面请求 → 渲染错误页，并用 500 状态码
    return render_template("error.html", message=msg), 500


# 兜底：所有没被上面捕获的异常都走这里
@bp.errorhandler(Exception)
def handle_any_error(e):                                       # noqa: ANN001

    # 延迟导入，避免循环依赖
    from werkzeug.exceptions import HTTPException

    # 404/405 这类是"HTTP 层面的正常异常"，原样返回，不要当成系统错误
    if isinstance(e, HTTPException):
        return e

    # 取出完整的异常堆栈文本
    tb = traceback.format_exc()

    # 接口请求：回传错误 + 堆栈最后 4 行（够定位问题，又不至于泄露太多）
    if _is_api():
        return _err("服务器内部错误：%s" % str(e), trace=tb.splitlines()[-4:])

    # 页面请求：渲染错误页
    return render_template("error.html", message="%s: %s" % (type(e).__name__, e)), 500


# 把数据库里的日期/时间格式化成页面友好的字符串
def _fmt_value(v):

    # 【知识点：isinstance 类型判断】判断 v 是不是 datetime 类型
    if isinstance(v, datetime.datetime):
        # "2026-11-10 15:20" 这种形式
        return v.strftime("%Y-%m-%d %H:%M")

    # date 只有年月日
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")

    # 其他类型（数字、字符串、None）原样返回
    return v


# 把一行的所有字段都格式化一遍
def _fmt_row(row):

    # 【知识点：字典推导式】{键: 值 for 键, 值 in 字典.items()} 一句话生成新字典
    return {k: _fmt_value(v) for k, v in row.items()}


# 把 MySQL 错误码翻译成"人话"
def _friendly(e, sql=None):
    """把常见 MySQL 报错翻译成业务语言（其余原样返回）。"""

    # 取错误码
    code = e.args[0] if e.args else None

    # 【知识点：字典当映射表】错误码 → 中文说明。
    # 比写一长串 if/elif 清爽得多，查起来也快
    msgs = {
        1451: "该记录正被其他业务引用，不能删除。",                       # 外键被引用，删不掉
        1452: "关联的业务数据不存在。",                                   # 插的外键值不存在
        1062: "该记录已存在，请勿重复录入。",                             # 唯一约束冲突
        1050: "该表已存在。",                                             # CREATE TABLE 重名
        1146: "数据表不存在。",                                           # 表被删了
        1406: "内容太长，超出字段允许的长度。",                           # 字段长度不够
        1265: "内容格式与字段类型不匹配。",                               # 类型不对
        1292: "日期格式不正确，请使用 2026-01-31 这样的格式。",           # 日期解析失败
        3819: "数据不满足业务校验规则（如库存不能为负）。",               # CHECK 约束失败
        1045: "数据库拒绝了登录（账号或密码不对）。若连的是 Linux 服务器，"
              "请在 Ubuntu 里执行 deploy/init_server_db.sh 创建 druguser 账号。",
        2003: "连不上数据库服务器：确认 Ubuntu 已启动、MySQL 已运行（dbup），"
              "或服务器 IP 已变化（Ubuntu 里执行 hostname -I 查看）。",
    }

    # dict.get(键, 默认值)：找到对应说明就用它，找不到用默认文案
    msg = msgs.get(code, "数据库错误（%s）：%s" % (code, str(e)))

    # 如果调用方把 SQL 也传进来了，附在后面，方便排查
    if sql:
        msg += "\n执行的 SQL：" + sql

    # 返回最终提示
    return msg


# =====================================================================
# 入口：直接进入业务页
# =====================================================================
# 【知识点：url_for("main.ops_page")】按"蓝图名.函数名"反查网址。
# 这样以后就算把 /ops 改成 /warehouse，这里也不用改
@bp.route("/")
def index():

    # 访问根路径时，让浏览器跳到业务页
    return redirect(url_for("main.ops_page"))


# 业务主页：返回一整张 HTML
@bp.route("/ops")
def ops_page():

    # render_template 会用 Jinja2 引擎把 templates/ops.html 渲染成 HTML
    # （页面里的动态部分由页面里的 JS 再通过下面的 /api/xxx 接口取数据填充）
    return render_template("ops.html")


# =====================================================================
# 基础资料：下拉选项
# =====================================================================
# 页面一打开就调这个接口，把所有下拉框的候选数据一次性拿齐
@bp.route("/api/options")
def api_options():

    # 外面套 try，是因为数据库可能连不上
    try:

        # 一次返回 6 组数据，前端分别填到不同下拉框里
        return _ok({
            "suppliers": dao.list_suppliers(),          # 供应商
            "warehouses": dao.list_warehouses(),        # 仓库
            "buyers": dao.list_employees("采购员"),      # 采购员（按岗位筛）
            "sellers": dao.list_employees("销售员"),     # 销售员
            "drugs": dao.drug_options(),                # 药品 + 库存
            "categories": dao.list_categories(),        # 药品分类
        })

    # 只捕获数据库错误，翻译成人话返回
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 药品目录：INSERT / UPDATE / DELETE
# =====================================================================
# 【知识点：GET 与 POST】GET 用来"取数据"，参数在网址里（?q=阿莫）；
# POST 用来"交数据"，参数在请求体里。下面这个只有 GET，所以是查询接口
@bp.route("/api/drugs")
def api_drugs():

    # request.args 是网址里的查询参数；get("q") 取不到时返回 None；
    # or "" 兜底成空串；.strip() 去掉首尾空格。整套是"防空指针"的常见写法
    q = (request.args.get("q") or "").strip()

    # 查数据库
    try:

        # 有关键字就传，没有就传 None（dao 里会忽略这个条件）
        rows = dao.list_drugs(q or None)

        # 逐行把 Decimal 转成 float、把库存转成 int，
        # 因为 JSON 不认识 Decimal 类型，直接序列化会报错
        for r in rows:
            r["pp"] = float(r["pp"])        # 采购价
            r["rp"] = float(r["rp"])        # 零售价
            r["stock"] = int(r["stock"])    # 总库存

        # 返回行数据 + 总数 + 关键字（前端用来回显）
        return _ok({"rows": rows, "total": len(rows), "keyword": q})

    # 数据库错误统一翻译
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 通用的"解析浮点数字段"工具函数
def _parse_float(body, key, label, required=True, minimum=0.0):

    # 从请求体里取值
    raw = body.get(key)

    # 没填（None 或空串）
    if raw in (None, ""):

        # 必填项就报错
        if required:
            raise BizError("请填写%s" % label)

        # 非必填就返回 None（数据库里存 NULL）
        return None

    # 试着转成浮点数
    try:
        v = float(raw)

    # 转不了（比如填了"abc"）→ 提示填数字
    except (TypeError, ValueError):
        raise BizError("%s请填写数字" % label)

    # 小于下限（默认 0，价格不能为负）→ 报错
    if v < minimum:
        raise BizError("%s不能小于 %g" % (label, minimum))

    # 保留两位小数返回
    return round(v, 2)


# 校验并组装"新增/修改药品"的字段。抽出来是因为新增和修改都要校验同一套规则
def _drug_payload(body):

    # 存放要写库的字段
    data = {}

    # 药品名称：取出来去空格
    name = (body.get("name") or "").strip()

    # 必填校验
    if not name:
        raise BizError("请填写药品名称")

    # 长度校验（对应数据库 VARCHAR(100)）
    if len(name) > 100:
        raise BizError("药品名称不能超过 100 字")

    # 放进字典
    data["drug_name"] = name

    # 分类 ID 转整数
    try:
        cat = int(body.get("cate") or 0)

    # 转不了或者没选
    except (TypeError, ValueError):
        raise BizError("请选择药品分类")

    # 没选分类（0 是兜底值）
    if not cat:
        raise BizError("请选择药品分类")

    # 写进字典
    data["category_id"] = cat

    # 规格，如 "0.25g*24粒"
    spec = (body.get("spec") or "").strip()

    # 必填
    if not spec:
        raise BizError("请填写规格，如 0.25g*24粒")

    # 写进字典
    data["spec"] = spec

    # 单位：没填就默认"盒"
    data["unit"] = (body.get("unit") or "").strip() or "盒"

    # 生产厂家：没填就存 NULL
    data["manufacturer"] = (body.get("maker") or "").strip() or None

    # 采购价、零售价都走通用解析
    pp = _parse_float(body, "pp", "采购价")
    rp = _parse_float(body, "rp", "零售价")

    # 业务规则：零售价不能比采购价低（否则卖一盒亏一盒）
    if rp < pp:
        raise BizError("零售价不能低于采购价")

    # 写进字典
    data["purchase_price"] = pp
    data["retail_price"] = rp

    # 保质期（月）：没填默认 24 个月
    try:
        life = int(body.get("life") or 24)
    except (TypeError, ValueError):
        raise BizError("保质期请填写整数月份")

    # 合理区间校验
    if not 1 <= life <= 120:
        raise BizError("保质期应在 1~120 个月之间")

    # 写进字典
    data["shelf_life_months"] = life

    # 是否处方药：前端勾了就是 1，没勾是 0
    # 【知识点：Python 里"真值"判断】非空字符串、非零数字都算 True
    data["is_rx"] = 1 if body.get("rx") else 0

    # 返回组装好的字段
    return data


# 【知识点：同一个网址 + 不同方法 = 不同函数】
# 上面已经有一个 @bp.route("/api/drugs") 处理 GET（查询），
# 这里再加 methods=["POST"]，就变成"POST /api/drugs = 新增"。
# 这是 REST 风格接口的常见写法：一个资源一个地址，靠方法区分动作
@bp.route("/api/drugs", methods=["POST"])
def api_drug_create():

    # 取请求体里的 JSON。
    # silent=True 表示"不是合法 JSON 就别抛异常，返回 None"，后面 or {} 兜底成空字典
    body = request.get_json(silent=True) or {}

    # 业务处理
    try:

        # 先校验参数
        data = _drug_payload(body)

        # 开一个"要提交"的游标（这里只需单条语句，不必用完整事务）
        with get_cursor(commit=True) as cur:

            # 查有没有同名同规格的药（业务唯一性检查）
            cur.execute("SELECT drug_id, drug_code FROM drug WHERE drug_name = %s "
                        "AND spec = %s LIMIT 1", (data["drug_name"], data["spec"]))

            # 查到了 → 说明重复，报错
            if cur.fetchone():
                raise BizError("药品目录中已有同名同规格的品种，请直接调整原记录的价格或库存")

            # 取当前最大 id + 1，用来生成编码
            cur.execute("SELECT COALESCE(MAX(drug_id), 0) + 1 AS n FROM drug")

            # 取出 n
            n = cur.fetchone()["n"]

            # 生成药品编码，如 YP000013
            # %06d 表示"至少 6 位，不够前面补 0"
            code = "YP%06d" % n

            # 【知识点：拼装 INSERT 的列名】把字典的键当列名、值当参数。
            # 注意：列名是代码里写死的字段名（不是用户输入），值是 %s 占位后传参，
            # 所以既灵活又没有 SQL 注入风险
            cols = ["drug_code"] + list(data.keys())

            # 对应的值列表
            vals = [code] + [data[k] for k in data]

            # 拼出形如 INSERT INTO drug (a, b, c) VALUES (%s, %s, %s)
            # ", ".join(...) 把列表用逗号连成一个字符串
            # ["%s"] * len(cols) 生成与列数相同的占位符个数
            sql = ("INSERT INTO drug (%s) VALUES (%s)"
                   % (", ".join(cols), ", ".join(["%s"] * len(cols))))

            # 执行，参数单独传（驱动负责转义）
            cur.execute(sql, vals)

        # 返回成功信息（附带 SQL 字符串，页面上会展示"实际执行的 SQL"）
        return _ok({"message": "已新增药品：%s（编码 %s）" % (data["drug_name"], code),
                    "sql": sql})

    # 业务校验失败
    except BizError as e:
        return _err(str(e))

    # 数据库错误
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 修改药品：网址里带药品 id
# 【知识点：<int:drug_id> 路径参数】访问 /api/drugs/42 时，
# Flask 会把 42 转成整数塞进函数参数 drug_id
@bp.route("/api/drugs/<int:drug_id>", methods=["POST"])
def api_drug_update(drug_id):

    # 取 JSON 体
    body = request.get_json(silent=True) or {}

    try:

        # 校验并组装字段
        data = _drug_payload(body)

        # 是否停用：勾了就是 1（在售），没勾就是 0（停用）
        data["status"] = 1 if body.get("status") else 0

        # 取出所有要更新的列名
        cols = list(data.keys())

        # 拼 UPDATE 语句：UPDATE drug SET drug_name = %s, spec = %s ... WHERE drug_id = %s
        # 注意 %%s 是"转义后的 %s"：因为这里用的是 % 格式化，写 %s 会被当成占位符吃掉，
        # 所以要用 %% 表示"一个真正的百分号"，拼完才能得到 SQL 需要的 %s
        sql = ("UPDATE drug SET %s WHERE drug_id = %%s"
               % ", ".join("%s = %%s" % c for c in cols))

        # 开游标（要提交）
        with get_cursor(commit=True) as cur:

            # 先确认这条记录还在（可能被别人删了）
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))

            # 不存在就报错
            if not cur.fetchone():
                raise BizError("该药品已不存在，请刷新后重试")

            # 执行更新：参数顺序必须和 SQL 里的占位符一一对应——
            # 先是 SET 的各列值，最后是 WHERE 的 drug_id
            cur.execute(sql, [data[c] for c in cols] + [drug_id])

        # 返回成功
        return _ok({"message": "已保存：%s" % data["drug_name"], "sql": sql})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 删除药品：注意这里不是直接删，而是先检查有没有被业务引用
@bp.route("/api/drugs/<int:drug_id>/remove", methods=["POST"])
def api_drug_delete(drug_id):

    try:

        # 开游标（要提交）
        with get_cursor(commit=True) as cur:

            # 先查这条药品
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))

            # 取一行
            row = cur.fetchone()

            # 不存在
            if not row:
                raise BizError("该药品已不存在，请刷新后重试")

            # 收集"被哪些业务数据引用"的说明，用于给出友好提示
            refs = []

            # 查采购明细里引用了多少条
            cur.execute("SELECT COUNT(*) AS c FROM purchase_item WHERE drug_id = %s", (drug_id,))

            # 取数量
            n = cur.fetchone()["c"]

            # 有引用就记一笔
            if n:
                refs.append("采购明细 %d 条" % n)

            # 查销售明细
            cur.execute("SELECT COUNT(*) AS c FROM sale_item WHERE drug_id = %s", (drug_id,))

            # 取数量
            n = cur.fetchone()["c"]

            # 有引用就记一笔
            if n:
                refs.append("销售明细 %d 条" % n)

            # 查当前库存总数
            cur.execute("SELECT COALESCE(SUM(quantity),0) AS q FROM stock WHERE drug_id = %s",
                        (drug_id,))

            # 取数量
            n = cur.fetchone()["q"]

            # 还有库存就不能删
            if n:
                refs.append("库存结存 %d" % n)

            # 再查所有"动态业务表单"里有没有这个药的数据
            # 【知识点：动态表名】表名不能参数化，所以用 %s 拼接，但 t 的值来自
            # _form_tables() 查 information_schema 得到的真实表名，且都带 bizform_ 前缀，
            # 还经过 _check_ident 正则校验 —— 属于"可信来源"，不是用户随便传的
            for t in _form_tables(cur):

                # 查这张表单里该药品的记录数
                cur.execute("SELECT COUNT(*) AS c FROM `%s` WHERE drug_id = %%s" % t, (drug_id,))

                # 取数量
                n = cur.fetchone()["c"]

                # 有记录就记一笔（顺便把表单的中文名也带出来）
                if n:
                    refs.append("表单「%s」%d 条" % (_form_meta(cur, t)["name"], n))

            # 只要有任何引用，就拒绝删除，并建议改成"停用"
            if refs:
                raise BizError("「%s」已有业务数据（%s），不能删除。"
                               "如不再经营，请在编辑中把状态改为停用。"
                               % (row["drug_name"], "、".join(refs)))

            # 确认无人引用，才真正删除
            cur.execute("DELETE FROM drug WHERE drug_id = %s", (drug_id,))

        # 返回成功
        return _ok({"message": "已从药品目录删除：%s" % row["drug_name"],
                    "sql": "DELETE FROM drug WHERE drug_id = %s" % drug_id})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 采购入库 / 销售出库
# =====================================================================
@bp.route("/api/purchases")
def api_purchases():

    try:

        # 查最近 15 张采购单，并逐行格式化日期
        rows = [_fmt_row(r) for r in dao.recent_purchases(15)]

        # 返回
        return _ok({"rows": rows})

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 采购入库：真正干活的是 services.receive_purchase（那里有完整的 6 表事务）
@bp.route("/api/purchases", methods=["POST"])
def api_purchase_create():

    # 取 JSON 体
    body = request.get_json(silent=True) or {}

    try:

        # 逐个字段取出并转成整数。or 0 是为了"没传时得到 0"，便于下面统一校验
        supplier_id = int(body.get("supplier_id") or 0)
        warehouse_id = int(body.get("warehouse_id") or 0)
        employee_id = int(body.get("employee_id") or 0)
        drug_id = int(body.get("drug_id") or 0)

        # 数量
        qty = int(body.get("qty") or 0)

        # 四个必选项：任何一个为 0 都算没选
        if not (supplier_id and warehouse_id and employee_id and drug_id):
            raise BizError("请选择供应商、入库仓库、采购员和药品")

        # 数量必须为正
        if qty <= 0:
            raise BizError("采购数量必须大于 0")

        # 单价走通用解析
        unit_price = _parse_float(body, "unit_price", "采购单价")

        # 批号：没填就交给 services 自动生成（None）
        batch_no = (body.get("batch_no") or "").strip() or None

        # 生产日期
        prod_date = (body.get("prod_date") or "").strip() or None

        # 有效期
        expiry_date = (body.get("expiry_date") or "").strip() or None

        # 有效期必填（药品必须有）
        if not expiry_date:
            raise BizError("请填写有效期至")

        # 【知识点：字符串比较日期】"2027-01-01" <= "2026-06-01" 这种比较是按字符逐位比，
        # 只要格式统一是 YYYY-MM-DD，比较结果就等价于时间先后，所以这里可以直接比字符串
        if prod_date and expiry_date <= prod_date:
            raise BizError("有效期必须晚于生产日期")

        # 调业务层执行"6 张表同一事务"的入库
        info = services.receive_purchase(supplier_id, warehouse_id, employee_id,
                                         drug_id, qty, unit_price,
                                         batch_no, prod_date, expiry_date)

        # 下面这段是"给页面展示用的示意 SQL"，把刚才做的事情用 SQL 复述一遍。
        # 它只是字符串拼接，不执行 —— 目的是答辩/演示时能直观看到做了哪些语句
        info["sql"] = (
            "START TRANSACTION;\n"
            "INSERT INTO purchase_order (po_no, supplier_id, warehouse_id, employee_id, "
            "order_date, total_amount, status) VALUES ('%s', %d, %d, %d, CURDATE(), %.2f, '已入库');\n"
            "INSERT INTO purchase_item (po_id, drug_id, quantity, unit_price, amount, batch_no, "
            "production_date, expiry_date) VALUES (LAST_INSERT_ID(), %d, %d, %.2f, %.2f, '%s', ...);\n"
            "INSERT INTO stock_batch (purchase_item_id, drug_id, warehouse_id, batch_no, "
            "expiry_date, quantity, purchase_price, in_date) VALUES (LAST_INSERT_ID(), ...);\n"
            "UPDATE stock SET quantity = quantity + %d WHERE drug_id = %d AND warehouse_id = %d;\n"
            "INSERT INTO stock_movement (drug_id, warehouse_id, move_type, ref_no, change_qty, "
            "before_qty, after_qty, batch_no, employee_id) VALUES (%d, %d, '采购入库', '%s', %d, %d, %d, '%s', %d);\n"
            "COMMIT;"
            % (info["po_no"], supplier_id, warehouse_id, employee_id, info["amount"],
               drug_id, qty, unit_price, info["amount"], info["batch_no"],
               qty, drug_id, warehouse_id, drug_id, warehouse_id, info["po_no"],
               qty, info["stock_before"], info["stock_after"], info["batch_no"], employee_id))

        # 返回结果（含单号、批号、库存前后对比）
        return _ok(info)

    except BizError as e:
        return _err(str(e))

    # int() 转换失败会抛这两种异常
    except (TypeError, ValueError):
        return _err("参数格式不正确")

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/sales")
def api_sales():

    try:

        # 最近 15 张销售单
        rows = [_fmt_row(r) for r in dao.recent_sales(15)]

        return _ok({"rows": rows})

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 销售出库：真正干活的是 services.sell_out（行锁 + 近效期先出 + 5 表事务）
@bp.route("/api/sales", methods=["POST"])
def api_sale_create():

    # 取 JSON 体
    body = request.get_json(silent=True) or {}

    try:

        # 取各字段并转整数
        warehouse_id = int(body.get("warehouse_id") or 0)
        employee_id = int(body.get("employee_id") or 0)
        drug_id = int(body.get("drug_id") or 0)
        qty = int(body.get("qty") or 0)

        # 客户名称
        customer = (body.get("customer_name") or "").strip()

        # 客户类型：没填默认"连锁药店"
        ctype = (body.get("customer_type") or "连锁药店").strip()

        # 必选项校验
        if not (warehouse_id and employee_id and drug_id):
            raise BizError("请选择出库仓库、销售员和药品")

        # 数量必须为正
        if qty <= 0:
            raise BizError("出库数量必须大于 0")

        # 客户必填
        if not customer:
            raise BizError("请填写客户名称")

        # 调业务层执行出库
        info = services.sell_out(warehouse_id, employee_id, drug_id, qty, customer, ctype)

        # 反推单价（总额 ÷ 数量），只为在页面上展示
        unit_price = round(info["amount"] / qty, 2) if qty else 0

        # 给页面展示用的示意 SQL
        info["sql"] = (
            "START TRANSACTION;\n"
            "SELECT quantity FROM stock WHERE drug_id = %d AND warehouse_id = %d FOR UPDATE;\n"
            "INSERT INTO sale_order (so_no, customer_name, customer_type, warehouse_id, "
            "employee_id, sale_date, total_amount, status) VALUES ('%s', '%s', '%s', %d, %d, CURDATE(), %.2f, '已出库');\n"
            "INSERT INTO sale_item (so_id, drug_id, quantity, unit_price, amount) "
            "VALUES (LAST_INSERT_ID(), %d, %d, %.2f, %.2f);\n"
            "UPDATE stock_batch SET quantity = quantity - %d WHERE drug_id = %d AND warehouse_id = %d ...;\n"
            "UPDATE stock SET quantity = quantity - %d WHERE drug_id = %d AND warehouse_id = %d;\n"
            "INSERT INTO stock_movement (..., move_type, ref_no, change_qty, before_qty, after_qty) "
            "VALUES (%d, ..., '销售出库', '%s', %d, %d, %d);\n"
            "COMMIT;"
            % (drug_id, warehouse_id, info["so_no"], customer, ctype, warehouse_id, employee_id,
               info["amount"], drug_id, qty, unit_price,
               info["amount"], qty, drug_id, warehouse_id, qty, drug_id, warehouse_id,
               drug_id, info["so_no"], -qty, info["stock_before"], info["stock_after"]))

        # 返回
        return _ok(info)

    except BizError as e:
        return _err(str(e))
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 库存台账
# =====================================================================
@bp.route("/api/stock")
def api_stock():

    try:

        # 取前 80 行库存台账
        rows = [_fmt_row(r) for r in dao.stock_table(80)]

        return _ok({"rows": rows})

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 业务表单：新建表单（CREATE TABLE）/ 登记（INSERT）/ 删除表单（DROP TABLE）
# 每张表单都带 drug_id 外键指向药品目录，登记时先选药，表单永远是药品的子表。
#
# 【知识点：动态 DDL】"新建表单 = 建表"是课程要求里的"创建数据表"。
# 用户在前端配几个字段，后端就拼出一条 CREATE TABLE 执行 —— 表没有提前建好，
# 是运行时建出来的。这是 DD L（数据定义语言）与 DML（增删改查）的区分点
# =====================================================================

# 正则：只允许"字母或下划线开头，后续字母/数字/下划线，总长 1~64"。
# 用它挡住带引号、带分号、带空格的表名，防止 SQL 注入
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# 动态表单统一用这个前缀，便于区分"业务表"和"业务表单"
_FORM_PREFIX = "bizform_"

# 13 张核心业务表的名字，用来防止误删/误改这些表
CORE_TABLES = {
    "drug_category", "supplier", "warehouse", "employee", "drug",
    "purchase_order", "purchase_item", "stock", "stock_batch",
    "sale_order", "sale_item", "stock_movement", "access_log",
}

# 表单字段可选的 5 种数据类型：键是前端传的类型名，值 = (中文名, MySQL 类型)
FORM_TYPES = {
    "text":  ("文本", "VARCHAR(100)"),
    "long":  ("长文本", "VARCHAR(255)"),
    "int":   ("整数", "INT"),
    "money": ("金额", "DECIMAL(10,2)"),
    "date":  ("日期", "DATE"),
}

# 预置的 4 套表单模板（用户点一下就能建出对应的表）
# 【知识点：列表套字典】这是"数据驱动界面"的常见写法：
# 前端拿到这个结构，就能自动渲染出表单设计器，不用为每套模板写一遍页面
FORM_TEMPLATES = [
    {"key": "recall", "name": "药品召回登记",
     "note": "厂家召回或质量问题批次的下架处理",
     "fields": [
         {"label": "召回原因", "type": "long", "required": True},
         {"label": "召回日期", "type": "date", "required": True},
         {"label": "涉及批号", "type": "text"},
         {"label": "处理结果", "type": "text"},
     ]},
    {"key": "check", "name": "库存盘点差异",
     "note": "账面数量与实盘数量对照，核对损耗",
     "fields": [
         {"label": "盘点日期", "type": "date", "required": True},
         {"label": "账面数量", "type": "int", "required": True},
         {"label": "实盘数量", "type": "int", "required": True},
         {"label": "差异原因", "type": "text"},
     ]},
    {"key": "price", "name": "药品调价记录",
     "note": "每次调价留痕，方便追溯价格变动",
     "fields": [
         {"label": "调价日期", "type": "date", "required": True},
         {"label": "原零售价", "type": "money", "required": True},
         {"label": "新零售价", "type": "money", "required": True},
         {"label": "审批人", "type": "text"},
     ]},
    {"key": "cold", "name": "冷链温控记录",
     "note": "需冷藏药品的每日温度登记",
     "fields": [
         {"label": "记录日期", "type": "date", "required": True},
         {"label": "温度（℃）", "type": "money", "required": True},
         {"label": "记录时段", "type": "text"},
         {"label": "是否超标", "type": "text"},
     ]},
]


# 校验表名是否合法（防注入的第一道门）
def _check_ident(name):

    # 空名字 或 不匹配正则 → 一律拒绝
    if not name or not _IDENT.match(name):
        raise BizError("名称不合法")


# 读取一张动态表单的"业务定义"：表单名、字段名、字段类型
# 巧妙之处：这些都从数据库的"注释"里读 —— 建表时把中文名写进 COMMENT，
# 之后就能反查出来，不用另建一张元数据表
def _form_meta(cur, table):
    """读取表单的业务定义：表单名与字段名都来自数据表的注释。"""

    # 先校验表名
    _check_ident(table)

    # 必须是我们自己建的表单（带 bizform_ 前缀），防止拿核心表名来查
    if not table.startswith(_FORM_PREFIX):
        raise BizError("该表单不存在")

    # 【知识点：information_schema 元数据库】
    # 它是 MySQL 自带的"数据库的数据库"，里面记录了所有表名、字段名、注释。
    # 查它就等于"读数据库自己的目录"，比 SHOW TABLES 更好用（能带条件、能参与 JOIN）
    cur.execute(
        "SELECT table_comment FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,))

    # 取表头信息（含表注释 = 表单中文名）
    head = cur.fetchone()

    # 表不存在
    if not head:
        raise BizError("该表单不存在")

    # 查所有字段
    cur.execute(
        "SELECT column_name, column_type, column_comment, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s "
        # 按字段在建表时的顺序排（否则字段顺序会乱）
        "ORDER BY ordinal_position", (table,))

    # 收集字段信息
    fields = []

    # 逐个字段处理
    for r in cur.fetchall():

        # 【知识点：为什么要写两种写法】MySQL 返回的列名大小写取决于版本/设置，
        # 有的返回 COLUMN_NAME、有的返回 column_name，所以两种都兜一下
        name = r.get("COLUMN_NAME") or r.get("column_name")

        # 这三个是系统字段（主键、药品外键、创建时间），不需要用户填，跳过
        if name in ("id", "drug_id", "created_at"):
            continue

        # 组装一个字段描述
        fields.append({
            "key": name,                                                      # 数据库字段名 f1/f2...
            "label": (r.get("COLUMN_COMMENT") or r.get("column_comment")) or name,  # 中文名
            "type": (r.get("COLUMN_TYPE") or r.get("column_type") or "").upper(),   # 数据类型
            # is_nullable 为 "NO" 说明建表时写了 NOT NULL，也就是"必填"
            "required": (r.get("IS_NULLABLE") or r.get("is_nullable")) == "NO",
        })

    # 返回这张表单的完整定义
    return {"table": table, "name": head.get("TABLE_COMMENT") or head.get("table_comment"),
            "fields": fields}


# 列出所有动态表单的表名
def _form_tables(cur):

    # LIKE 'bizform\_%'：\_ 是转义的下划线，表示"真正的下划线字符"，
    # 如果不转义，_ 在 LIKE 里代表"任意一个字符"，就会误匹配别的表
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name LIKE 'bizform\\_%' "
        # 按建表时间倒序，新建的表单排前面
        "ORDER BY create_time DESC")

    # 取出表名列表
    return [r.get("TABLE_NAME") or r.get("table_name") for r in cur.fetchall()]


# 查所有表单 + 每个表单的记录数 + 模板定义（页面初始化时调）
@bp.route("/api/forms")
def api_forms():

    try:

        # 只读查询
        with get_cursor() as cur:

            # 结果列表
            items = []

            # 遍历所有动态表单
            for t in _form_tables(cur):

                # 取表单定义
                meta = _form_meta(cur, t)

                # 统计这张表的记录数、涉及多少种药品
                cur.execute("SELECT COUNT(*) AS c, COUNT(DISTINCT drug_id) AS d "
                            "FROM `%s`" % t)

                # 取一行
                row = cur.fetchone()

                # 记录数
                meta["count"] = row["c"]

                # 涉及药品数
                meta["drug_count"] = row["d"]

                # 加入结果列表
                items.append(meta)

            # 不设 LIMIT：药品 1200+ 种，截断会让新登记的药在「表单记录」的
            # 药品下拉里彻底消失（前端带关键字筛选，不需要 SQL 层限流）
            cur.execute("SELECT drug_id AS id, drug_name AS name, spec, unit "
                        "FROM drug WHERE status = 1 ORDER BY drug_name")

            # 药品候选列表
            drugs = cur.fetchall()

        # 一次性返回：表单列表 + 药品下拉 + 模板定义 + 字段类型选项
        # 【知识点：列表推导式遍历字典】FORM_TYPES.items() 给出 (键, 值)，
        # 这里只要键和值里的第一个元素（中文名）
        return _ok({"items": items, "drugs": drugs, "templates": FORM_TEMPLATES,
                    "types": [{"key": k, "label": v[0]} for k, v in FORM_TYPES.items()]})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 新建业务表单 —— 也就是"动态建表"（对应课程的"创建数据表"要求）
@bp.route("/api/forms", methods=["POST"])
def api_form_create():

    # 取 JSON
    body = request.get_json(silent=True) or {}

    # 表单名
    name = (body.get("name") or "").strip()

    # 必填
    if not name:
        return _err("请填写表单名称")

    # 长度限制（表注释最多 2048 字符，但页面显示 30 字比较合适）
    if len(name) > 30:
        return _err("表单名称不能超过 30 字")

    # 用户配置的字段列表
    raw_fields = body.get("fields") or []

    # 字段数量限制在 1~8 个
    if not 1 <= len(raw_fields) <= 8:
        return _err("请设置 1~8 个字段")

    # 收集"列定义 SQL 片段"，如 "f1 VARCHAR(100) NOT NULL COMMENT '召回原因'"
    cols, seen = [], set()

    # 【知识点：enumerate(列表, start=1)】边遍历边拿序号，序号从 1 开始。
    # 这里序号用来生成字段名 f1、f2...
    for i, f in enumerate(raw_fields, start=1):

        # 字段中文名
        label = (f.get("label") or "").strip()

        # 必填
        if not label:
            return _err("第 %d 个字段还没有填写名称" % i)

        # 长度限制
        if len(label) > 20:
            return _err("字段名称不能超过 20 字：%s" % label)

        # 重名校验
        if label in seen:
            return _err("字段名称重复：%s" % label)

        # 记进"已出现"集合
        # 【知识点：set 集合】查找速度比列表快，专门用来判重
        seen.add(label)

        # 查这个类型是否支持
        t = FORM_TYPES.get((f.get("type") or "").strip())

        # 不支持就报错
        if not t:
            return _err("字段「%s」的类型不支持" % label)

        # 拼一列的 DDL：
        #   t[1] 是 MySQL 类型，required 为真是加 NOT NULL，最后把中文名写进 COMMENT
        cols.append("f%d %s%s COMMENT '%s'" % (
            i, t[1], " NOT NULL" if f.get("required") else "", label))

    # 生成表名：前缀 + 10 位随机十六进制。用随机名避免"表单重名冲突"
    table = _FORM_PREFIX + uuid.uuid4().hex[:10]

    # 拼完整的 CREATE TABLE 语句
    # 每张表单都自带三列：自增主键 id、药品外键 drug_id、创建时间 created_at
    # 【知识点：外键 FOREIGN KEY】CONSTRAINT ... FOREIGN KEY (drug_id) REFERENCES drug (drug_id)
    # 表示"本表的 drug_id 必须是 drug 表里存在的值"，数据库层面保证引用完整性
    sql = ("CREATE TABLE `%s` (\n"
           "  id INT AUTO_INCREMENT PRIMARY KEY,\n"
           "  drug_id INT NOT NULL COMMENT '药品ID',\n"
           "  %s,\n"
           "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
           "  KEY `idx_%s_drug` (drug_id),\n"
           "  CONSTRAINT `fk_%s_drug` FOREIGN KEY (drug_id) REFERENCES drug (drug_id)\n"
           ") COMMENT='%s'"   # 只保留表注释；引擎/字符集用默认值（InnoDB + 继承库级 utf8mb4）
           % (table, ",\n  ".join(cols), table, table, name))

    # 执行建表
    try:

        with get_cursor(commit=True) as cur:

            # 先查有没有同名表单（表注释 = 表单名）
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_comment = %s LIMIT 1", (name,))

            # 有同名就拒绝
            if cur.fetchone():
                return _err("已存在同名表单：%s" % name)

            # 执行 DDL。注意：MySQL 的 DDL 是"隐式提交"的，不能回滚
            cur.execute(sql)

        # 返回表名 + 实际执行的 DDL（页面上会展示这段 SQL）
        return _ok({"table": table, "name": name, "message": "业务表单「%s」已启用" % name,
                    "sql": sql})

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 查某个表单下的记录（表单名在网址里传进来）
@bp.route("/api/forms/<table>/records")
def api_form_records(table):

    try:

        with get_cursor() as cur:

            # 校验并拿到表单定义
            meta = _form_meta(cur, table)

            # 查这张表最近 50 条记录，顺便 JOIN 药品表把药名和规格带出来
            # l.* 表示"这张表单的所有列"；g.drug_name AS `__drug` 里用反引号是因为
            # 列别名带下划线前缀，加反引号避免和 MySQL 关键字冲突
            cur.execute(
                "SELECT l.*, g.drug_name AS `__drug`, g.spec AS `__spec` "
                "FROM `%s` l JOIN drug g ON g.drug_id = l.drug_id "
                "ORDER BY l.id DESC LIMIT 50" % table)

            # 格式化每一行
            rows = [_fmt_row(r) for r in cur.fetchall()]

        # 返回定义 + 数据 + 展示用 SQL
        return _ok({"meta": meta, "rows": rows,
                    "sql": "SELECT l.*, g.drug_name, g.spec FROM `%s` l "
                           "JOIN drug g ON g.drug_id = l.drug_id "
                           "ORDER BY l.id DESC LIMIT 50" % table})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 往某个表单里登记一条记录（对应课程的"插入数据"）
@bp.route("/api/forms/<table>/records", methods=["POST"])
def api_form_insert(table):

    # 取 JSON
    body = request.get_json(silent=True) or {}

    # 用户填的字段值，形如 {"f1": "厂家召回", "f2": "2026-11-01"}
    values = body.get("values") or {}

    try:

        with get_cursor(commit=True) as cur:

            # 先拿表单定义（知道有哪些字段、哪些必填）
            meta = _form_meta(cur, table)

            # 这条记录对应哪个药品
            try:
                drug_id = int(body.get("drug_id"))

            # 转不了说明没选
            except (TypeError, ValueError):
                return _err("请先选择这条记录对应的药品")

            # 验证药品存在
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))

            # 取一行
            drug = cur.fetchone()

            # 不存在
            if not drug:
                return _err("选择的药品已不存在，请刷新后重试")

            # 准备列名和参数，先把 drug_id 放进去
            keys, args = ["drug_id"], [drug_id]

            # 按表单定义的顺序逐字段取值
            for f in meta["fields"]:

                # 取这个字段的值
                v = values.get(f["key"])

                # 如果传的是字符串，去掉首尾空格
                if isinstance(v, str):
                    v = v.strip()

                # 空值处理
                if not v:

                    # 必填没填 → 报错，提示里带中文标签
                    if f["required"]:
                        return _err("请填写「%s」" % f["label"])

                    # 非必填 → 存 NULL
                    v = None

                # 记录列名
                keys.append(f["key"])

                # 记录值
                args.append(v)

            # 拼 INSERT：列名逐个加反引号，值全部用 %s 占位
            sql = ("INSERT INTO `%s` (%s) VALUES (%s)"
                   % (table, ", ".join("`%s`" % k for k in keys),
                      ", ".join(["%s"] * len(keys))))

            # 执行插入
            cur.execute(sql, args)

        # 返回成功
        return _ok({"message": "已为「%s」登记 1 条表单记录" % drug["drug_name"], "sql": sql})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 删除整张表单（对应课程的"删除数据表"）
@bp.route("/api/forms/<table>/remove", methods=["POST"])
def api_form_drop(table):

    try:

        with get_cursor(commit=True) as cur:

            # 先确认这确实是我们建的表单（内部会校验前缀）
            meta = _form_meta(cur, table)

            # 数一下还有多少条记录
            cur.execute("SELECT COUNT(*) AS c FROM `%s`" % table)

            # 取数量
            n = cur.fetchone()["c"]

            # 【知识点：二次确认】有数据且用户没勾"确认删除"时，返回 need_confirm，
            # 前端弹窗问一句"还有 N 条记录，确定一起删吗"，用户确认后带 force 再请求一次
            if n and not (request.get_json(silent=True) or {}).get("force"):
                return _err("该表单下还有 %d 条记录，确认要连同记录一起删除吗？" % n,
                            need_confirm=True, count=n)

            # 执行删表。DROP TABLE 会连表结构和数据一起删掉，且无法回滚
            cur.execute("DROP TABLE `%s`" % table)

        # 返回成功
        return _ok({"message": "业务表单「%s」已删除" % meta["name"],
                    "sql": "DROP TABLE `%s`" % table})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 跨数据表操作：四张只读 JOIN 报表
# =====================================================================
# 报表清单：键是前端传的名字，值是标题和说明（说明里写明涉及几张表）
REPORTS = {
    "supplier_rank": {"name": "供应商供货排行（5 表 JOIN）",
                      "desc": "purchase_item → purchase_order → supplier → warehouse → employee"},
    "drug_sales": {"name": "药品销售排行（5 表 JOIN）",
                   "desc": "sale_item → sale_order → drug → drug_category → warehouse"},
    "batch_chain": {"name": "效期预警批次溯源（6 表 JOIN）",
                    "desc": "stock_batch → drug → warehouse → purchase_item → purchase_order → supplier"},
    "drug_trace": {"name": "单药品出入库全链路（7 表 JOIN）",
                   "desc": "stock_movement → drug → warehouse → employee → sale_order / purchase_order → supplier"},
}


# 跑报表：前端传报表名，这里分发到对应的 SQL
@bp.route("/api/report", methods=["POST"])
def api_report():

    # 取 JSON
    body = request.get_json(silent=True) or {}

    # 报表名
    name = body.get("name")

    # 【知识点：白名单校验】只允许 REPORTS 里列出的名字，防止前端乱传导致执行意外查询
    if name not in REPORTS:
        return _err("未知报表")

    try:

        # 供应商排行（5 表）
        if name == "supplier_rank":
            rows = dao.report_supplier_rank(10)

        # 药品销售排行（5 表）
        elif name == "drug_sales":
            rows = dao.report_drug_sales(12)

        # 批次溯源（6 表）
        elif name == "batch_chain":
            rows = dao.report_batch_chain(15)

        # 剩下的就是单药品全链路（7 表），需要指定药品
        else:

            # 取药品 id
            drug_id = int(body.get("drug_id") or 0)

            # 没选就提示
            if not drug_id:
                return _err("请先选择一种药品")

            # 执行 7 表 JOIN
            rows = dao.report_drug_trace(drug_id, 20)

        # 返回数据 + 列名（列名由第一行的键推导，前端据此生成表头）+ 行数
        return _ok({"rows": [_fmt_row(r) for r in rows],
                    "columns": list(rows[0].keys()) if rows else [],
                    "count": len(rows)})

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 多用户并发访问 + 访问性能记录
# =====================================================================
@bp.route("/api/sale_concurrency", methods=["POST"])
def api_sale_concurrency():
    """多个销售终端同时卖同一种药：验证库存不超卖，并记录每个终端的响应耗时。"""

    # 取 JSON
    body = request.get_json(silent=True) or {}

    try:

        # 药品、仓库
        drug_id = int(body.get("drug_id") or 0)
        warehouse_id = int(body.get("warehouse_id") or 0)

        # 【知识点：min(max(x, 下限), 上限) 夹紧】把取值限制在合理范围内，
        # 终端数限制 2~30，防止前端传 10000 直接把服务器打爆
        clients = min(max(int(body.get("clients") or 10), 2), 30)

        # 每次卖出数量限制 1~50
        qty = min(max(int(body.get("qty") or 1), 1), 50)

        # 是否需要在演示前重置库存
        reset_stock = body.get("reset_stock")

        # 空值 → None（不重置）；有值 → 转成整数，且不能为负
        reset_stock = None if reset_stock in (None, "") else max(int(reset_stock), 0)

    except (TypeError, ValueError):
        return _err("参数格式不正确")

    # 药品必选
    if not drug_id:
        return _err("请先选择一种药品")

    # 仓库必选
    if not warehouse_id:
        return _err("请先选择出库仓库")

    try:

        # 交给业务层：开 threads 并发卖，返回结果统计
        return _ok(services.sale_concurrency(drug_id, warehouse_id, clients, qty, reset_stock))

    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# 查最近的访问日志（页面底部"访问性能记录"表格用）
@bp.route("/api/access_logs")
def api_access_logs():

    try:

        # 最近 12 条
        rows = dao.recent_access_logs(12)

        # 返回日志 + 总条数
        return _ok({"rows": [_fmt_row(r) for r in rows],
                    "total": dao.access_log_count()})

    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 索引支持（融入业务）：对「单药品出入库全链路」这条真实业务查询做体检——
# 同一条 SQL 一次正常执行（走索引）、一次 IGNORE INDEX 强制全表扫描，
# 用实测耗时和 EXPLAIN 计划说明索引在业务查询里的作用。
# =====================================================================
def _explain(cur, sql, args, alias="m"):
    """EXPLAIN 会输出 JOIN 每一行，这里取流水表（别名 m）那一行，
    它才反映"走索引 ref"还是"全表扫描 ALL"。"""

    # 【知识点：EXPLAIN】在 SQL 前面加 EXPLAIN，MySQL 不执行查询，
    # 只告诉你"它打算怎么执行"：走哪个索引（key）、扫多少行（rows）、访问类型（type）
    cur.execute("EXPLAIN " + sql, args)

    # 取回所有行（多表 JOIN 会有多行，每行对应一张表）
    rows = cur.fetchall() or []

    # 【知识点：next(生成器, 默认值)】找出"表别名是 m"的那一行；
    # 找不到就退回第一行；连结果都没有就用空字典
    pick = next((r for r in rows if r.get("table") == alias), rows[0] if rows else {})

    # 只挑三个关键字段返回：type（访问类型）、key（用的索引）、rows（预估扫描行数）
    return {k: pick.get(k) for k in ("type", "key", "rows") if k in pick}


def _timed(cur, sql, args, repeat=3):

    # 收集每次的耗时
    durations = []

    # 重复跑 N 次，取平均，减少偶然波动
    for _ in range(repeat):

        # 开始计时
        t0 = time.perf_counter()

        # 执行查询
        cur.execute(sql, args)

        # 【知识点：为什么要 fetchall】MySQL 驱动默认"惰性取结果"，
        # 不把结果取完，查询就不算真正执行完，计时会偏小。所以必须取完再停表
        cur.fetchall()

        # 停表，换算毫秒，存进列表
        durations.append((time.perf_counter() - t0) * 1000)

    # 排序（顺便去掉极端值的影响，这里只用于算平均）
    durations.sort()

    # 返回平均值，保留两位小数
    return round(sum(durations) / len(durations), 2)


# 索引体检接口
@bp.route("/api/index_check")
def api_index_check():

    # 取药品 id。request.args.get("drug_id", type=int) 会自动把字符串转成整数
    drug_id = request.args.get("drug_id", type=int)

    # 没选药品
    if not drug_id:
        return _err("请先选择药品")

    # 重复次数：默认 3 次，最多 10 次（防止跑太久）
    repeat = min(request.args.get("repeat", 3, type=int) or 3, 10)

    try:

        # 正常版 SQL：占位符替换成空字符串
        normal_sql = dao.TRACE_SQL.replace("{ignore}", "")

        # 【知识点：IGNORE INDEX 强制全表扫描】
        # 这是 MySQL 的"提示语法"，告诉优化器"别用这两个索引"。
        # 用来做对比实验：同一条 SQL，走索引 vs 不走索引，差多少
        slow_sql = dao.TRACE_SQL.replace(
            "{ignore}", " IGNORE INDEX (idx_move_drug_time, idx_move_time)")

        # 只读查询
        with get_cursor() as cur:

            # 先确认索引到底存在不存在（否则对比就没意义）
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'stock_movement' "
                "AND index_name = 'idx_move_drug_time'")

            # 取结果
            row = cur.fetchone() or {}

            # 有记录就说明索引存在
            index_exists = bool(row.get("cnt"))

            # 取"走索引"的执行计划
            plan_with = _explain(cur, normal_sql, [drug_id, 20])

            # 取"不走索引"的执行计划
            plan_without = _explain(cur, slow_sql, [drug_id, 20])

            # 实测"走索引"耗时
            ms_with = _timed(cur, normal_sql, [drug_id, 20], repeat)

            # 实测"不走索引"耗时
            ms_without = _timed(cur, slow_sql, [drug_id, 20], repeat)

    except pymysql.MySQLError as e:
        return _err(_friendly(e))

    # 加速比 = 慢的 ÷ 快的。比如 13.84 表示快了 13.84 倍
    speedup = round(ms_without / ms_with, 1) if ms_with > 0 else None

    # 把两边的耗时、执行计划、加速比一起返回给页面
    return _ok({
        "drug_id": drug_id,
        "index": "idx_move_drug_time",
        "index_exists": index_exists,
        "repeat": repeat,
        "with": {"ms": ms_with, "plan": plan_with},
        "without": {"ms": ms_without, "plan": plan_without},
        "speedup": speedup,
    })
