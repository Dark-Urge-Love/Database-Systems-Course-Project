# -*- coding: utf-8 -*-
"""
Views 层：页面路由与 Ajax 接口
------------------------------------------------------------------
对应课程要求的 B/S 结构：浏览器（客户端）→ Flask（本机）→ 通过 TCP/IP
访问 Linux 服务器上的 MySQL。数据概览页显示当前连接的目标服务器信息，
方便答辩时直接证明"数据不在本机"。
"""
import datetime
import re
import time
import uuid
import traceback

import pymysql
from flask import Blueprint, jsonify, render_template, request

from . import config, dao, services
from .db import BizError, get_cursor

bp = Blueprint("main", __name__)


# =====================================================================
# 公共：把当前连接信息塞给所有模板
# =====================================================================
def connection_banner():
    base = {
        "host": config.DB_HOST,
        "port": config.DB_PORT,
        "user": config.DB_USER,
        "db": config.DB_NAME,
    }
    try:
        info = dao.server_info()
        base.update({
            "ok": True,
            "version": info["version"],
            "hostname": info["hostname"],
            "datadir": info["datadir"],
        })
        return base
    except Exception as e:                                     # noqa: BLE001
        base.update({"ok": False, "error": str(e)})
        return base


@bp.app_context_processor
def inject_globals():
    return {"APP_TITLE": config.APP_TITLE, "conn": connection_banner()}


@bp.after_request
def no_cache_api(resp):
    """接口数据永远新鲜：库存、日志这类数字不允许被浏览器缓存。"""
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def _err(message, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), 200


def _is_api():
    return request.path.startswith("/api/")


@bp.errorhandler(pymysql.MySQLError)
def handle_mysql_error(e):                                     # noqa: ANN001
    msg = "数据库错误（%s）：%s" % (e.args[0] if e.args else "?", str(e))
    if _is_api():
        return _err(msg)
    return render_template("error.html", message=msg), 500


@bp.errorhandler(Exception)
def handle_any_error(e):                                       # noqa: ANN001
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    tb = traceback.format_exc()
    if _is_api():
        return _err("服务器内部错误：%s" % str(e), trace=tb.splitlines()[-4:])
    return render_template("error.html", message="%s: %s" % (type(e).__name__, e)), 500


def _fmt_value(v):
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    return v


def _fmt_row(row):
    return {k: _fmt_value(v) for k, v in row.items()}


def _friendly(e, sql=None):
    """把常见 MySQL 报错翻译成业务语言（其余原样返回）。"""
    code = e.args[0] if e.args else None
    msgs = {
        1451: "该记录正被其他业务引用，不能删除。",
        1452: "关联的业务数据不存在。",
        1062: "该记录已存在，请勿重复录入。",
        1050: "该表已存在。",
        1146: "数据表不存在。",
        1406: "内容太长，超出字段允许的长度。",
        1265: "内容格式与字段类型不匹配。",
        1292: "日期格式不正确，请使用 2026-01-31 这样的格式。",
        3819: "数据不满足业务校验规则（如库存不能为负）。",
        1045: "数据库拒绝了登录（账号或密码不对）。若连的是 Linux 服务器，"
              "请在 Ubuntu 里执行 deploy/init_server_db.sh 创建 druguser 账号。",
        2003: "连不上数据库服务器：确认 Ubuntu 已启动、MySQL 已运行（dbup），"
              "或服务器 IP 已变化（Ubuntu 里执行 hostname -I 查看）。",
    }
    msg = msgs.get(code, "数据库错误（%s）：%s" % (code, str(e)))
    if sql:
        msg += "\n执行的 SQL：" + sql
    return msg


# =====================================================================
# 首页：数据规模证明（课程要求：≥5 表、≥100,000 条）
# =====================================================================
@bp.route("/")
def dashboard():
    log_count = 0
    summary = {"total_qty": 0, "sku_rows": 0, "low_rows": 0, "near_expiry": 0}
    try:
        rows, total = dao.table_row_counts()
        db_ok, db_err = True, None
        try:
            log_count = dao.access_log_count()
            summary = dao.stock_summary()
        except pymysql.MySQLError:
            pass
    except Exception as e:                                     # noqa: BLE001
        rows, total = [], 0
        db_ok, db_err = False, str(e)
    return render_template("dashboard.html", rows=rows, total=total,
                           db_ok=db_ok, db_err=db_err, log_count=log_count,
                           summary=summary)


@bp.route("/ops")
def ops_page():
    return render_template("ops.html")


# =====================================================================
# 基础资料：下拉选项
# =====================================================================
@bp.route("/api/options")
def api_options():
    try:
        return _ok({
            "suppliers": dao.list_suppliers(),
            "warehouses": dao.list_warehouses(),
            "buyers": dao.list_employees("采购员"),
            "sellers": dao.list_employees("销售员"),
            "drugs": dao.drug_options(),
            "categories": dao.list_categories(),
        })
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 药品目录：INSERT / UPDATE / DELETE
# =====================================================================
@bp.route("/api/drugs")
def api_drugs():
    q = (request.args.get("q") or "").strip()
    try:
        rows = dao.list_drugs(q or None)
        for r in rows:
            r["pp"] = float(r["pp"])
            r["rp"] = float(r["rp"])
            r["stock"] = int(r["stock"])
        return _ok({"rows": rows, "total": len(rows), "keyword": q})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


def _parse_float(body, key, label, required=True, minimum=0.0):
    raw = body.get(key)
    if raw in (None, ""):
        if required:
            raise BizError("请填写%s" % label)
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise BizError("%s请填写数字" % label)
    if v < minimum:
        raise BizError("%s不能小于 %g" % (label, minimum))
    return round(v, 2)


def _drug_payload(body):
    data = {}
    name = (body.get("name") or "").strip()
    if not name:
        raise BizError("请填写药品名称")
    if len(name) > 100:
        raise BizError("药品名称不能超过 100 字")
    data["drug_name"] = name

    try:
        cat = int(body.get("cate") or 0)
    except (TypeError, ValueError):
        raise BizError("请选择药品分类")
    if not cat:
        raise BizError("请选择药品分类")
    data["category_id"] = cat

    spec = (body.get("spec") or "").strip()
    if not spec:
        raise BizError("请填写规格，如 0.25g*24粒")
    data["spec"] = spec
    data["unit"] = (body.get("unit") or "").strip() or "盒"
    data["manufacturer"] = (body.get("maker") or "").strip() or None

    pp = _parse_float(body, "pp", "采购价")
    rp = _parse_float(body, "rp", "零售价")
    if rp < pp:
        raise BizError("零售价不能低于采购价")
    data["purchase_price"] = pp
    data["retail_price"] = rp

    try:
        life = int(body.get("life") or 24)
    except (TypeError, ValueError):
        raise BizError("保质期请填写整数月份")
    if not 1 <= life <= 120:
        raise BizError("保质期应在 1~120 个月之间")
    data["shelf_life_months"] = life
    data["is_rx"] = 1 if body.get("rx") else 0
    return data


@bp.route("/api/drugs", methods=["POST"])
def api_drug_create():
    body = request.get_json(silent=True) or {}
    try:
        data = _drug_payload(body)
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT drug_id, drug_code FROM drug WHERE drug_name = %s "
                        "AND spec = %s LIMIT 1", (data["drug_name"], data["spec"]))
            if cur.fetchone():
                raise BizError("药品目录中已有同名同规格的品种，请直接调整原记录的价格或库存")
            cur.execute("SELECT COALESCE(MAX(drug_id), 0) + 1 AS n FROM drug")
            n = cur.fetchone()["n"]
            code = "YP%06d" % n
            cols = ["drug_code"] + list(data.keys())
            vals = [code] + [data[k] for k in data]
            sql = ("INSERT INTO drug (%s) VALUES (%s)"
                   % (", ".join(cols), ", ".join(["%s"] * len(cols))))
            cur.execute(sql, vals)
        return _ok({"message": "已新增药品：%s（编码 %s）" % (data["drug_name"], code),
                    "sql": sql})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/drugs/<int:drug_id>", methods=["POST"])
def api_drug_update(drug_id):
    body = request.get_json(silent=True) or {}
    try:
        data = _drug_payload(body)
        data["status"] = 1 if body.get("status") else 0
        cols = list(data.keys())
        sql = ("UPDATE drug SET %s WHERE drug_id = %%s"
               % ", ".join("%s = %%s" % c for c in cols))
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))
            if not cur.fetchone():
                raise BizError("该药品已不存在，请刷新后重试")
            cur.execute(sql, [data[c] for c in cols] + [drug_id])
        return _ok({"message": "已保存：%s" % data["drug_name"], "sql": sql})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/drugs/<int:drug_id>/remove", methods=["POST"])
def api_drug_delete(drug_id):
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))
            row = cur.fetchone()
            if not row:
                raise BizError("该药品已不存在，请刷新后重试")

            refs = []
            cur.execute("SELECT COUNT(*) AS c FROM purchase_item WHERE drug_id = %s", (drug_id,))
            n = cur.fetchone()["c"]
            if n:
                refs.append("采购明细 %d 条" % n)
            cur.execute("SELECT COUNT(*) AS c FROM sale_item WHERE drug_id = %s", (drug_id,))
            n = cur.fetchone()["c"]
            if n:
                refs.append("销售明细 %d 条" % n)
            cur.execute("SELECT COALESCE(SUM(quantity),0) AS q FROM stock WHERE drug_id = %s",
                        (drug_id,))
            n = cur.fetchone()["q"]
            if n:
                refs.append("库存结存 %d" % n)
            for t in _form_tables(cur):
                cur.execute("SELECT COUNT(*) AS c FROM `%s` WHERE drug_id = %%s" % t, (drug_id,))
                n = cur.fetchone()["c"]
                if n:
                    refs.append("表单「%s」%d 条" % (_form_meta(cur, t)["name"], n))
            if refs:
                raise BizError("「%s」已有业务数据（%s），不能删除。"
                               "如不再经营，请在编辑中把状态改为停用。"
                               % (row["drug_name"], "、".join(refs)))
            cur.execute("DELETE FROM drug WHERE drug_id = %s", (drug_id,))
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
        rows = [_fmt_row(r) for r in dao.recent_purchases(15)]
        return _ok({"rows": rows})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/purchases", methods=["POST"])
def api_purchase_create():
    body = request.get_json(silent=True) or {}
    try:
        supplier_id = int(body.get("supplier_id") or 0)
        warehouse_id = int(body.get("warehouse_id") or 0)
        employee_id = int(body.get("employee_id") or 0)
        drug_id = int(body.get("drug_id") or 0)
        qty = int(body.get("qty") or 0)
        if not (supplier_id and warehouse_id and employee_id and drug_id):
            raise BizError("请选择供应商、入库仓库、采购员和药品")
        if qty <= 0:
            raise BizError("采购数量必须大于 0")
        unit_price = _parse_float(body, "unit_price", "采购单价")
        batch_no = (body.get("batch_no") or "").strip() or None
        prod_date = (body.get("prod_date") or "").strip() or None
        expiry_date = (body.get("expiry_date") or "").strip() or None
        if not expiry_date:
            raise BizError("请填写有效期至")
        if prod_date and expiry_date <= prod_date:
            raise BizError("有效期必须晚于生产日期")

        info = services.receive_purchase(supplier_id, warehouse_id, employee_id,
                                         drug_id, qty, unit_price,
                                         batch_no, prod_date, expiry_date)
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
        return _ok(info)
    except BizError as e:
        return _err(str(e))
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/sales")
def api_sales():
    try:
        rows = [_fmt_row(r) for r in dao.recent_sales(15)]
        return _ok({"rows": rows})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/sales", methods=["POST"])
def api_sale_create():
    body = request.get_json(silent=True) or {}
    try:
        warehouse_id = int(body.get("warehouse_id") or 0)
        employee_id = int(body.get("employee_id") or 0)
        drug_id = int(body.get("drug_id") or 0)
        qty = int(body.get("qty") or 0)
        customer = (body.get("customer_name") or "").strip()
        ctype = (body.get("customer_type") or "连锁药店").strip()
        if not (warehouse_id and employee_id and drug_id):
            raise BizError("请选择出库仓库、销售员和药品")
        if qty <= 0:
            raise BizError("出库数量必须大于 0")
        if not customer:
            raise BizError("请填写客户名称")
        info = services.sell_out(warehouse_id, employee_id, drug_id, qty, customer, ctype)
        unit_price = round(info["amount"] / qty, 2) if qty else 0
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
        rows = [_fmt_row(r) for r in dao.stock_table(80)]
        return _ok({"rows": rows})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 业务表单：新建表单（CREATE TABLE）/ 登记（INSERT）/ 删除表单（DROP TABLE）
# 每张表单都带 drug_id 外键指向药品目录，登记时先选药，表单永远是药品的子表。
# =====================================================================
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FORM_PREFIX = "bizform_"
CORE_TABLES = set(dao.TABLES)

FORM_TYPES = {
    "text":  ("文本", "VARCHAR(100)"),
    "long":  ("长文本", "VARCHAR(255)"),
    "int":   ("整数", "INT"),
    "money": ("金额", "DECIMAL(10,2)"),
    "date":  ("日期", "DATE"),
}

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


def _check_ident(name):
    if not name or not _IDENT.match(name):
        raise BizError("名称不合法")


def _form_meta(cur, table):
    """读取表单的业务定义：表单名与字段名都来自数据表的注释。"""
    _check_ident(table)
    if not table.startswith(_FORM_PREFIX):
        raise BizError("该表单不存在")
    cur.execute(
        "SELECT table_comment FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    head = cur.fetchone()
    if not head:
        raise BizError("该表单不存在")
    cur.execute(
        "SELECT column_name, column_type, column_comment, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s "
        "ORDER BY ordinal_position", (table,))
    fields = []
    for r in cur.fetchall():
        name = r.get("COLUMN_NAME") or r.get("column_name")
        if name in ("id", "drug_id", "created_at"):
            continue
        fields.append({
            "key": name,
            "label": (r.get("COLUMN_COMMENT") or r.get("column_comment")) or name,
            "type": (r.get("COLUMN_TYPE") or r.get("column_type") or "").upper(),
            "required": (r.get("IS_NULLABLE") or r.get("is_nullable")) == "NO",
        })
    return {"table": table, "name": head.get("TABLE_COMMENT") or head.get("table_comment"),
            "fields": fields}


def _form_tables(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name LIKE 'bizform\\_%' "
        "ORDER BY create_time DESC")
    return [r.get("TABLE_NAME") or r.get("table_name") for r in cur.fetchall()]


@bp.route("/api/forms")
def api_forms():
    try:
        with get_cursor() as cur:
            items = []
            for t in _form_tables(cur):
                meta = _form_meta(cur, t)
                cur.execute("SELECT COUNT(*) AS c, COUNT(DISTINCT drug_id) AS d "
                            "FROM `%s`" % t)
                row = cur.fetchone()
                meta["count"] = row["c"]
                meta["drug_count"] = row["d"]
                items.append(meta)
            cur.execute("SELECT drug_id AS id, drug_name AS name, spec, unit "
                        "FROM drug WHERE status = 1 ORDER BY drug_name LIMIT 1200")
            drugs = cur.fetchall()
        return _ok({"items": items, "drugs": drugs, "templates": FORM_TEMPLATES,
                    "types": [{"key": k, "label": v[0]} for k, v in FORM_TYPES.items()]})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/forms", methods=["POST"])
def api_form_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _err("请填写表单名称")
    if len(name) > 30:
        return _err("表单名称不能超过 30 字")
    raw_fields = body.get("fields") or []
    if not 1 <= len(raw_fields) <= 8:
        return _err("请设置 1~8 个字段")

    cols, seen = [], set()
    for i, f in enumerate(raw_fields, start=1):
        label = (f.get("label") or "").strip()
        if not label:
            return _err("第 %d 个字段还没有填写名称" % i)
        if len(label) > 20:
            return _err("字段名称不能超过 20 字：%s" % label)
        if label in seen:
            return _err("字段名称重复：%s" % label)
        seen.add(label)
        t = FORM_TYPES.get((f.get("type") or "").strip())
        if not t:
            return _err("字段「%s」的类型不支持" % label)
        cols.append("f%d %s%s COMMENT '%s'" % (
            i, t[1], " NOT NULL" if f.get("required") else "", label))

    table = _FORM_PREFIX + uuid.uuid4().hex[:10]
    sql = ("CREATE TABLE `%s` (\n"
           "  id INT AUTO_INCREMENT PRIMARY KEY,\n"
           "  drug_id INT NOT NULL COMMENT '药品ID',\n"
           "  %s,\n"
           "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
           "  KEY `idx_%s_drug` (drug_id),\n"
           "  CONSTRAINT `fk_%s_drug` FOREIGN KEY (drug_id) REFERENCES drug (drug_id)\n"
           ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='%s'"
           % (table, ",\n  ".join(cols), table, table, name))
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_comment = %s LIMIT 1", (name,))
            if cur.fetchone():
                return _err("已存在同名表单：%s" % name)
            cur.execute(sql)
        return _ok({"table": table, "name": name, "message": "业务表单「%s」已启用" % name,
                    "sql": sql})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/forms/<table>/records")
def api_form_records(table):
    try:
        with get_cursor() as cur:
            meta = _form_meta(cur, table)
            cur.execute(
                "SELECT l.*, g.drug_name AS `__drug`, g.spec AS `__spec` "
                "FROM `%s` l JOIN drug g ON g.drug_id = l.drug_id "
                "ORDER BY l.id DESC LIMIT 50" % table)
            rows = [_fmt_row(r) for r in cur.fetchall()]
        return _ok({"meta": meta, "rows": rows,
                    "sql": "SELECT l.*, g.drug_name, g.spec FROM `%s` l "
                           "JOIN drug g ON g.drug_id = l.drug_id "
                           "ORDER BY l.id DESC LIMIT 50" % table})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/forms/<table>/records", methods=["POST"])
def api_form_insert(table):
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    try:
        with get_cursor(commit=True) as cur:
            meta = _form_meta(cur, table)
            try:
                drug_id = int(body.get("drug_id"))
            except (TypeError, ValueError):
                return _err("请先选择这条记录对应的药品")
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))
            drug = cur.fetchone()
            if not drug:
                return _err("选择的药品已不存在，请刷新后重试")
            keys, args = ["drug_id"], [drug_id]
            for f in meta["fields"]:
                v = values.get(f["key"])
                if isinstance(v, str):
                    v = v.strip()
                if not v:
                    if f["required"]:
                        return _err("请填写「%s」" % f["label"])
                    v = None
                keys.append(f["key"])
                args.append(v)
            sql = ("INSERT INTO `%s` (%s) VALUES (%s)"
                   % (table, ", ".join("`%s`" % k for k in keys),
                      ", ".join(["%s"] * len(keys))))
            cur.execute(sql, args)
        return _ok({"message": "已为「%s」登记 1 条表单记录" % drug["drug_name"], "sql": sql})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/forms/<table>/remove", methods=["POST"])
def api_form_drop(table):
    try:
        with get_cursor(commit=True) as cur:
            meta = _form_meta(cur, table)
            cur.execute("SELECT COUNT(*) AS c FROM `%s`" % table)
            n = cur.fetchone()["c"]
            if n and not (request.get_json(silent=True) or {}).get("force"):
                return _err("该表单下还有 %d 条记录，确认要连同记录一起删除吗？" % n,
                            need_confirm=True, count=n)
            cur.execute("DROP TABLE `%s`" % table)
        return _ok({"message": "业务表单「%s」已删除" % meta["name"],
                    "sql": "DROP TABLE `%s`" % table})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 跨数据表操作：四张只读 JOIN 报表
# =====================================================================
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


@bp.route("/api/report", methods=["POST"])
def api_report():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name not in REPORTS:
        return _err("未知报表")
    try:
        if name == "supplier_rank":
            rows = dao.report_supplier_rank(10)
        elif name == "drug_sales":
            rows = dao.report_drug_sales(12)
        elif name == "batch_chain":
            rows = dao.report_batch_chain(15)
        else:
            drug_id = int(body.get("drug_id") or 0)
            if not drug_id:
                return _err("请先选择一种药品")
            rows = dao.report_drug_trace(drug_id, 20)
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
    body = request.get_json(silent=True) or {}
    try:
        drug_id = int(body.get("drug_id") or 0)
        warehouse_id = int(body.get("warehouse_id") or 0)
        clients = min(max(int(body.get("clients") or 10), 2), 30)
        qty = min(max(int(body.get("qty") or 1), 1), 50)
        reset_stock = body.get("reset_stock")
        reset_stock = None if reset_stock in (None, "") else max(int(reset_stock), 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not drug_id:
        return _err("请先选择一种药品")
    if not warehouse_id:
        return _err("请先选择出库仓库")
    try:
        return _ok(services.sale_concurrency(drug_id, warehouse_id, clients, qty, reset_stock))
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/access_logs")
def api_access_logs():
    try:
        rows = dao.recent_access_logs(12)
        return _ok({"rows": [_fmt_row(r) for r in rows],
                    "total": dao.access_log_count()})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 索引支持：同一查询在「走索引」与「强制绕过索引」两种情况下对比
# =====================================================================
PERF_CASES = {
    "movement_by_drug": {
        "title": "按药品查库存流水（stock_movement，5.7 万行）",
        "table": "stock_movement",
        "index": "idx_move_drug_time",
        "sql": ("SELECT SQL_NO_CACHE m.move_id, m.move_time, m.move_type, m.change_qty "
                "FROM stock_movement m{ignore} WHERE m.drug_id = %s "
                "AND m.move_time >= '2025-01-01'"),
        "ignore": ["idx_move_drug_time", "idx_move_time"],
        "param": "drug_id",
        "hint": "药品编号",
    },
    "movement_by_date": {
        "title": "按日期统计近期出入库（stock_movement，时间范围）",
        "table": "stock_movement",
        "index": "idx_move_time",
        "sql": ("SELECT SQL_NO_CACHE COUNT(*) AS cnt, SUM(m.change_qty) AS qty "
                "FROM stock_movement m{ignore} WHERE m.move_time >= %s"),
        "ignore": ["idx_move_time", "idx_move_type_time", "idx_move_drug_time"],
        "param": "since",
        "hint": "起始日期",
    },
}


def _explain(cur, sql, args):
    cur.execute("EXPLAIN " + sql, args)
    row = cur.fetchone() or {}
    return {k: row.get(k) for k in ("type", "key", "rows", "possible_keys", "Extra") if k in row}


def _timed(cur, sql, args, repeat=5):
    durations = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        cur.execute(sql, args)
        cur.fetchall()
        durations.append((time.perf_counter() - t0) * 1000)
    durations.sort()
    return {
        "avg_ms": round(sum(durations) / len(durations), 3),
        "min_ms": round(durations[0], 3),
        "max_ms": round(durations[-1], 3),
        "samples": [round(d, 3) for d in durations],
    }


@bp.route("/perf")
def perf_page():
    return render_template("perf.html", cases=PERF_CASES)


@bp.route("/api/perf", methods=["POST"])
def api_perf():
    body = request.get_json(silent=True) or {}
    key = body.get("case")
    if key not in PERF_CASES:
        return _err("未知用例")
    case = PERF_CASES[key]

    if case["param"] == "drug_id":
        param = int(body.get("drug_id") or 1)
        args = (param,)
    else:
        param = (body.get("since") or "2026-09-01").strip()
        args = (param,)
    repeat = min(int(body.get("repeat") or 5), 20)

    with get_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s", (case["table"],))
        existing = {r["INDEX_NAME"] or r["index_name"] for r in cur.fetchall()}
        index_exists = case["index"] in existing

    try:
        ignored = [i for i in case["ignore"] if i in existing]
        ignore_clause = (" IGNORE INDEX (" + ", ".join(ignored) + ")") if ignored else ""
        without_sql = case["sql"].replace("{ignore}", ignore_clause)
        with get_cursor() as cur:
            plan_without = _explain(cur, without_sql, args)
            time_without = _timed(cur, without_sql, args, repeat)
        with get_cursor() as cur:
            with_sql = case["sql"].replace("{ignore}", "")
            plan_with = _explain(cur, with_sql, args)
            time_with = _timed(cur, with_sql, args, repeat)
    except pymysql.MySQLError as e:
        return _err("执行失败：%s" % str(e))

    speedup = None
    if time_with["avg_ms"] > 0:
        speedup = round(time_without["avg_ms"] / time_with["avg_ms"], 2)

    return _ok({
        "title": case["title"],
        "index": case["index"],
        "index_exists": index_exists,
        "param": param,
        "repeat": repeat,
        "without": {"plan": plan_without, "time": time_without, "sql": without_sql},
        "with": {"plan": plan_with, "time": time_with, "sql": with_sql},
        "speedup": speedup,
    })
