# -*- coding: utf-8 -*-
"""
Views 层：页面路由与 Ajax 接口
------------------------------------------------------------------
对应课程要求的 B/S 结构：浏览器（客户端）→ Flask（本机）→ 通过 TCP/IP
访问 Linux 服务器上的 MySQL。页面顶部固定显示当前连接的目标服务器地址，
方便答辩时直接证明"数据不在本机"。
"""
import re
import time
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
    try:
        rows, total = dao.table_row_counts()
        db_ok, db_err = True, None
    except Exception as e:                                     # noqa: BLE001
        rows, total = [], 0
        db_ok, db_err = False, str(e)
    return render_template("dashboard.html", rows=rows, total=total,
                           db_ok=db_ok, db_err=db_err)


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


@bp.route("/api/concurrency_demo", methods=["POST"])
def api_concurrency_demo():
    """多线程并发抢号，证明不会超卖。"""
    body = request.get_json(silent=True) or {}
    try:
        schedule_id = int(body.get("schedule_id") or 0)
        thread_count = min(int(body.get("thread_count") or 10), 30)
    except (TypeError, ValueError):
        return _err("参数格式不正确")
    if not schedule_id:
        return _err("请选择一个排班")
    t0 = time.time()
    try:
        result = services.concurrency_register_demo(schedule_id, thread_count)
        result["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
        return _ok(result)
    except BizError as e:
        return _err(str(e))


# =====================================================================
# 数据管理台：用户亲自建表 / 插入 / 修改 / 删除 / 删表 / 跨表查询
# ------------------------------------------------------------------
# 标识符（表名/列名）无法参数化，统一走正则白名单校验后再拼入 SQL；
# 值全部用 %s 参数化传入；删表只允许删自建表，业务表受保护。
# =====================================================================
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TYPE_MAP = {
    "INT": "INT",
    "TINYINT": "TINYINT",
    "DECIMAL(10,2)": "DECIMAL(10,2)",
    "VARCHAR(50)": "VARCHAR(50)",
    "VARCHAR(100)": "VARCHAR(100)",
    "VARCHAR(255)": "VARCHAR(255)",
    "TEXT": "TEXT",
    "DATE": "DATE",
    "DATETIME": "DATETIME",
}

BUSINESS_TABLES = {"drug": "药品目录", "patient": "患者档案"}
CORE_TABLES = {"department", "doctor", "patient", "schedule", "appointment",
               "visit", "prescription", "drug", "prescription_item", "payment"}


def _check_ident(name):
    if not name or not _IDENT.match(name):
        raise BizError("名称不合法：%s（只允许字母、数字、下划线，且以字母或下划线开头）" % name)


def _list_tables(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
        "ORDER BY table_name")
    return [r.get("TABLE_NAME") or r.get("table_name") for r in cur.fetchall()]


def _table_meta(cur, table):
    """列元数据：列名 / 类型 / 是否可空 / 是否主键 / 是否自增。"""
    _check_ident(table)
    cur.execute(
        "SELECT column_name, column_type, is_nullable, column_key, extra "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position",
        (table,))
    cols = []
    for r in cur.fetchall():
        name = r.get("COLUMN_NAME") or r.get("column_name")
        cols.append({
            "name": name,
            "type": (r.get("COLUMN_TYPE") or r.get("column_type") or "").upper(),
            "nullable": (r.get("IS_NULLABLE") or r.get("is_nullable")) == "YES",
            "pk": (r.get("COLUMN_KEY") or r.get("column_key") or "") == "PRI",
            "auto": "auto_increment" in (r.get("EXTRA") or r.get("extra") or "").lower(),
        })
    if not cols:
        raise BizError("表 %s 不存在" % table)
    pk = next((c["name"] for c in cols if c["pk"]), cols[0]["name"])
    return cols, pk


def _clean_values(cols, raw):
    """按真实列过滤提交的值；可空列留空按 NULL 处理。"""
    by_name = {c["name"]: c for c in cols}
    unknown = [k for k in raw if k not in by_name]
    if unknown:
        raise BizError("未知列：%s" % ", ".join(unknown))
    out = {}
    for k, v in raw.items():
        c = by_name[k]
        if c["auto"]:
            continue
        if v is None or (isinstance(v, str) and v.strip() == ""):
            out[k] = None if c["nullable"] else ""
        else:
            out[k] = v
    return out


@bp.route("/ops")
def ops_page():
    return render_template("ops.html")


@bp.route("/api/crud/tables")
def api_crud_tables():
    with get_cursor() as cur:
        tables = _list_tables(cur)
    business = [{"name": t, "cn": BUSINESS_TABLES[t]} for t in tables if t in BUSINESS_TABLES]
    mine = [t for t in tables if t not in CORE_TABLES]
    return _ok({"business": business, "mine": mine})


@bp.route("/api/crud/table")
def api_crud_table():
    table = request.args.get("name") or ""
    try:
        with get_cursor() as cur:
            cols, pk = _table_meta(cur, table)
            cur.execute("SELECT * FROM `{}` ORDER BY `{}` DESC LIMIT 50".format(table, pk))
            rows = cur.fetchall()
        return _ok({"columns": cols, "pk": pk, "rows": rows})
    except BizError as e:
        return _err(str(e))


@bp.route("/api/crud/create_table", methods=["POST"])
def api_crud_create_table():
    body = request.get_json(silent=True) or {}
    table = (body.get("name") or "").strip()
    sql = None
    try:
        _check_ident(table)
        raw_cols = body.get("columns") or []
        if len(raw_cols) < 1:
            return _err("至少需要一个字段")
        defs = []
        seen = set()
        for c in raw_cols:
            cname = (c.get("name") or "").strip()
            _check_ident(cname)
            if cname.lower() == "id":
                return _err("字段名不能叫 id（系统自动生成主键 id）")
            if cname in seen:
                return _err("字段重复：%s" % cname)
            seen.add(cname)
            t = _TYPE_MAP.get(c.get("type") or "")
            if not t:
                return _err("不支持的字段类型：%s" % c.get("type"))
            defs.append("`%s` %s%s" % (cname, t, " NOT NULL" if c.get("notnull") else ""))
        sql = ("CREATE TABLE `{}` (id INT AUTO_INCREMENT PRIMARY KEY, {}) "
               "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4".format(table, ", ".join(defs)))
        with get_cursor(commit=True) as cur:
            cur.execute(sql)
        return _ok({"sql": sql, "message": "表 {} 已创建".format(table)})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e, sql))


@bp.route("/api/crud/drop_table", methods=["POST"])
def api_crud_drop_table():
    body = request.get_json(silent=True) or {}
    table = body.get("table") or ""
    try:
        _check_ident(table)
        if table in CORE_TABLES:
            return _err("业务表 {} 受保护，只能删除自己创建的表".format(table))
        sql = "DROP TABLE IF EXISTS `{}`".format(table)
        with get_cursor(commit=True) as cur:
            cur.execute(sql)
        return _ok({"sql": sql, "message": "表 {} 已删除".format(table)})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/crud/insert", methods=["POST"])
def api_crud_insert():
    body = request.get_json(silent=True) or {}
    table = body.get("table") or ""
    try:
        with get_cursor(commit=True) as cur:
            cols, pk = _table_meta(cur, table)
            values = _clean_values(cols, body.get("values") or {})
            keys = list(values.keys())
            if not keys:
                return _err("没有可写入的字段")
            sql = "INSERT INTO `{}` ({}) VALUES ({})".format(
                table,
                ", ".join("`%s`" % k for k in keys),
                ", ".join(["%s"] * len(keys)))
            args = [values[k] for k in keys]
            cur.execute(sql, args)
            new_id = cur.lastrowid
        return _ok({"sql": sql, "params": args, "id": new_id, "rowcount": 1})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/crud/update", methods=["POST"])
def api_crud_update():
    body = request.get_json(silent=True) or {}
    table = body.get("table") or ""
    try:
        with get_cursor(commit=True) as cur:
            cols, pk = _table_meta(cur, table)
            values = _clean_values(cols, body.get("values") or {})
            keys = list(values.keys())
            if not keys:
                return _err("没有要修改的字段")
            pk_value = body.get("pk_value")
            sql = "UPDATE `{}` SET {} WHERE `{}` = %s".format(
                table, ", ".join("`%s` = %%s" % k for k in keys), pk)
            args = [values[k] for k in keys] + [pk_value]
            cur.execute(sql, args)
        return _ok({"sql": sql, "params": args, "rowcount": cur.rowcount})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/crud/delete", methods=["POST"])
def api_crud_delete():
    body = request.get_json(silent=True) or {}
    table = body.get("table") or ""
    try:
        with get_cursor(commit=True) as cur:
            cols, pk = _table_meta(cur, table)
            sql = "DELETE FROM `{}` WHERE `{}` = %s".format(table, pk)
            args = [body.get("pk_value")]
            cur.execute(sql, args)
        return _ok({"sql": sql, "params": args, "rowcount": cur.rowcount})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


def _friendly(e, sql=None):
    """把常见 MySQL 报错翻译成人话（其余原样返回）。"""
    code = e.args[0] if e.args else None
    msgs = {
        1451: "该记录正被其他表引用（外键约束保护），不能删除。先删除引用它的记录再试。",
        1452: "写入失败：引用的记录不存在（外键约束）。",
        1062: "唯一字段冲突：已有相同主键/唯一值的记录。",
        1050: "该表已存在，换一个表名。",
        1146: "表不存在。",
        1049: "数据库不存在。",
        1406: "数据太长，超出字段长度限制。",
        1265: "数据格式与字段类型不匹配（如向数字字段写了文字）。",
        1292: "日期格式不正确，DATE 请用 2026-01-31 这样的格式。",
    }
    msg = msgs.get(code, "数据库错误（%s）：%s" % (code, str(e)))
    if sql:
        msg += "\n执行的 SQL：" + sql
    return msg


# =====================================================================
# 跨数据表操作：两张固定 JOIN 报表（只读）
# =====================================================================
REPORTS = {
    "doctor_revenue": {
        "name": "医生开药金额 Top10（5 表 JOIN）",
        "desc": "prescription_item → prescription → visit → doctor → department",
    },
    "patient_flow": {
        "name": "患者完整就诊流水（7 表 JOIN）",
        "desc": "appointment → patient → schedule → doctor → department → visit → prescription_item → drug",
    },
}


@bp.route("/api/report", methods=["POST"])
def api_report():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name not in REPORTS:
        return _err("未知报表")
    try:
        if name == "doctor_revenue":
            rows = dao.report_doctor_revenue(10)
        else:
            pid = int(body.get("patient_id") or 1)
            rows = dao.report_patient_flow(pid, 20)
        return _ok({"rows": rows,
                    "columns": list(rows[0].keys()) if rows else [],
                    "count": len(rows)})
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
        "with": {"plan": plan_with, "time": time_with, "sql": case["with_sql"]},
        "speedup": speedup,
    })
