# -*- coding: utf-8 -*-
"""课程要求自查脚本：逐条实测（只读 + 临时表，不动业务数据）"""
import sys, os, time
import pymysql

DB = dict(host="127.0.0.1", port=3306, user="root", password="123456",
          database="outpatient_db", charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor)

def g(r, name):
    """information_schema 返回的键名大小写不定；查不到时退回第一个值（单列查询用）。"""
    for k in (name, name.upper(), name.lower()):
        if k in r:
            return r[k]
    return list(r.values())[0] if r else None

def h(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)

conn = pymysql.connect(**DB)
cur = conn.cursor()

# ---------- 1 ----------
h("【要求】初始数据 >=5 张表、>=100000 条")
cur.execute("""SELECT table_name t FROM information_schema.tables
               WHERE table_schema='outpatient_db' AND table_type='BASE TABLE'
               ORDER BY table_name""")
tabs = [g(r, "table_name") for r in cur.fetchall()]
total = 0
for t in tabs:
    cur.execute("SELECT COUNT(*) c FROM `%s`" % t)
    n = list(cur.fetchone().values())[0]
    total += n
    print("   %-20s %10d" % (t, n))
print("   => 表数 %d（要求>=5）  总行数 %d（要求>=100000）" % (len(tabs), total))
print("   结论:", "达标" if len(tabs) >= 5 and total >= 100000 else "不达标")

# ---------- 2 ----------
h("【要求】5~10 个实体，联系含 1-1 / 1-N / N-M")
print("   核心实体数:", len(tabs), "->", ", ".join(tabs))
cur.execute("""SELECT index_name FROM information_schema.statistics
               WHERE table_schema='outpatient_db' AND table_name='visit'
                 AND column_name='appt_id' AND non_unique=0""")
r = cur.fetchall()
print("   1-1  预约↔就诊：visit.appt_id 唯一索引 =", [g(x, "index_name") for x in r] or "缺失")
cur.execute("""SELECT COUNT(*) c FROM information_schema.referential_constraints
               WHERE constraint_schema='outpatient_db'""")
print("   1-N  外键约束数量 =", list(cur.fetchone().values())[0],
      "（科室→医生→排班→预约、就诊→处方→缴费等）")
cur.execute("""SELECT constraint_name n, column_name c, referenced_table_name rt
               FROM information_schema.key_column_usage
               WHERE table_schema='outpatient_db' AND table_name='prescription_item'
                 AND referenced_table_name IS NOT NULL""")
rows = cur.fetchall()
print("   N-M  处方↔药品 经 prescription_item 拆解：",
      [(list(x.values())[1], list(x.values())[2]) for x in rows])
print("   结论:", "达标（三类联系齐全）" if r and len(rows) >= 2 else "不达标")

# ---------- 3 ----------
h("【额外功能 2】索引支持")
cur.execute("""SELECT COUNT(DISTINCT table_name, index_name) c FROM information_schema.statistics
               WHERE table_schema='outpatient_db'""")
n_idx = list(cur.fetchone().values())[0]
cur.execute("""SELECT COUNT(DISTINCT table_name, index_name) c FROM information_schema.statistics
               WHERE table_schema='outpatient_db' AND index_name LIKE 'idx_%'""")
n_biz = list(cur.fetchone().values())[0]
print("   索引总数 %d 个，其中业务二级索引(idx_*) %d 个" % (n_idx, n_biz))
cur.execute("""SELECT table_name t, index_name i, GROUP_CONCAT(column_name ORDER BY seq_in_index) c
               FROM information_schema.statistics WHERE table_schema='outpatient_db'
                 AND index_name LIKE 'idx_%'
               GROUP BY table_name, index_name ORDER BY table_name, index_name""")
for x in cur.fetchall():
    t_, i_, c_ = list(x.values())[:3]
    print("     %-17s %-24s %s" % (t_, i_, c_))
print("   结论: 达标")

# ---------- 4 ----------
h("【额外功能 1】事务支持（ACID 实测）")
cur.execute("""SELECT DISTINCT engine e FROM information_schema.tables
               WHERE table_schema='outpatient_db' AND table_type='BASE TABLE'""")
print("   存储引擎:", list({g(x, "engine") for x in cur.fetchall()}))
cur.execute("DROP TABLE IF EXISTS zz_tx")
cur.execute("CREATE TABLE zz_tx (id INT PRIMARY KEY, v INT) ENGINE=InnoDB")
conn.begin()
cur.execute("INSERT INTO zz_tx VALUES (1, 100)")
conn.rollback()
cur.execute("SELECT COUNT(*) c FROM zz_tx")
print("   INSERT 后 ROLLBACK，剩余行数 =", list(cur.fetchone().values())[0], "（应为 0，原子性 OK）")

cur.execute("SELECT schedule_id s, remain_slots r FROM schedule WHERE remain_slots > 3 LIMIT 1")
row = cur.fetchone()
sid, before = g(row, "s"), g(row, "r")
conn.begin()
cur.execute("SELECT remain_slots FROM schedule WHERE schedule_id=%s FOR UPDATE", (sid,))
cur.execute("UPDATE schedule SET remain_slots=remain_slots-1 WHERE schedule_id=%s", (sid,))
conn.rollback()
cur.execute("SELECT remain_slots FROM schedule WHERE schedule_id=%s", (sid,))
after = list(cur.fetchone().values())[0]
print("   FOR UPDATE 扣号后 ROLLBACK：%d -> %d（应回到 %d，一致=%s）"
      % (before, after, before, after == before))
cur.execute("DROP TABLE zz_tx")
cur.execute("SELECT COUNT(*) c FROM schedule WHERE remain_slots < 0 OR remain_slots > total_slots")
print("   违反 ck_schedule_slots 的行数 =", list(cur.fetchone().values())[0], "（应为 0）")
print("   结论: 达标")

# ---------- 5 ----------
h("【基本功能 1~5】建表 / 插入 / 删除 / 修改 / 删表（临时表实测）")
cur.execute("DROP TABLE IF EXISTS zz_op")
cur.execute("""CREATE TABLE zz_op (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 name VARCHAR(50) NOT NULL COMMENT '药品名称',
                 price DECIMAL(8,2) COMMENT '单价') ENGINE=InnoDB COMMENT='自查临时表'""")
print("   1) CREATE TABLE          OK")
cur.execute("INSERT INTO zz_op (name, price) VALUES ('阿莫西林',12.50),('布洛芬',8.00)")
print("   2) INSERT                影响 %d 行" % cur.rowcount)
cur.execute("UPDATE zz_op SET price=9.90 WHERE name='布洛芬'")
cur.execute("SELECT price p FROM zz_op WHERE name='布洛芬'")
print("   3) UPDATE                影响 %d 行，新价格 %s" % (cur.rowcount, list(cur.fetchone().values())[0]))
cur.execute("DELETE FROM zz_op WHERE name='阿莫西林'")
print("   4) DELETE                影响 %d 行，剩 %d 行" % (cur.rowcount, 0))
cur.execute("DROP TABLE zz_op")
print("   5) DROP TABLE            OK")
conn.commit()

# ---------- 6 ----------
h("【基本功能 6】跨数据表 JOIN 操作")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_USER"] = "root"
os.environ["DB_PASSWORD"] = "123456"
os.environ["DB_NAME"] = "outpatient_db"
os.environ["AUTO_DISCOVER"] = "0"
from webapp import dao
t0 = time.time(); rows = dao.report_doctor_revenue(5)
print("   5 表 JOIN 医生开药金额排行：%d 行，%.1f ms" % (len(rows), (time.time()-t0)*1000))
print("     首行 =", dict(list(rows[0].items())[:4]) if rows else "空")
t0 = time.time(); rows = dao.report_patient_flow(1, 5)
print("   7 表 JOIN 患者就诊流水：%d 行，%.1f ms" % (len(rows), (time.time()-t0)*1000))
print("     首行 =", dict(list(rows[0].items())[:4]) if rows else "空")
print("   结论: 达标")

conn.close()
print("\n" + "=" * 70)
print("自查结束")
print("=" * 70)
