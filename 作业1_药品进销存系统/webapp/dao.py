# -*- coding: utf-8 -*-
#  ↑ 编码声明：本文件用 UTF-8，避免中文乱码

"""
DAO 层：所有 SQL 语句集中在这里
------------------------------------------------------------------
分层：views(页面) → services(业务事务) → dao(SQL) → db(连接)
好处：SQL 与页面解耦，报告里可以直接引用本文件作为"实现代码"。

【知识点：DAO 是什么】
DAO = Data Access Object（数据访问对象）。把所有 SQL 关在一个文件里，
上层（页面、业务逻辑）只调函数、不写 SQL。好处：
  · 改 SQL 只改一处；换个数据库，只动这一层
  · 避免 SQL 散落在各处，越来越乱没法维护
  · 老师检查时，看这一个文件就知道你对数据库做了什么操作
"""

# 从同包的 db 模块导入 get_cursor（只读查询用的上下文管理器）
from .db import get_cursor


# =====================================================================
# 通用查询封装
# =====================================================================
# 【知识点：为什么不直接写 cur.execute，而是包三个小函数】
# 因为 90% 的查询都是"执行一句 SQL，取全部行"或"取一行"或"改一下"。
# 封装成三个动词，上层代码就变成 query_all(...)、execute(...)，短且意图清楚。
def query_all(sql, args=None):
    # with 语句会在代码块结束后自动关闭游标和连接（见 db.py）
    with get_cursor() as cur:

        # 【知识点：args or ()】如果 args 是 None（没传参数），就换成空元组 ()。
        # 第二个参数必须是元组/列表，即使没有参数也得给个空的，否则驱动会报错
        cur.execute(sql, args or ())

        # fetchall() 取回全部结果行。因为用了 DictCursor，每行是 {"列名": 值} 的字典
        return cur.fetchall()


def query_one(sql, args=None):
    # 同样开一个只读游标
    with get_cursor() as cur:

        # 执行 SQL，参数化占位符由驱动负责转义（防注入的关键，见下面说明）
        cur.execute(sql, args or ())

        # fetchone() 只取第一行；没有结果时返回 None
        return cur.fetchone()


def execute(sql, args=None):
    """执行单条写语句，返回受影响行数。"""

    # commit=True：这条语句执行完就提交（单条写语句不需要显式事务）
    with get_cursor(commit=True) as cur:

        # 执行 INSERT / UPDATE / DELETE
        cur.execute(sql, args or ())

        # rowcount 是"受影响的行数"，比如 UPDATE 改了 3 行就返回 3；
        # 页面可以据此提示"修改成功 1 条"或"没找到这条记录"
        return cur.rowcount


# =====================================================================
# 访问性能日志（多客户端并发访问时逐条记录响应耗时）
# =====================================================================
def log_access_batch(rows):
    """rows: [(scene, client_no, drug_id, qty, ok, elapsed_ms), ...] 一次批量写入。"""

    # 空列表直接返回，省得白跑一趟数据库
    if not rows:
        return 0

    # 要写数据，所以 commit=True
    with get_cursor(commit=True) as cur:

        # 【知识点：executemany 批量插入】
        # 普通 execute 一次插一行；executemany 接收"列表套元组"，
        # 把 N 行用一条多值 INSERT 发过去，比循环 N 次快得多。
        # SQL 里的 6 个 %s 会和每个元组里的 6 个值一一对应
        cur.executemany(
            "INSERT INTO access_log (scene, client_no, drug_id, qty, ok, elapsed_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s)", rows)

        # 返回一共插了多少行
        return cur.rowcount


def recent_access_logs(limit=12):
    # 调 query_all 拿多行；参数用列表 [limit] 传入，对应 SQL 里的 LIMIT %s
    return query_all(
        # 【知识点：别名 AS】把列名简化成短名字，前端取值更方便；l / d 是表别名
        "SELECT l.log_id, l.client_no, l.drug_id, l.qty, l.ok, l.elapsed_ms, l.created_at, "
        "d.drug_name, d.spec "
        # 【知识点：LEFT JOIN 左连接】以左表 access_log 为主，右表 drug 只为"补充药名"。
        # 即使这条日志的药品已被删除（drug 里查不到），日志行仍然会显示出来，只是药名为 NULL。
        # 换成 INNER JOIN（简写 JOIN）的话，查不到对应药品的日志就会整行消失 —— 日志不能被吞掉
        "FROM access_log l LEFT JOIN drug d ON d.drug_id = l.drug_id "
        # ORDER BY ... DESC 表示按 log_id 从大到小（最新的在前），LIMIT 只取前 N 条
        "ORDER BY l.log_id DESC LIMIT %s", [limit])


def access_log_count():
    # COUNT(*) 数总行数；AS c 给它起别名，取结果时就能写 ["c"]
    return query_one("SELECT COUNT(*) AS c FROM access_log")["c"]


def server_info():
    """取服务器版本 / 当前连接身份，用于证明"连的是远程 Linux 服务器"。"""
    return query_one(
        # 【知识点：MySQL 内置函数与系统变量】
        # VERSION()       —— MySQL 版本号，如 8.0.39
        # CURRENT_USER()  —— 当前登录的账号，如 druguser@192.168.150.1
        # @@hostname      —— 服务器主机名（证明是 Ubuntu 而不是 Windows）
        # @@port          —— MySQL 监听端口
        # @@datadir       —— 数据文件存放目录，Linux 上一定是 /var/lib/mysql/ 这种以 / 开头的路径
        "SELECT VERSION() AS version, CURRENT_USER() AS db_user, "
        "@@hostname AS hostname, @@port AS port, @@datadir AS datadir"
    )


# =====================================================================
# 基础资料
# =====================================================================
def list_drugs(keyword=None, limit=200):
    # SQL 用字符串拼接的方式"按条件拼装"。注意：拼的是 SQL 结构（列名、表名），
    # 用户输入的关键字永远走 %s 参数，不拼进字符串 —— 这样才安全
    sql = ("SELECT d.drug_id AS id, d.drug_code AS code, d.drug_name AS name, "
           "d.spec, d.unit, d.manufacturer AS maker, d.purchase_price AS pp, "
           "d.retail_price AS rp, d.is_rx AS rx, d.status, d.category_id, "
           "c.category_name AS cate, COALESCE(SUM(s.quantity), 0) AS stock "
           "FROM drug d "
           # JOIN = 内连接：只保留"分类表里存在"的药品（分类被删的药品不会显示）
           "JOIN drug_category c ON c.category_id = d.category_id "
           # LEFT JOIN + SUM：某个药在所有仓库的库存加起来，注意要配合 GROUP BY
           "LEFT JOIN stock s ON s.drug_id = d.drug_id ")

    # 先把参数列表准备成空列表，后面按条件往里追加
    args = []

    # 有关键字才加 WHERE 条件（这就是"动态 SQL"）
    if keyword:

        # 【知识点：LIKE 模糊匹配】% 是通配符，表示"任意多个字符"。
        # 这里三个条件用 OR 连起来：药名 或 编码 或 厂家 任意一个包含关键字就算命中
        sql += ("WHERE d.drug_name LIKE %s OR d.drug_code LIKE %s "
                "OR d.manufacturer LIKE %s ")

        # 【知识点：列表 * 3】["%kw%"] * 3 会生成三个元素的列表，正好对应上面三个 %s。
        # 关键字两侧加 % 是"包含匹配"，比如 keyword=阿莫 → %阿莫%
        args = ["%" + keyword + "%"] * 3

    # 【知识点：GROUP BY 与聚合函数】
    # 因为 SELECT 里用了 SUM(s.quantity)（聚合函数），所以 SELECT 里所有"非聚合列"
    # 都必须出现在 GROUP BY 中，否则同一药品的多行会被随意合并，结果不确定。
    # 这是 SQL 的"分组规则"（ONLY_FULL_GROUP_BY 模式会强制检查）
    sql += ("GROUP BY d.drug_id, d.drug_code, d.drug_name, d.spec, d.unit, d.manufacturer, "
            "d.purchase_price, d.retail_price, d.is_rx, d.status, d.category_id, "
            "c.category_name ORDER BY d.drug_id DESC LIMIT %s")

    # 把 limit 也作为参数追加到列表末尾（对应最后的 LIMIT %s）
    args.append(limit)

    # 执行并返回全部行
    return query_all(sql, args)


def drug_options():
    """下拉候选：在售药品 + 各仓库库存。

    不设 LIMIT：新加的药品按名称排序后可能排在最后，
    之前写死 LIMIT 1200 会把它切掉，导致采购 / 销售下拉里找不到。
    （1200 种药品的下拉数据量很小，前端还带关键字筛选，不必截断。）
    """
    return query_all(
        # 只取展示下拉需要的几个字段
        "SELECT d.drug_id AS id, d.drug_name AS name, d.spec, d.unit, "
        "d.purchase_price AS pp, d.retail_price AS rp, d.category_id, "
        # COALESCE(x, 0) = x 是 NULL 就取 0。因为 LEFT JOIN 没匹配到库存时 SUM 会是 NULL，
        # 页面上显示 "None 件" 很难看，所以兜底成 0
        "COALESCE(SUM(s.quantity), 0) AS stock "
        "FROM drug d LEFT JOIN stock s ON s.drug_id = d.drug_id "
        # status = 1 表示在售（0 = 停用/删除）。停用的药不该出现在开单下拉里
        "WHERE d.status = 1 "
        # 聚合查询必须 GROUP BY 所有非聚合列
        "GROUP BY d.drug_id, d.drug_name, d.spec, d.unit, d.purchase_price, "
        "         d.retail_price, d.category_id "
        # 按药名排序，方便人找
        "ORDER BY d.drug_name")


def list_categories():
    # 药品分类下拉：只取 id 和名称，按 id 排序
    return query_all(
        "SELECT category_id AS id, category_name AS name FROM drug_category "
        "WHERE status = 1 ORDER BY category_id")


def list_suppliers():
    # 供应商下拉：带信用等级和联系人（采购开单时要选供应商）
    return query_all(
        "SELECT supplier_id AS id, supplier_name AS name, credit_level AS credit, "
        "contact_person AS contact FROM supplier WHERE status = 1 ORDER BY supplier_id")


def list_warehouses():
    # 仓库下拉：带位置和容量
    return query_all(
        "SELECT warehouse_id AS id, warehouse_name AS name, location, capacity "
        "FROM warehouse WHERE status = 1 ORDER BY warehouse_id")


def list_employees(position=None):
    # 员工下拉：可以把"岗位"当可选筛选条件（比如只列销售员）
    sql = ("SELECT e.employee_id AS id, e.emp_name AS name, e.position, "
           "e.warehouse_id AS wh_id, w.warehouse_name AS wh "
           # 员工关联仓库，把仓库名一起查出来，省得前端再查一次
           "FROM employee e JOIN warehouse w ON w.warehouse_id = e.warehouse_id "
           "WHERE e.status = 1 ")

    # 参数列表先空着
    args = []

    # 传了岗位才加条件
    if position:

        # 注意 AND 前面有空格：因为上一段 SQL 结尾是 "status = 1 "，靠这个空格才不会粘成一个词
        sql += "AND e.position = %s "

        # 把岗位值放进参数列表
        args.append(position)

    # 最后按员工编号排序
    sql += "ORDER BY e.employee_id"

    # 执行
    return query_all(sql, args)


# =====================================================================
# 单据列表
# =====================================================================
def recent_purchases(limit=15):
    """最近的采购单，带供应商、仓库、经办人（4 表 JOIN）。"""
    return query_all(
        # 【知识点：中文别名】AS 单号 —— 列名直接起成中文，前端表格就能直接显示成"单号"，
        # 不用再维护一张"英文字段 → 中文表头"的对照表
        "SELECT po.po_no AS 单号, s.supplier_name AS 供应商, w.warehouse_name AS 仓库, "
        "e.emp_name AS 采购员, po.order_date AS 下单日期, po.status AS 状态, "
        # COUNT 数行数：一张采购单有几个明细，就是"品种数"
        "COUNT(pi.item_id) AS 品种数, "
        # 【知识点：ROUND(数值, 2)】保留两位小数，金额才好看。
        # COALESCE(SUM(...),0) 处理"这张单还没有明细"时 SUM 为 NULL 的情况
        "ROUND(COALESCE(SUM(pi.amount),0),2) AS 金额 "
        # 主表：采购单
        "FROM purchase_order po "
        # 依次内连接供应商、仓库、员工，把三张表的外键"翻译"成名字
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        "JOIN warehouse w ON w.warehouse_id = po.warehouse_id "
        "JOIN employee e ON e.employee_id = po.employee_id "
        # 明细用 LEFT JOIN：允许"刚建单还没录明细"的采购单也显示出来（金额 0）
        "LEFT JOIN purchase_item pi ON pi.po_id = po.po_id "
        # 分组：一张采购单一行，明细被聚合成"品种数"和"金额"
        "GROUP BY po.po_id, po.po_no, s.supplier_name, w.warehouse_name, e.emp_name, "
        "         po.order_date, po.status "
        # 按下单日期倒序取最近 N 张
        "ORDER BY po.order_date DESC, po.po_id DESC LIMIT %s", [limit])


def recent_sales(limit=15):
    """最近的销售单（4 表 JOIN）。"""
    return query_all(
        # 和上面采购单一个套路：销售单 + 仓库 + 销售员 + 明细聚合
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
        # 【知识点：标量子查询】括号里是一条独立的小 SQL，为每一行返回一个值。
        # 这里统计"这个药在这个仓库还有几个批次没卖完"，写法比重重 JOIN 更好懂
        "(SELECT COUNT(*) FROM stock_batch b WHERE b.drug_id = s.drug_id "
        " AND b.warehouse_id = s.warehouse_id AND b.quantity > 0) AS 在库批次 "
        # 主表是库存表：一行 = 一个药品在一个仓库的结存
        "FROM stock s "
        "JOIN drug dr ON dr.drug_id = s.drug_id "
        "JOIN drug_category c ON c.category_id = dr.category_id "
        "JOIN warehouse w ON w.warehouse_id = s.warehouse_id "
        # 按结存从多到少排，一眼看到压货最多的
        "ORDER BY s.quantity DESC LIMIT %s", [limit])


# =====================================================================
# 跨表报表
# =====================================================================
def report_supplier_rank(limit=10):
    """供应商供货排行：purchase_item → purchase_order → supplier → warehouse → employee（5 表）"""
    return query_all(
        # 从"明细"出发一路向上连接到供应商，统计每个供应商供了多少货
        "SELECT s.supplier_name AS 供应商, s.credit_level AS 信用等级, "
        # 【知识点：COUNT(DISTINCT 列)】去重计数。一张单有 5 条明细只算 1 张单，
        # 不加 DISTINCT 会把明细行也数进去，单数就虚高了
        "COUNT(DISTINCT po.po_id) AS 采购单数, COUNT(pi.item_id) AS 采购品种, "
        "SUM(pi.quantity) AS 采购总量, ROUND(SUM(pi.amount), 2) AS 采购金额, "
        # MAX 只是为了让这个非聚合列也能出现在分组结果里（一个供应商可能从多个仓入库，取其中一个）
        "MAX(w.warehouse_name) AS 主要入库仓 "
        "FROM purchase_item pi "
        "JOIN purchase_order po ON po.po_id = pi.po_id "
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        "JOIN warehouse w ON w.warehouse_id = po.warehouse_id "
        "JOIN employee e ON e.employee_id = po.employee_id "
        # 按供应商分组：一个供应商一行
        "GROUP BY s.supplier_id, s.supplier_name, s.credit_level "
        # 【知识点：ORDER BY 用别名】可以直接用 SELECT 里定义的别名排序（采购金额 DESC = 降序）
        "ORDER BY 采购金额 DESC LIMIT %s", [limit])


def report_drug_sales(limit=15):
    """药品销售排行：sale_item → sale_order → drug → drug_category → warehouse（5 表）"""
    return query_all(
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, c.category_name AS 分类, "
        "w.warehouse_name AS 仓库, SUM(si.quantity) AS 销售数量, "
        "ROUND(SUM(si.amount), 2) AS 销售额, COUNT(DISTINCT so.so_id) AS 单据数 "
        # 从销售明细出发，连到单据（拿仓库）、连到药品（拿名称规格）、连到分类
        "FROM sale_item si "
        "JOIN sale_order so ON so.so_id = si.so_id "
        "JOIN drug dr ON dr.drug_id = si.drug_id "
        "JOIN drug_category c ON c.category_id = dr.category_id "
        "JOIN warehouse w ON w.warehouse_id = so.warehouse_id "
        # 按药品 + 规格 + 分类 + 仓库分组，也就是"同一个药在不同仓库分别统计"
        "GROUP BY dr.drug_id, dr.drug_name, dr.spec, c.category_name, w.warehouse_name "
        # 销售额高的排前面
        "ORDER BY 销售额 DESC LIMIT %s", [limit])


def report_batch_chain(limit=20):
    """近效期批次溯源：stock_batch → drug → warehouse → purchase_item → purchase_order → supplier（6 表）"""
    return query_all(
        # 这条查询就是"批次溯源"：从仓库里的一批货，一路回溯到它是哪张采购单、哪个供应商来的
        "SELECT dr.drug_name AS 药品, dr.spec AS 规格, w.warehouse_name AS 仓库, "
        "sb.batch_no AS 批号, sb.quantity AS 结存, sb.expiry_date AS 有效期至, "
        # 【知识点：DATEDIFF(日期1, 日期2)】两个日期相差多少天。
        # 这里算"还有多少天过期"，负数是已经过期
        "DATEDIFF(sb.expiry_date, CURDATE()) AS 剩余天数, "
        "pi.unit_price AS 采购单价, po.po_no AS 来源采购单, s.supplier_name AS 供应商 "
        # 主表：库存批次（一行 = 一个批次）
        "FROM stock_batch sb "
        "JOIN drug dr ON dr.drug_id = sb.drug_id "
        "JOIN warehouse w ON w.warehouse_id = sb.warehouse_id "
        # 关键一步：批次表里存了 purchase_item_id（当初是哪条采购明细进的这个批次），
        # 顺着它就能找到采购明细 → 采购单 → 供应商，这就是"1-1 关系"的落地点
        "JOIN purchase_item pi ON pi.item_id = sb.purchase_item_id "
        "JOIN purchase_order po ON po.po_id = pi.po_id "
        "JOIN supplier s ON s.supplier_id = po.supplier_id "
        # 【知识点：DATE_ADD + INTERVAL】CURDATE() 是今天，INTERVAL 180 DAY 表示 180 天，
        # 整句 = "今天往后 180 天"，也就是"半年内到期"的批次
        "WHERE sb.quantity > 0 AND sb.expiry_date < DATE_ADD(CURDATE(), INTERVAL 180 DAY) "
        # 越早到期越危险，所以按有效期升序排
        "ORDER BY sb.expiry_date LIMIT %s", [limit])


# 单药品出入库全链路 SQL（7 表 JOIN）。{ignore} 占位符供「索引体检」
# 注入 IGNORE INDEX 强制全表扫描，与正常业务查询形成对照。
TRACE_SQL = (
    # 【知识点：模块级常量】用全大写命名表示"这是个常量，别改它"。
    # 它被下面的函数和"索引体检"页面共用，只定义一次
    "SELECT m.move_time AS 发生时间, m.move_type AS 类型, "
    "dr.drug_name AS 药品, w.warehouse_name AS 仓库, "
    "m.change_qty AS 变动, m.before_qty AS 变动前, m.after_qty AS 变动后, "
    "m.batch_no AS 批号, e.emp_name AS 经办人, m.ref_no AS 关联单号, "
    # 一条流水可能来自销售单，也可能来自采购单，用 COALESCE 取"先找到的那个"，
    # 两个都没有就显示"—"
    "COALESCE(so.customer_name, sp.supplier_name, '—') AS 往来单位 "
    # {ignore} 是留给"索引体检"替换的空位：正常查时换成空字符串，
    # 对比实验时换成 " IGNORE INDEX (idx_mv_drug_time)" 强制走全表扫描
    "FROM stock_movement m{ignore} "
    "JOIN drug dr ON dr.drug_id = m.drug_id "
    "JOIN warehouse w ON w.warehouse_id = m.warehouse_id "
    "JOIN employee e ON e.employee_id = m.employee_id "
    # 两个 LEFT JOIN 分别去"销售单"和"采购单"里按单号找对方名称，
    # 用 LEFT JOIN 是因为两条路只有一条能对上（另一条全是 NULL）
    "LEFT JOIN sale_order so ON so.so_no = m.ref_no "
    "LEFT JOIN purchase_order po ON po.po_no = m.ref_no "
    "LEFT JOIN supplier sp ON sp.supplier_id = po.supplier_id "
    # 只查某个药品的流水
    "WHERE m.drug_id = %s "
    # 最近的流水排前面
    "ORDER BY m.move_time DESC, m.move_id DESC LIMIT %s")


def report_drug_trace(drug_id, limit=20):
    """单药品出入库全链路：stock_movement → drug → warehouse → employee
       + LEFT JOIN sale_order / purchase_order / supplier（7 表）"""
    # 【知识点：字符串 replace】把 {ignore} 替换成空字符串，得到"正常版"SQL。
    # （Python 3.6+ 也可用 f-string 写 f"...FROM stock_movement m{ignore}... ，
    #   这里用 replace 是为了让同一份模板能被两种场景复用）
    return query_all(TRACE_SQL.replace("{ignore}", ""), [drug_id, limit])
