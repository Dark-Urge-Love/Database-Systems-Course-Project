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
# 药房业务：药品目录维护 + 业务档案
# ------------------------------------------------------------------
# 面向业务：界面上只有「药品名称」「供应商档案」这类业务对象，
# 表名与列名由系统内部生成（archive_xxx / f1..fn），用户无需也不应感知。
# 标识符无法参数化，统一走正则白名单校验；值全部 %s 参数化传入。
# =====================================================================
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ARCHIVE_PREFIX = "archive_"
CORE_TABLES = {"department", "doctor", "patient", "schedule", "appointment",
               "visit", "prescription", "drug", "prescription_item", "payment"}

# 业务档案可选的字段类型：key → (界面文案, 物理类型)
ARCHIVE_TYPES = {
    "text":  ("文本", "VARCHAR(100)"),
    "long":  ("长文本", "VARCHAR(255)"),
    "int":   ("整数", "INT"),
    "money": ("金额", "DECIMAL(10,2)"),
    "date":  ("日期", "DATE"),
}


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
            cur.execute("DELETE FROM drug WHERE drug_id = %s", (drug_id,))
        return _ok({"message": "已从药品目录删除：%s" % row["drug_name"],
                    "sql": "DELETE FROM drug WHERE drug_id = %s"})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


# ---------------------------------------------------------------------
# 业务档案：新建档案（CREATE TABLE）/ 录入记录（INSERT）/ 删除档案（DROP TABLE）
# ---------------------------------------------------------------------
def _archive_meta(cur, table):
    """读取档案的业务定义：档案名与字段名都来自数据表的注释。"""
    _check_ident(table)
    if not table.startswith(_ARCHIVE_PREFIX):
        raise BizError("该档案不存在")
    cur.execute(
        "SELECT table_comment FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    head = cur.fetchone()
    if not head:
        raise BizError("该档案不存在")
    cur.execute(
        "SELECT column_name, column_type, column_comment, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s "
        "ORDER BY ordinal_position", (table,))
    fields = []
    for r in cur.fetchall():
        name = r.get("COLUMN_NAME") or r.get("column_name")
        if name in ("id", "created_at"):
            continue
        fields.append({
            "key": name,
            "label": (r.get("COLUMN_COMMENT") or r.get("column_comment")) or name,
            "type": (r.get("COLUMN_TYPE") or r.get("column_type") or "").upper(),
            "required": (r.get("IS_NULLABLE") or r.get("is_nullable")) == "NO",
        })
    return {"table": table, "name": head.get("TABLE_COMMENT") or head.get("table_comment"),
            "fields": fields}


@bp.route("/api/archives")
def api_archives():
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT table_name, table_comment FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name LIKE 'archive\\_%' "
                "ORDER BY create_time DESC")
            items = []
            for r in cur.fetchall():
                t = r.get("TABLE_NAME") or r.get("table_name")
                meta = _archive_meta(cur, t)
                cur.execute("SELECT COUNT(*) AS c FROM `%s`" % t)
                meta["count"] = cur.fetchone()["c"]
                items.append(meta)
        return _ok({"items": items, "types": [
            {"key": k, "label": v[0]} for k, v in ARCHIVE_TYPES.items()]})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/archives", methods=["POST"])
def api_archive_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _err("请填写档案名称")
    if len(name) > 30:
        return _err("档案名称不能超过 30 字")
    raw_fields = body.get("fields") or []
    if not 1 <= len(raw_fields) <= 8:
        return _err("请设置 1~8 个字段")

    cols, labels, seen = [], [], set()
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
        labels.append(label)

    table = _ARCHIVE_PREFIX + uuid.uuid4().hex[:10]
    sql = ("CREATE TABLE `%s` (id INT AUTO_INCREMENT PRIMARY KEY, %s, "
           "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) "
           "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='%s'"
           % (table, ", ".join(cols), name))
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_comment = %s LIMIT 1", (name,))
            if cur.fetchone():
                return _err("已存在同名业务档案：%s" % name)
            cur.execute(sql)
        return _ok({"table": table, "name": name, "message": "业务档案「%s」已启用" % name,
                    "sql": sql})
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/api/archives/<table>/records")
def api_archive_records(table):
    try:
        with get_cursor() as cur:
            meta = _archive_meta(cur, table)
            cur.execute("SELECT * FROM `%s` ORDER BY id DESC LIMIT 50" % table)
            rows = [_fmt_row(r) for r in cur.fetchall()]
        return _ok({"meta": meta, "rows": rows})
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
            keys, args = [], []
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
        return _ok({"message": "已录入 1 条记录", "sql": sql})
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
                return _err("该档案下还有 %d 条记录，确认要连同记录一起删除吗？" % n,
                            need_confirm=True, count=n)
            cur.execute("DROP TABLE `%s`" % table)
        return _ok({"message": "业务档案「%s」已删除" % meta["name"],
                    "sql": "DROP TABLE `%s`" % table})
    except BizError as e:
        return _err(str(e))
    except pymysql.MySQLError as e:
        return _err(_friendly(e))


@bp.route("/ops")
def ops_page():
    return render_template("ops.html")


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
        return _ok({"rows": [_fmt_row(r) for r in rows],
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
