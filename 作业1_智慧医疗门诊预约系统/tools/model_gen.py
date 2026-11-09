#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建模工具链：从 ER 模型（sql/er_model.json）自动生成数据库代码。

对应课程要求 3：「能够应用数据库建模工具进行数据库建模，数据库代码自动生成」。
  - sql/er_model.json  ：建模工具的模型文件（实体、属性、主键、索引、外键、联系）
  - tools/model_gen.py ：从模型自动生成 DDL 与 Mermaid ER 图
  - sql/schema_generated.sql ：自动生成的建表脚本（与手工排版版 schema.sql 结构一致）

用法：
  python tools/model_gen.py                       # 生成 schema_generated.sql
  python tools/model_gen.py --mermaid er.mmd      # 同时导出 Mermaid ER 图
  python tools/model_gen.py --verify 127.0.0.1 3306 root 123456
                                                  # 生成后与 schema.sql 各建一个
                                                  # 临时库，逐表比对结构是否一致
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "sql", "er_model.json")
GEN_PATH = os.path.join(ROOT, "sql", "schema_generated.sql")
REF_PATH = os.path.join(ROOT, "sql", "schema.sql")


# ---------------------------------------------------------------------
# 生成 DDL
# ---------------------------------------------------------------------
def col_sql(a):
    parts = ["`%s`" % a["name"], a["type"]]
    if a.get("auto"):
        parts.append("AUTO_INCREMENT")
    elif a.get("not_null"):
        parts.append("NOT NULL")
    if "default" in a:
        parts.append("DEFAULT %s" % a["default"])
    if a.get("comment"):
        parts.append("COMMENT '%s'" % a["comment"])
    return " ".join(parts)


def gen_ddl(model):
    """把 ER 模型转换为建表 DDL（文本）。"""
    entities = model["entities"]

    # 拓扑校验：外键引用的表必须先建
    seen = set()
    for e in entities:
        for fk in e["foreign_keys"]:
            if fk["ref_table"] not in seen:
                raise SystemExit("错误：表 %s 的外键引用了尚未定义的表 %s"
                                 % (e["name"], fk["ref_table"]))
        seen.add(e["name"])

    out = []
    out.append("-- =============================================================")
    out.append("-- 本文件由 tools/model_gen.py 根据 sql/er_model.json 自动生成，")
    out.append("-- 请勿手改；修改模型后重新生成。")
    out.append("-- =============================================================")
    out.append("")
    out.append("SET NAMES %s;" % model["charset"])
    out.append("")
    out.append("DROP DATABASE IF EXISTS %s;" % model["database"])
    out.append("CREATE DATABASE %s" % model["database"])
    out.append("  DEFAULT CHARACTER SET %s" % model["charset"])
    out.append("  DEFAULT COLLATE %s;" % model["collation"])
    out.append("USE %s;" % model["database"])
    out.append("")

    for e in entities:
        out.append("-- ---- %s（%s）----" % (e["name"], e.get("cn", "")))
        out.append("CREATE TABLE %s (" % e["name"])
        body = [col_sql(a) for a in e["attributes"]]
        body.append("PRIMARY KEY (%s)" % ", ".join("`%s`" % c for c in e["primary_key"]))
        for u in e.get("unique", []):
            body.append("UNIQUE KEY %s (%s)" % (u["name"], ", ".join(u["columns"])))
        for idx in e.get("indexes", []):
            body.append("KEY %s (%s)" % (idx["name"], ", ".join(idx["columns"])))
        for fk in e.get("foreign_keys", []):
            body.append(
                "CONSTRAINT %s FOREIGN KEY (%s) REFERENCES %s (%s)"
                % (fk["name"], ", ".join(fk["columns"]),
                   fk["ref_table"], ", ".join(fk["ref_columns"])))
        for ck in e.get("checks", []):
            body.append("CONSTRAINT %s CHECK (%s)" % (ck["name"], ck["expr"]))
        out.append(",\n".join("  " + b for b in body))
        out.append(") ENGINE=%s DEFAULT CHARSET=%s COMMENT='%s表';"
                   % (model["engine"], model["charset"], e.get("cn", e["name"])))
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------
# 生成 Mermaid ER 图
# ---------------------------------------------------------------------
def gen_mermaid(model):
    lines = ["erDiagram"]
    for r in model["relationships"]:
        label = r.get("label") or "关联"
        if r["type"] == "1-1":
            card = "||--||"
        elif r["type"] == "N-M":
            lines.append("    %s ||--o{ %s : \"%s\"" % (r["from"], r["via"], label))
            lines.append("    %s ||--o{ %s : \"%s\"" % (r["to"], r["via"], label))
            continue
        else:  # 1-N
            card = "||--o{"
        lines.append("    %s %s %s : \"%s\"" % (r["from"], card, r["to"], label))
    return "\n".join(lines)


# ---------------------------------------------------------------------
# 校验：生成的 DDL 与 schema.sql 在真实 MySQL 中结构是否一致
# ---------------------------------------------------------------------
def load_statements(path):
    """读 SQL 文件 → 去注释 → 拆语句 → 去掉库级语句。"""
    text = io.open(path, encoding="utf-8").read()
    body = "\n".join(l for l in text.splitlines()
                     if not l.strip().startswith("--"))
    skip = ("SET NAMES", "DROP DATABASE", "CREATE DATABASE", "USE ")
    return [s.strip() for s in body.split(";")
            if s.strip() and not s.strip().upper().startswith(skip)]


def _pick(row, *names):
    """information_schema 的列名大小写因配置而异，按候选名取值。"""
    for n in names:
        if n in row:
            return row[n]
    return None


def snapshot_schema(cur, db):
    """取某库全部表的结构快照（列 / 主键 / 唯一键 / 普通索引 / 外键 / CHECK）。"""
    snap = {}
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s", (db,))
    tables = sorted(_pick(r, "TABLE_NAME", "table_name") for r in cur.fetchall())
    for t in tables:
        cur.execute(
            "SELECT column_name, column_type, is_nullable, extra, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position", (db, t))
        cols = [(_pick(r, "COLUMN_NAME", "column_name"),
                 _pick(r, "COLUMN_TYPE", "column_type"),
                 _pick(r, "IS_NULLABLE", "is_nullable"),
                 _pick(r, "EXTRA", "extra"),
                 str(_pick(r, "COLUMN_DEFAULT", "column_default")))
                for r in cur.fetchall()]
        cur.execute(
            "SELECT DISTINCT index_name, GROUP_CONCAT(column_name ORDER BY "
            "seq_in_index) AS cols, MAX(non_unique) AS non_unique "
            "FROM information_schema.statistics "
            "WHERE table_schema = %s AND table_name = %s "
            "GROUP BY index_name", (db, t))
        idx = {(_pick(r, "index_name"), _pick(r, "cols"), _pick(r, "non_unique"))
               for r in cur.fetchall()}
        cur.execute(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = %s AND table_name = %s "
            "AND constraint_type IN ('FOREIGN KEY', 'CHECK')", (db, t))
        cons = {_pick(r, "CONSTRAINT_NAME", "constraint_name")
                for r in cur.fetchall()}
        snap[t] = {"cols": cols, "idx": idx, "cons": cons}
    return snap


def verify(host, port, user, password):
    import pymysql

    gen_db, ref_db = "__model_verify_gen", "__model_verify_ref"
    conn = pymysql.connect(host=host, port=int(port), user=user,
                           password=password, charset="utf8mb4",
                           autocommit=True)
    cur = conn.cursor(pymysql.cursors.DictCursor)
    try:
        for db in (gen_db, ref_db):
            cur.execute("DROP DATABASE IF EXISTS `%s`" % db)
            cur.execute("CREATE DATABASE `%s` DEFAULT CHARSET=utf8mb4" % db)
            cur.execute("USE `%s`" % db)
            for st in load_statements(GEN_PATH if db == gen_db else REF_PATH):
                cur.execute(st)

        gen, ref = snapshot_schema(cur, gen_db), snapshot_schema(cur, ref_db)
        ok = True
        if set(gen) != set(ref):
            ok = False
            print("表集合不一致：仅生成有 %s / 仅手工有 %s"
                  % (set(gen) - set(ref), set(ref) - set(gen)))
        for t in sorted(set(gen) & set(ref)):
            if gen[t]["cols"] != ref[t]["cols"]:
                ok = False
                print("[%s] 列不一致" % t)
                for g, r in zip(gen[t]["cols"], ref[t]["cols"]):
                    if g != r:
                        print("   生成:", g, "\n   手工:", r)
            for key in ("idx", "cons"):
                if gen[t][key] != ref[t][key]:
                    ok = False
                    print("[%s] %s 不一致" % (t, key))
                    print("   生成:", sorted(gen[t][key]))
                    print("   手工:", sorted(ref[t][key]))
        print("\n校验结果：%s（共比对 %d 张表）"
              % ("完全一致 ✔" if ok else "存在差异 ✘", len(ref)))
        return 0 if ok else 1
    finally:
        for db in (gen_db, ref_db):
            cur.execute("DROP DATABASE IF EXISTS `%s`" % db)
        conn.close()


# ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="ER 模型 → 数据库代码 自动生成")
    ap.add_argument("--mermaid", metavar="OUT", help="导出 Mermaid ER 图路径")
    ap.add_argument("--verify", nargs=4, metavar=("HOST", "PORT", "USER", "PWD"),
                    help="与 schema.sql 在真实 MySQL 中逐表比对结构")
    args = ap.parse_args()

    model = json.load(io.open(MODEL_PATH, encoding="utf-8"))

    ddl = gen_ddl(model)
    io.open(GEN_PATH, "w", encoding="utf-8", newline="\n").write(ddl)
    print("已生成 %s（%d 张表，%d 行）"
          % (os.path.relpath(GEN_PATH, ROOT), len(model["entities"]),
             ddl.count("\n")))

    if args.mermaid:
        io.open(args.mermaid, "w", encoding="utf-8", newline="\n").write(
            gen_mermaid(model))
        print("已生成 Mermaid ER 图 %s" % args.mermaid)

    if args.verify:
        sys.exit(verify(*args.verify))


if __name__ == "__main__":
    main()
