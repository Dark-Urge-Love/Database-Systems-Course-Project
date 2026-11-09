# -*- coding: utf-8 -*-
"""
Views 层：页面路由与 Ajax 接口
------------------------------------------------------------------
对应课程要求的 B/S 结构：浏览器（客户端）→ Flask（本机）→ 通过 TCP/IP
访问 Linux 服务器上的 MySQL。页面顶部固定显示当前连接的目标服务器地址，
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
from .db import BizError, get_cursor, transaction

bp = Blueprint("main", __name__)


# =====================================================================
# 公共：把当前连接信息塞给所有模板
# =====================================================================
def connection_banner():
    try:
        info = dao.server_info()
        return {
            "ok": True,
            "host": config.DB_HOST,
            "port": config.DB_PORT,
            "user": config.DB_USER,
            "db": config.DB_NAME,
            "version": info["version"],
            "hostname": info["hostname"],
            "datadir": info["datadir"],
        }
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "host": config.DB_HOST, "port": config.DB_PORT,
                "user": config.DB_USER, "db": config.DB_NAME, "error": str(e)}


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
    """页面出错时给一个能自助排查的页面，而不是 Flask 默认的报错页。"""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    tb = traceback.format_exc()
    if _is_api():
        return _err("服务器内部错误：%s" % str(e), trace=tb.splitlines()[-4:])
    return render_template("error.html", message="%s: %s" % (type(e).__name__, e)), 500


# =====================================================================
# 首页：数据规模证明（课程要求：≥5 表、≥100,000 条）
# =====================================================================
@bp.route("/")
def dashboard():
    log_count = 0
    try:
        rows, total = dao.table_row_counts()
        db_ok, db_err = True, None
        try:
            log_count = dao.access_log_count()
        except pymysql.MySQLError:
            log_count = 0
    except Exception as e:                                     # noqa: BLE001
        rows, total = [], 0
        db_ok, db_err = False, str(e)
    return render_template("dashboard.html", rows=rows, total=total,
                           db_ok=db_ok, db_err=db_err, log_count=log_count)


# =====================================================================
# 患者端：挂号（T1 事务）
# =====================================================================
@bp.route("/register")
def register_page():
    departments = dao.list_departments()
    return render_template("register.html", departments=departments)


@bp.route("/api/doctors")
def api_doctors():
    dept_id = request.args.get("dept_id", type=int)
    return _ok(dao.list_doctors(dept_id))


@bp.route("/api/schedules")
def api_schedules():
    doctor_id = request.args.get("doctor_id", type=int)
    if not doctor_id:
        return _err("缺少 doctor_id")
    only_available = request.args.get("all") != "1"
    return _ok(dao.list_schedules(doctor_id, only_available=only_available))


@bp.route("/api/patients")
def api_patients():
    return _ok(dao.list_patients(request.args.get("q"), limit=30))


@bp.route("/api/register", methods=["POST"])
def api_register():
    body = request.get_json(silent=True) or {}
    try:
        patient_id = int(body.get("patient_id") or 0)
        schedule_id = int(body.get("schedule_id") or 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not patient_id or not schedule_id:
        return _err("请先选择患者和排班")
    t0 = time.time()
    try:
        info = services.register(patient_id, schedule_id)
        info["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        info["sql"] = (
            "START TRANSACTION;\n"
            "SELECT remain_slots FROM schedule WHERE schedule_id=%s FOR UPDATE;\n"
            "INSERT INTO appointment(patient_id, schedule_id, appt_time, queue_no, status) "
            "VALUES (%s, %s, NOW(), %s, '已预约');\n"
            "UPDATE schedule SET remain_slots = remain_slots - 1 WHERE schedule_id=%s;\n"
            "COMMIT;" % (schedule_id, patient_id, schedule_id, info["queue_no"], schedule_id)
        )
        return _ok(info)
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err("数据库错误：%s" % str(e))


@bp.route("/api/dispense_concurrency", methods=["POST"])
def api_dispense_concurrency():
    """多个药房窗口同时发同一种药：验证库存不超发，并记录每个窗口的响应耗时。"""
    body = request.get_json(silent=True) or {}
    try:
        drug_id = int(body.get("drug_id") or 0)
        clients = min(max(int(body.get("clients") or 10), 2), 30)
        qty = min(max(int(body.get("qty") or 1), 1), 50)
        reset_stock = body.get("reset_stock")
        reset_stock = None if reset_stock in (None, "") else max(int(reset_stock), 0)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not drug_id:
        return _err("请先选择一种药品")
    try:
        result = services.dispense_concurrency(drug_id, clients, qty, reset_stock)
        return _ok(result)
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/access_logs")
def api_access_logs():
    """最近的并发访问性能记录（从 access_log 表读，不是前端缓存）。"""
    try:
        rows = dao.recent_access_logs(12)
        return _ok({"rows": [_fmt_row(r) for r in rows],
                    "total": dao.access_log_count()})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 药房业务：药品目录维护 + 业务档案
# ------------------------------------------------------------------
# 面向业务：界面上只有「药品名称」「供应商档案」这类业务对象，
# 表名与列名由系统内部生成（archive_xxx / f1..fn），用户无需也不应感知。
# 标识符无法参数化，统一走正则白名单校验；值全部 %s 参数化传入。
# =====================================================================
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ARCHIVE_PREFIX = "ledger_"
CORE_TABLES = {"department", "doctor", "patient", "schedule", "appointment",
               "visit", "prescription", "drug", "prescription_item", "payment"}

# 药品台账可选的字段类型：key → (界面文案, 物理类型)
ARCHIVE_TYPES = {
    "text":  ("文本", "VARCHAR(100)"),
    "long":  ("长文本", "VARCHAR(255)"),
    "int":   ("整数", "INT"),
    "money": ("金额", "DECIMAL(10,2)"),
    "date":  ("日期", "DATE"),
}

# 台账模板：药房里真实在用的四类登记，建表时一键带出字段
LEDGER_TEMPLATES = [
    {"key": "adr", "name": "药品不良反应登记",
     "note": "患者用药后出现的不良反应与处理",
     "fields": [
         {"label": "反应表现", "type": "long", "required": True},
         {"label": "发生日期", "type": "date", "required": True},
         {"label": "严重程度", "type": "text"},
         {"label": "处理措施", "type": "long"},
     ]},
    {"key": "stock", "name": "药品库存盘点",
     "note": "账面数量与实盘数量对照，核对损耗",
     "fields": [
         {"label": "盘点日期", "type": "date", "required": True},
         {"label": "账面数量", "type": "int", "required": True},
         {"label": "实盘数量", "type": "int", "required": True},
         {"label": "盘点人", "type": "text"},
     ]},
    {"key": "price", "name": "药品调价记录",
     "note": "每次调价留痕，方便追溯价格变动",
     "fields": [
         {"label": "调整日期", "type": "date", "required": True},
         {"label": "原单价", "type": "money", "required": True},
         {"label": "新单价", "type": "money", "required": True},
         {"label": "调整原因", "type": "text"},
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


def _fmt_value(v):
    """日期/时间转成界面友好的字符串。"""
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
    }
    msg = msgs.get(code, "数据库错误（%s）：%s" % (code, str(e)))
    if sql:
        msg += "\n执行的 SQL：" + sql
    return msg


# ---------------------------------------------------------------------
# 药品目录：新药入库（INSERT）/ 信息维护（UPDATE）/ 停用删除（DELETE）
# ---------------------------------------------------------------------
DRUG_FIELDS = [
    {"key": "name", "label": "药品名称", "type": "text", "required": True},
    {"key": "spec", "label": "规格", "type": "text"},
    {"key": "unit", "label": "单位", "type": "text"},
    {"key": "maker", "label": "生产厂家", "type": "text"},
    {"key": "price", "label": "单价（元）", "type": "money", "required": True},
    {"key": "stock", "label": "库存数量", "type": "int", "required": True},
    {"key": "rx", "label": "处方药", "type": "bool"},
]


def _drug_payload(body):
    """校验并整理药品表单，返回 (字段字典, 错误信息)。"""
    data = {}
    name = (body.get("name") or "").strip()
    if not name:
        return None, "请填写药品名称"
    if len(name) > 100:
        return None, "药品名称不能超过 100 字"
    data["drug_name"] = name
    data["spec"] = (body.get("spec") or "").strip() or None
    data["unit"] = (body.get("unit") or "").strip() or None
    data["manufacturer"] = (body.get("maker") or "").strip() or None

    try:
        price = float(body.get("price"))
    except (TypeError, ValueError):
        return None, "单价请填写数字"
    if price < 0:
        return None, "单价不能为负数"
    data["price"] = round(price, 2)

    try:
        stock = int(body.get("stock"))
    except (TypeError, ValueError):
        return None, "库存数量请填写整数"
    if stock < 0:
        return None, "库存数量不能为负数"
    data["stock"] = stock

    data["is_rx"] = 1 if body.get("rx") else 0
    return data, None


def _drug_row(r):
    """数据库行 → 业务对象（界面只认这些字段）。"""
    return {
        "id": r["drug_id"],
        "name": r["drug_name"],
        "spec": r["spec"] or "",
        "unit": r["unit"] or "",
        "maker": r["manufacturer"] or "",
        "price": float(r["price"]),
        "stock": r["stock"],
        "rx": bool(r["is_rx"]),
        "status": r["status"],
    }


@bp.route("/api/pharmacy/drugs")
def api_drugs():
    q = (request.args.get("q") or "").strip()
    sql = ("SELECT drug_id, drug_name, spec, unit, manufacturer, price, stock, is_rx, status "
           "FROM drug")
    args = []
    if q:
        sql += " WHERE drug_name LIKE %s OR manufacturer LIKE %s"
        args = ["%" + q + "%", "%" + q + "%"]
    sql += " ORDER BY drug_id DESC LIMIT 200"
    try:
        with get_cursor() as cur:
            cur.execute(sql, args)
            rows = [_drug_row(r) for r in cur.fetchall()]
        return _ok({"rows": rows, "fields": DRUG_FIELDS,
                    "total": len(rows), "keyword": q})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/pharmacy/drug-options")
def api_drug_options():
    """并发发药的下拉候选：全部在用药品（含当前库存）。"""
    try:
        rows = dao.query_all(
            "SELECT drug_id AS id, drug_name AS name, spec, unit, stock "
            "FROM drug WHERE status = 1 ORDER BY drug_name")
        return _ok({"rows": rows, "total": len(rows)})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/pharmacy/drugs", methods=["POST"])
def api_drug_create():
    body = request.get_json(silent=True) or {}
    data, err = _drug_payload(body)
    if err:
        return _err(err)
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT drug_id FROM drug WHERE drug_name = %s "
                "AND IFNULL(spec, '') = IFNULL(%s, '') LIMIT 1",
                (data["drug_name"], data["spec"]))
            if cur.fetchone():
                return _err("药品目录中已有同名同规格的品种，请直接在原记录上调整库存或价格")
            cols = list(data.keys())
            sql = ("INSERT INTO drug (%s) VALUES (%s)"
                   % (", ".join(cols), ", ".join(["%s"] * len(cols))))
            cur.execute(sql, [data[c] for c in cols])
            new_id = cur.lastrowid
        return _ok({"id": new_id, "message": "已入库：%s" % data["drug_name"], "sql": sql})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/pharmacy/drugs/<int:drug_id>", methods=["POST"])
def api_drug_update(drug_id):
    body = request.get_json(silent=True) or {}
    data, err = _drug_payload(body)
    if err:
        return _err(err)
    if "status" in body:
        data["status"] = 1 if body.get("status") else 0
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT drug_id FROM drug WHERE drug_id = %s", (drug_id,))
            if not cur.fetchone():
                return _err("该药品已不存在，请刷新后重试")
            cols = list(data.keys())
            sql = ("UPDATE drug SET %s WHERE drug_id = %%s"
                   % ", ".join("%s = %%s" % c for c in cols))
            cur.execute(sql, [data[c] for c in cols] + [drug_id])
        return _ok({"message": "已保存：%s" % data["drug_name"], "sql": sql})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/pharmacy/drugs/<int:drug_id>/remove", methods=["POST"])
def api_drug_delete(drug_id):
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT drug_name FROM drug WHERE drug_id = %s", (drug_id,))
            row = cur.fetchone()
            if not row:
                return _err("该药品已不存在，请刷新后重试")
            cur.execute("SELECT COUNT(*) AS c FROM prescription_item WHERE drug_id = %s",
                        (drug_id,))
            used = cur.fetchone()["c"]
            if used:
                return _err("「%s」已产生 %d 条处方记录，不能删除。"
                            "如不再使用，请在编辑中把状态改为停用。" % (row["drug_name"], used))
            # 台账通过外键指向 drug，登记过的药品同样不能删（这里提前给出可读提示）
            ledgers = []
            for t in _ledger_tables(cur):
                cur.execute("SELECT COUNT(*) AS c FROM `%s` WHERE drug_id = %%s" % t, (drug_id,))
                if cur.fetchone()["c"]:
                    ledgers.append(_archive_meta(cur, t)["name"])
            if ledgers:
                return _err("「%s」已在台账「%s」中登记，不能删除。"
                            "如不再使用，请在编辑中把状态改为停用。"
                            % (row["drug_name"], "、".join(ledgers)))
            cur.execute("DELETE FROM drug WHERE drug_id = %s", (drug_id,))
        return _ok({"message": "已从药品目录删除：%s" % row["drug_name"],
                    "sql": "DELETE FROM drug WHERE drug_id = %s"})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# ---------------------------------------------------------------------
# 药品台账：新建台账（CREATE TABLE + 外键）/ 录入记录（INSERT）/ 删除台账（DROP TABLE）
# 每张台账都带 drug_id 外键指向药品目录，录入时先选药，台账永远是药品的子表。
# ---------------------------------------------------------------------
def _archive_meta(cur, table):
    """读取台账的业务定义：台账名与字段名都来自数据表的注释。"""
    _check_ident(table)
    if not table.startswith(_ARCHIVE_PREFIX):
        raise BizError("该台账不存在")
    cur.execute(
        "SELECT table_comment FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    head = cur.fetchone()
    if not head:
        raise BizError("该台账不存在")
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


def _ledger_tables(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name LIKE 'ledger\\_%' "
        "ORDER BY create_time DESC")
    return [r.get("TABLE_NAME") or r.get("table_name") for r in cur.fetchall()]


@bp.route("/api/archives")
def api_archives():
    try:
        with get_cursor() as cur:
            items = []
            for t in _ledger_tables(cur):
                meta = _archive_meta(cur, t)
                cur.execute("SELECT COUNT(*) AS c, COUNT(DISTINCT drug_id) AS d "
                            "FROM `%s`" % t)
                row = cur.fetchone()
                meta["count"] = row["c"]
                meta["drug_count"] = row["d"]
                items.append(meta)
            # 台账录入时只能从药品目录里选，这里一次性把候选带过去
            cur.execute("SELECT drug_id AS id, drug_name AS name, spec, unit "
                        "FROM drug WHERE status = 1 ORDER BY drug_name")
            drugs = cur.fetchall()
        return _ok({"items": items, "drugs": drugs, "templates": LEDGER_TEMPLATES,
                    "types": [{"key": k, "label": v[0]} for k, v in ARCHIVE_TYPES.items()]})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/archives", methods=["POST"])
def api_archive_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _err("请填写台账名称")
    if len(name) > 30:
        return _err("台账名称不能超过 30 字")
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
        t = ARCHIVE_TYPES.get((f.get("type") or "").strip())
        if not t:
            return _err("字段「%s」的类型不支持" % label)
        cols.append("f%d %s%s COMMENT '%s'" % (
            i, t[1], " NOT NULL" if f.get("required") else "", label))

    table = _ARCHIVE_PREFIX + uuid.uuid4().hex[:10]
    # drug_id 是台账与药品目录的关联列，建表时自动带上并建外键，
    # 台账因此永远是 drug 的子表：录入必须先选药，药品被引用时也删不掉。
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
                return _err("已存在同名药品台账：%s" % name)
            cur.execute(sql)
        return _ok({"table": table, "name": name, "message": "药品台账「%s」已启用" % name,
                    "sql": sql})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/archives/<table>/records")
def api_archive_records(table):
    try:
        with get_cursor() as cur:
            meta = _archive_meta(cur, table)
            # 台账只存 drug_id，界面要显示药名，所以这里 JOIN 药品目录
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


@bp.route("/api/archives/<table>/records", methods=["POST"])
def api_archive_insert(table):
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    try:
        with get_cursor(commit=True) as cur:
            meta = _archive_meta(cur, table)
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
        return _ok({"message": "已为「%s」登记 1 条台账记录" % drug["drug_name"], "sql": sql})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/archives/<table>/remove", methods=["POST"])
def api_archive_drop(table):
    try:
        with get_cursor(commit=True) as cur:
            meta = _archive_meta(cur, table)
            cur.execute("SELECT COUNT(*) AS c FROM `%s`" % table)
            n = cur.fetchone()["c"]
            if n and not (request.get_json(silent=True) or {}).get("force"):
                return _err("该台账下还有 %d 条记录，确认要连同记录一起删除吗？" % n,
                            need_confirm=True, count=n)
            cur.execute("DROP TABLE `%s`" % table)
        return _ok({"message": "药品台账「%s」已删除" % meta["name"],
                    "sql": "DROP TABLE `%s`" % table})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/ops")
def ops_page():
    return render_template("ops.html")


# =====================================================================
# 跨数据表操作：四张只读 JOIN 报表，其中三张以药品为中心
# =====================================================================
REPORTS = {
    "doctor_revenue": {
        "name": "医生开药金额 Top10（5 表 JOIN）",
        "desc": "prescription_item → prescription → visit → doctor → department",
    },
    "drug_consume": {
        "name": "药品消耗排行（6 表 JOIN）",
        "desc": "prescription_item → prescription → visit → doctor → department → drug",
    },
    "drug_ledger": {
        "name": "药品台账总览（台账 JOIN 药品）",
        "desc": "ledger_xxx → drug",
    },
    "patient_flow": {
        "name": "患者完整就诊流水（7 表 JOIN）",
        "desc": "appointment → patient → schedule → doctor → department → visit → prescription_item → drug",
    },
}


def _report_drug_ledger(cur, table):
    """台账里只存 drug_id，这里 JOIN 药品目录把编码翻译成药名，
    并顺带统计本台账覆盖了多少种药。"""
    tables = _ledger_tables(cur)
    if not tables:
        return [], [], True
    table = table if table in tables else tables[0]
    meta = _archive_meta(cur, table)
    cols = ", ".join("l.`%s`" % f["key"] for f in meta["fields"])
    cur.execute(
        "SELECT g.drug_name AS 药品, g.spec AS 规格, g.manufacturer AS 生产厂家, "
        "%s, l.created_at AS 录入时间 "
        "FROM `%s` l JOIN drug g ON g.drug_id = l.drug_id "
        "ORDER BY l.id DESC LIMIT 30" % (cols, table))
    rows = [_fmt_row(r) for r in cur.fetchall()]
    # 用业务字段名替换 f1/f2，界面直接可读
    rename = {f["key"]: f["label"] for f in meta["fields"]}
    fixed = []
    for r in rows:
        fixed.append({rename.get(k, k): v for k, v in r.items()})
    return fixed, list(fixed[0].keys()) if fixed else [], False


@bp.route("/api/report", methods=["POST"])
def api_report():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name not in REPORTS:
        return _err("未知报表")
    try:
        empty = False
        if name == "doctor_revenue":
            rows = dao.report_doctor_revenue(10)
        elif name == "drug_consume":
            rows = dao.report_drug_consume(15)
        elif name == "drug_ledger":
            with get_cursor() as cur:
                rows, cols, empty = _report_drug_ledger(cur, (body.get("table") or "").strip())
            return _ok({"rows": rows, "columns": cols, "count": len(rows), "empty": empty})
        else:
            pid = int(body.get("patient_id") or 1)
            rows = dao.report_patient_flow(pid, 20)
        return _ok({"rows": [_fmt_row(r) for r in rows],
                    "columns": list(rows[0].keys()) if rows else [],
                    "count": len(rows)})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# =====================================================================
# 索引支持：同一查询在「走索引」与「强制绕过索引」两种情况下对比
# =====================================================================
PERF_CASES = {
    "appointment_patient": {
        "title": "按患者查预约记录（appointment，4 万行）",
        "table": "appointment",
        "index": "idx_appt_patient",
        "with_sql": "SELECT SQL_NO_CACHE a.appt_id, a.appt_time, a.queue_no, a.status "
                    "FROM appointment a{ignore} WHERE a.patient_id = %s",
        "ignore": ["idx_appt_patient", "idx_appt_patient_time"],
        "param": "patient_id",
    },
    "visit_doctor": {
        "title": "按医生统计就诊量（visit，3 万行）",
        "table": "visit",
        "index": "idx_visit_doctor",
        "with_sql": "SELECT SQL_NO_CACHE v.doctor_id, COUNT(*) AS cnt FROM visit v{ignore} "
                    "WHERE v.doctor_id = %s GROUP BY v.doctor_id",
        "ignore": ["idx_visit_doctor"],
        "param": "doctor_id",
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

    if case["param"] == "patient_id":
        param = int(body.get("patient_id") or 1234)
        args = (param,)
    else:
        param = int(body.get("doctor_id") or 5)
        args = (param,)

    repeat = min(int(body.get("repeat") or 5), 20)

    with get_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (case["table"],),
        )
        existing = {r["INDEX_NAME"] or r["index_name"] for r in cur.fetchall()}
        index_exists = case["index"] in existing

    try:
        # 只忽略真实存在的索引（缺 index.sql 时也能跑，只是对比不明显）
        ignored = [i for i in case["ignore"] if i in existing]
        ignore_clause = (" IGNORE INDEX (" + ", ".join(ignored) + ")") if ignored else ""
        without_sql = case["with_sql"].replace("{ignore}", ignore_clause)
        with get_cursor() as cur:
            plan_without = _explain(cur, without_sql, args)
            time_without = _timed(cur, without_sql, args, repeat)
        with get_cursor() as cur:
            with_sql = case["with_sql"].replace("{ignore}", "")
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
