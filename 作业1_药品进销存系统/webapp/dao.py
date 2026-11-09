# -*- coding: utf-8 -*-
"""
DAO 层：所有 SQL 语句集中在这里
------------------------------------------------------------------
分层：views(页面) → services(业务事务) → dao(SQL) → db(连接)
好处：SQL 与页面解耦，报告里可以直接引用本文件作为"实现代码"。
"""
from .db import get_cursor


# =====================================================================
# 通用查询封装
# =====================================================================
def query_all(sql, args=None):
    with get_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def query_one(sql, args=None):
    with get_cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchone()


def execute(sql, args=None):
    """执行单条写语句，返回受影响行数。"""
    with get_cursor(commit=True) as cur:
        cur.execute(sql, args or ())
        return cur.rowcount


# =====================================================================
# 访问性能日志（多客户端并发访问时逐条记录响应耗时）
# =====================================================================
def log_access_batch(rows):
    """rows: [(scene, client_no, drug_id, qty, ok, elapsed_ms), ...] 一次批量写入。"""
    if not rows:
        return 0
    with get_cursor(commit=True) as cur:
        cur.executemany(
            "INSERT INTO access_log (scene, client_no, drug_id, qty, ok, elapsed_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s)", rows)
        return cur.rowcount


def recent_access_logs(limit=12):
    return query_all(
        "SELECT l.log_id, l.client_no, l.drug_id, l.qty, l.ok, l.elapsed_ms, l.created_at, "
        "d.drug_name, d.spec "
        "FROM access_log l LEFT JOIN drug d ON d.drug_id = l.drug_id "
        "ORDER BY l.log_id DESC LIMIT %s", [limit])


def access_log_count():
    return query_one("SELECT COUNT(*) AS c FROM access_log")["c"]


def server_info():
    """取服务器版本 / 当前连接身份，用于证明"连的是远程 Linux 服务器"。"""
    return query_one(
        "SELECT VERSION() AS version, CURRENT_USER() AS db_user, "
        "@@hostname AS hostname, @@port AS port, @@datadir AS datadir"
    )


# =====================================================================
# 基础资料
# =====================================================================
def list_drugs(keyword=None, limit=200):
    sql = ("SELECT d.drug_id AS id, d.drug_code AS code, d.drug_name AS name, "
           "d.spec, d.unit, d.manufacturer AS maker, d.purchase_price AS pp, "
           "d.retail_price AS rp, d.is_rx AS rx, d.status, d.category_id, "
           "c.category_name AS cate, COALESCE(SUM(s.quantity), 0) AS stock "
           "FROM drug d "
           "JOIN drug_category c ON c.category_id = d.category_id "
           "LEFT JOIN stock s ON s.drug_id = d.drug_id ")
    args = []
    if keyword:
        sql += ("WHERE d.drug_name LIKE %s OR d.drug_code LIKE %s "
                "OR d.manufacturer LIKE %s ")
        args = ["%" + keyword + "%"] * 3
    sql += ("GROUP BY d.drug_id, d.drug_code, d.drug_name, d.spec, d.unit, d.manufacturer, "
            "d.purchase_price, d.retail_price, d.is_rx, d.status, d.category_id, "
            "c.category_name ORDER BY d.drug_id DESC LIMIT %s")
    args.append(limit)
    return query_all(sql, args)


def drug_options():
    """下拉候选：在售药品 + 各仓库库存。

    不设 LIMIT：新加的药品按名称排序后可能排在最后，
    之前写死 LIMIT 1200 会把它切掉，导致采购 / 销售下拉里找不到。
    （1200 种药品的下拉数据量很小，前端还带关键字筛选，不必截断。）
    """
    return query_all(
        "SELECT d.drug_id AS id, d.drug_name AS name, d.spec, d.unit, "
        "d.purchase_price AS pp, d.retail_price AS rp, d.category_id, "
        "COALESCE(SUM(s.quantity), 0) AS stock "
        "FROM drug d LEFT JOIN stock s ON s.drug_id = d.drug_id "
        "WHERE d.status = 1 "
        "GROUP BY d.drug_id, d.drug_name, d.spec, d.unit, d.purchase_price, "
        "         d.retail_price, d.category_id "
        "ORDER BY d.drug_name")


def list_categories():
    return query_all(
        "SELECT category_id AS id, category_name AS name FROM drug_category "
        "WHERE status = 1 ORDER BY category_id")


def list_suppliers():
    return query_all(
        "SELECT supplier_id AS id, supplier_name AS name, credit_level AS credit, "
        "contact_person AS contact FROM supplier WHERE status = 1 ORDER BY supplier_id")


def list_warehouses():
    return query_all(
        "SELECT warehouse_id AS id, warehouse_name AS name, location, capacity "
        "FROM warehouse WHERE status = 1 ORDER BY warehouse_id")


def list_employees(position=None):
    sql = ("SELECT e.employee_id AS id, e.emp_name AS name, e.position, "
           "e.warehouse_id AS wh_id, w.warehouse_name AS wh "
           "FROM employee e JOIN warehouse w ON w.warehouse_id = e.warehouse_id "
           "WHERE e.status = 1 ")
    args = []
    if position:
        sql += "AND e.position = %s "
        args.append(position)
    sql += "ORDER BY e.employee_id"
    return query_all(sql, args)


# =====================================================================
# 单据列表
# =====================================================================
def recent_purchases(limit=15):
    """最近的采购单，带供应商、仓库、经办人（4 表 JOIN）。"""
    return query_all(
        "SELECT po.po_no AS 单号, s.supplier_name AS 供应商, w.warehouse_name AS 仓库, "
        "e.emp_name AS 采购员, po.order_date AS 下单日期, po.status AS 状态, "
        "COUNT(pi.item_id) AS 品种数, "
        "ROUND(COALESCE(SUM(pi.amount),0),2) AS 金额 "
        "FROM purchase_order po "
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        "JOIN warehouse w ON w.warehouse_id = po.warehouse_id "
        "JOIN employee e ON e.employee_id = po.employee_id "
        "LEFT JOIN purchase_item pi ON pi.po_id = po.po_id "
        "GROUP BY po.po_id, po.po_no, s.supplier_name, w.warehouse_name, e.emp_name, "
        "         po.order_date, po.status "
        "ORDER BY po.order_date DESC, po.po_id DESC LIMIT %s", [limit])


def recent_sales(limit=15):
    """最近的销售单（4 表 JOIN）。"""
    return query_all(
        "SELECT so.so_no AS 单号, so.customer_name AS 客户, so.customer_type AS 类型, "
        "w.warehouse_name AS 仓库, e.emp_name AS 销售员, so.sale_date AS 销售日期, "
        "COUNT(si.item_id) AS 品种数, ROUND(COALESCE(SUM(si.amount),0),2) AS 金额 "
        "FROM sale_order so "
        "JOIN warehouse w ON w.warehouse_id = so.warehouse_id "
        "JOIN employee e ON e.employee_id = so.employee_id "
        "LEFT JOIN sale_item si ON si.so_id = so.so_id "
        "GROUP BY so.so_id, so.so_no, so.customer_name, so.customer_type, "
        "         w.warehouse_name, e.emp_name, so.sale_date "
        "ORDER BY so.sale_date DESC, so.so_id DESC LIMIT %s", [limit])


def stock_table(limit=100):
    """库存台账：药品 × 仓库，带分类与近效期批次（4 表 + 子查询）。"""
    return query_all(
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, c.category_name AS 分类, "
        "w.warehouse_name AS 仓库, s.quantity AS 结存, s.safety_stock AS 安全库存, "
        "s.update_time AS 更新时间, "
        "(SELECT COUNT(*) FROM stock_batch b WHERE b.drug_id = s.drug_id "
        " AND b.warehouse_id = s.warehouse_id AND b.quantity > 0) AS 在库批次 "
        "FROM stock s "
        "JOIN drug dr ON dr.drug_id = s.drug_id "
        "JOIN drug_category c ON c.category_id = dr.category_id "
        "JOIN warehouse w ON w.warehouse_id = s.warehouse_id "
        "ORDER BY s.quantity DESC LIMIT %s", [limit])


# =====================================================================
# 跨表报表
# =====================================================================
def report_supplier_rank(limit=10):
    """供应商供货排行：purchase_item → purchase_order → supplier → warehouse → employee（5 表）"""
    return query_all(
        "SELECT s.supplier_name AS 供应商, s.credit_level AS 信用等级, "
        "COUNT(DISTINCT po.po_id) AS 采购单数, COUNT(pi.item_id) AS 采购品种, "
        "SUM(pi.quantity) AS 采购总量, ROUND(SUM(pi.amount), 2) AS 采购金额, "
        "MAX(w.warehouse_name) AS 主要入库仓 "
        "FROM purchase_item pi "
        "JOIN purchase_order po ON po.po_id = pi.po_id "
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        "JOIN warehouse w ON w.warehouse_id = po.warehouse_id "
        "JOIN employee e ON e.employee_id = po.employee_id "
        "GROUP BY s.supplier_id, s.supplier_name, s.credit_level "
        "ORDER BY 采购金额 DESC LIMIT %s", [limit])


def report_drug_sales(limit=15):
    """药品销售排行：sale_item → sale_order → drug → drug_category → warehouse（5 表）"""
    return query_all(
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, c.category_name AS 分类, "
        "w.warehouse_name AS 仓库, SUM(si.quantity) AS 销售数量, "
        "ROUND(SUM(si.amount), 2) AS 销售额, COUNT(DISTINCT so.so_id) AS 单据数 "
        "FROM sale_item si "
        "JOIN sale_order so ON so.so_id = si.so_id "
        "JOIN drug dr ON dr.drug_id = si.drug_id "
        "JOIN drug_category c ON c.category_id = dr.category_id "
        "JOIN warehouse w ON w.warehouse_id = so.warehouse_id "
        "GROUP BY dr.drug_id, dr.drug_name, dr.spec, c.category_name, w.warehouse_name "
        "ORDER BY 销售额 DESC LIMIT %s", [limit])


def report_batch_chain(limit=20):
    """近效期批次溯源：stock_batch → drug → warehouse → purchase_item → purchase_order → supplier（6 表）"""
    return query_all(
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, w.warehouse_name AS 仓库, "
        "sb.batch_no AS 批号, sb.quantity AS 结存, sb.expiry_date AS 有效期至, "
        "DATEDIFF(sb.expiry_date, CURDATE()) AS 剩余天数, "
        "pi.unit_price AS 采购单价, po.po_no AS 来源采购单, s.supplier_name AS 供应商 "
        "FROM stock_batch sb "
        "JOIN drug dr ON dr.drug_id = sb.drug_id "
        "JOIN warehouse w ON w.warehouse_id = sb.warehouse_id "
        "JOIN purchase_item pi ON pi.item_id = sb.purchase_item_id "
        "JOIN purchase_order po ON po.po_id = pi.po_id "
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        "WHERE sb.quantity > 0 AND sb.expiry_date < DATE_ADD(CURDATE(), INTERVAL 180 DAY) "
        "ORDER BY sb.expiry_date LIMIT %s", [limit])


# 单药品出入库全链路 SQL（7 表 JOIN）。{ignore} 占位符供「索引体检」
# 注入 IGNORE INDEX 强制全表扫描，与正常业务查询形成对照。
TRACE_SQL = (
    "SELECT m.move_time AS 发生时间, m.move_type AS 类型, "
    "dr.drug_name AS 药品, w.warehouse_name AS 仓库, "
    "m.change_qty AS 变动, m.before_qty AS 变动前, m.after_qty AS 变动后, "
    "m.batch_no AS 批号, e.emp_name AS 经办人, m.ref_no AS 关联单号, "
    "COALESCE(so.customer_name, sp.supplier_name, '—') AS 往来单位 "
    "FROM stock_movement m{ignore} "
    "JOIN drug dr ON dr.drug_id = m.drug_id "
    "JOIN warehouse w ON w.warehouse_id = m.warehouse_id "
    "JOIN employee e ON e.employee_id = m.employee_id "
    "LEFT JOIN sale_order so ON so.so_no = m.ref_no "
    "LEFT JOIN purchase_order po ON po.po_no = m.ref_no "
    "LEFT JOIN supplier sp ON sp.supplier_id = po.supplier_id "
    "WHERE m.drug_id = %s "
    "ORDER BY m.move_time DESC, m.move_id DESC LIMIT %s")


def report_drug_trace(drug_id, limit=20):
    """单药品出入库全链路：stock_movement → drug → warehouse → employee
       + LEFT JOIN sale_order / purchase_order / supplier（7 表）"""
    return query_all(TRACE_SQL.replace("{ignore}", ""), [drug_id, limit])
