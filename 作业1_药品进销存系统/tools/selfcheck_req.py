# -*- coding: utf-8 -*-
"""课程要求自查脚本：逐条实测（只读 + 事务回滚，不动业务数据）"""
import sys
import time

import pymysql

HOST, PORT, USER, PWD, DB = "127.0.0.1", 3306, "root", "123456", "drug_db"
ok_n = fail_n = 0


def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print("  [PASS] %-36s %s" % (name, detail))
    else:
        fail_n += 1
        print("  [FAIL] %-36s %s" % (name, detail))


conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PWD,
                       database=DB, charset="utf8mb4")
cur = conn.cursor()

print("=" * 72)
print("药品进销存管理系统 · 课程要求逐条实测（数据库层）")
print("=" * 72)

# ---------------------------------------------------------------------
cur.execute("SELECT VERSION()")
version = cur.fetchone()[0]
check("0) MySQL 服务器可连接", bool(version), "MySQL %s @ %s:%d/%s" % (version, HOST, PORT, DB))

# 1 数据规模
cur.execute("SHOW TABLES")
tables = [r[0] for r in cur.fetchall()]
biz = [t for t in tables if t != "access_log"]
total = 0
for t in biz:
    cur.execute("SELECT COUNT(*) FROM `%s`" % t)
    total += cur.fetchone()[0]
check("1) 数据表 ≥ 5 张", len(biz) >= 5, "%d 张业务表：%s" % (len(biz), "、".join(biz)))
check("   数据总量 ≥ 100,000 条", total >= 100000, "合计 %s 条" % format(total, ","))

# 2 关系类型
cur.execute("""SELECT COUNT(*) FROM information_schema.table_constraints
               WHERE table_schema=%s AND constraint_type='FOREIGN KEY'""", (DB,))
fk_n = cur.fetchone()[0]
check("2) 1-N 联系（外键）", fk_n >= 10, "%d 个外键约束" % fk_n)

cur.execute("SHOW COLUMNS FROM stock_batch LIKE 'purchase_item_id'")
has_col = bool(cur.fetchone())
cur.execute("""SELECT COUNT(*) FROM information_schema.statistics
               WHERE table_schema=%s AND table_name='stock_batch'
                 AND index_name='uk_batch_purchase' AND non_unique=0""", (DB,))
uk_n = cur.fetchone()[0]
check("   1-1 联系（采购明细 ↔ 批次）", has_col and uk_n == 1,
      "stock_batch.purchase_item_id 唯一索引 uk_batch_purchase")

cur.execute("""SELECT COUNT(*) FROM information_schema.key_column_usage
               WHERE table_schema=%s AND table_name IN ('purchase_item','sale_item')
                 AND referenced_table_name='drug'""", (DB,))
nm = cur.fetchone()[0]
check("   N-M 联系（单据 ↔ 药品桥接）", nm >= 2,
      "purchase_item / sale_item 各自带 drug_id 外键，拆解两张单据与药品的 N-M")

# 3 约束
cur.execute("""SELECT COUNT(*) FROM information_schema.table_constraints
               WHERE table_schema=%s AND constraint_type='CHECK'""", (DB,))
ck_n = cur.fetchone()[0]
check("3) CHECK 约束兜底", ck_n >= 8, "%d 个 CHECK（库存非负 / 流水守恒 / 单价非负）" % ck_n)

cur.execute("SELECT stock_id, quantity FROM stock WHERE quantity > 100 LIMIT 1")
sid, sq = cur.fetchone()
try:
    cur.execute("UPDATE stock SET quantity = -1 WHERE stock_id = %s", (sid,))
    conn.rollback()
    check("   负库存被 CHECK 拦截", False, "竟然没有报错")
except pymysql.MySQLError as e:
    conn.rollback()
    check("   负库存被 CHECK 拦截", e.args[0] == 3819,
          "错误码 %s（3819 = 违反 ck_stock_qty，库存不能为负）" % e.args[0])

# 4 外键拦截
try:
    cur.execute("DELETE FROM drug WHERE drug_id = 1")
    conn.rollback()
    check("4) 外键拦截删除被引用数据", False, "竟然删除成功")
except pymysql.MySQLError as e:
    conn.rollback()
    check("4) 外键拦截删除被引用数据", e.args[0] == 1451,
          "错误码 %s（1451 = 有外键引用，不能删除）" % e.args[0])

# 5 事务
cur.execute("SELECT COUNT(*) FROM drug")
n0 = cur.fetchone()[0]
cur.execute("START TRANSACTION")
cur.execute("INSERT INTO drug_category (category_id, category_name, category_code, status) "
            "VALUES (900, '自查分类', 'T900', 1)")
cur.execute("INSERT INTO drug (drug_id, drug_code, drug_name, category_id, spec, status) "
            "VALUES (9001, 'YPTEST01', '自查药', 900, '1g*1片', 1)")
cur.execute("ROLLBACK")
cur.execute("SELECT COUNT(*) FROM drug")
n1 = cur.fetchone()[0]
check("5) 事务回滚（INSERT 后 ROLLBACK）", n1 == n0, "插入 2 行后回滚，药品数 %d → %d" % (n0, n1))

# 6 行锁（FOR UPDATE）
cur.execute("START TRANSACTION")
cur.execute("SELECT stock_id, quantity FROM stock WHERE quantity > 100 LIMIT 1 FOR UPDATE")
sid, row_qty = cur.fetchone()
cur.execute("UPDATE stock SET quantity = quantity - 1 WHERE stock_id = %s", (sid,))
cur.execute("SELECT quantity FROM stock WHERE stock_id = %s", (sid,))
mid = cur.fetchone()[0]
conn.rollback()
cur.execute("SELECT quantity FROM stock WHERE stock_id = %s", (sid,))
back = cur.fetchone()[0]
check("6) FOR UPDATE 行锁事务", mid == row_qty - 1 and back == row_qty,
      "锁行扣 1（%d → %d）后回滚复原为 %d" % (row_qty, mid, back))

# 7 索引
cur.execute("""SELECT COUNT(DISTINCT index_name), COUNT(*) FROM information_schema.statistics
               WHERE table_schema=%s AND index_name <> 'PRIMARY'""", (DB,))
idx_n, idx_rows = cur.fetchone()
cur.execute("""SELECT COUNT(DISTINCT index_name) FROM information_schema.statistics
               WHERE table_schema=%s AND non_unique=0 AND index_name <> 'PRIMARY'""", (DB,))
idx_uk = cur.fetchone()[0]
check("7) 索引数量", idx_n >= 20,
      "%d 个二级索引（唯一 %d + 普通 %d；多列索引按列展开共 %d 条记录）"
      % (idx_n, idx_uk, idx_n - idx_uk, idx_rows))

# 8 库存分布
cur.execute("SELECT SUM(quantity), COUNT(*) FROM stock")
qty_sum, sku_rows = cur.fetchone()
cur.execute("SELECT COUNT(*) FROM stock_batch WHERE quantity > 0 "
            "AND expiry_date < DATE_ADD(CURDATE(), INTERVAL 90 DAY)")
near = cur.fetchone()[0]
check("8) 库存与效期数据可用", qty_sum and qty_sum > 100000 and sku_rows > 1000,
      "库存 %s 件 / %s 个药品仓 / 近效期批次 %d 个" % (
          format(int(qty_sum), ","), format(sku_rows, ","), near))

cur.close()
conn.close()

print()
print("=" * 72)
print("结果：通过 %d 项，失败 %d 项" % (ok_n, fail_n))
print("=" * 72)
sys.exit(0 if fail_n == 0 else 1)
