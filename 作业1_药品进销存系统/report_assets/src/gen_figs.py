# -*- coding: utf-8 -*-
"""生成报告用的三张示意图（ER 图 / 架构图 / 事务流程图）。

设计约束：
  1. 图内不放任何说明性文字（不写图注、不写解释段落），说明交给正文与图注；
  2. 字号尽量大：画布宽度压在 900~950px，ER 图实体名 19px、架构与流程 15~17px，
     插入 A4（可用宽约 16cm）后仍能看清；
  3. E-R 图只画"实体 + 联系"（字段清单见正文关系模式），节点小、留白大，
     连线按业务线分色，避免十几个字段框挤在一起看不清关系；
  4. 高分辨率输出：Edge 以 --force-device-scale-factor=2 截图，PNG 为 2 倍像素。
"""
import io
import os
import subprocess

SRC = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

FONT = '"SimSun","宋体",serif'
NAVY = "#24476f"
C_BUY = "#2f6fb0"      # 采购链
C_SALE = "#a0442f"     # 销售链
C_STOCK = "#4f7a5a"    # 库存 / 组织
C_LOG = "#7b8794"


# ---------------------------------------------------------------- E-R 图
class Node:
    """E-R 图节点：只写中文名 + 表名，不带字段列表。"""

    def __init__(self, key, col, row, cn, en, kind="entity"):
        self.key, self.cn, self.en, self.kind = key, cn, en, kind
        self.w, self.h = 160, 62
        self.x = [30, 250, 470, 690][col]
        self.y = [26, 206, 386, 530][row]

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2


NODES = [
    Node("supplier", 0, 0, "供应商", "supplier"),
    Node("purchase_order", 1, 0, "采购单", "purchase_order"),
    Node("purchase_item", 2, 0, "采购明细", "purchase_item"),
    Node("stock_batch", 3, 0, "库存批次", "stock_batch", "derived"),
    Node("drug_category", 0, 1, "药品分类", "drug_category"),
    Node("drug", 1, 1, "药品", "drug"),
    Node("stock", 2, 1, "库存结存", "stock", "derived"),
    Node("stock_movement", 3, 1, "库存流水", "stock_movement"),
    Node("warehouse", 0, 2, "仓库", "warehouse"),
    Node("employee", 1, 2, "员工", "employee"),
    Node("sale_order", 2, 2, "销售单", "sale_order"),
    Node("sale_item", 3, 2, "销售明细", "sale_item"),
    Node("access_log", 2, 3, "访问日志", "access_log", "log"),
]
M = {n.key: n for n in NODES}

# (起点, 终点, 联系名, 起点基数, 终点基数, 颜色)
RELS = [
    ("supplier", "purchase_order", "供应", "1", "N", C_BUY),
    ("purchase_order", "purchase_item", "包含", "1", "N", C_BUY),
    ("purchase_item", "stock_batch", "到货生成", "1", "1", C_BUY),
    ("warehouse", "purchase_order", "入库于", "1", "N", C_BUY),
    ("employee", "purchase_order", "经办", "1", "N", C_BUY),
    ("purchase_item", "drug", "涉及", "N", "1", C_STOCK),
    ("drug", "drug_category", "属于", "N", "1", C_STOCK),
    ("drug", "stock", "结存", "1", "N", C_STOCK),
    ("drug", "stock_batch", "批号", "1", "N", C_STOCK),
    ("drug", "stock_movement", "流水", "1", "N", C_STOCK),
    ("warehouse", "stock", "存放", "1", "N", C_STOCK),
    ("warehouse", "stock_batch", "存放", "1", "N", C_STOCK),
    ("warehouse", "stock_movement", "存放", "1", "N", C_STOCK),
    ("employee", "warehouse", "隶属", "N", "1", C_STOCK),
    ("employee", "stock_movement", "经办", "1", "N", C_STOCK),
    ("warehouse", "sale_order", "出库于", "1", "N", C_SALE),
    ("employee", "sale_order", "经办", "1", "N", C_SALE),
    ("sale_order", "sale_item", "包含", "1", "N", C_SALE),
    ("sale_item", "drug", "售出", "N", "1", C_SALE),
]

W, H = 900, 610


def edge_point(n, tx, ty):
    """从节点中心指向 (tx,ty) 的射线与矩形边框的交点。"""
    dx, dy = tx - n.cx, ty - n.cy
    if dx == 0 and dy == 0:
        return n.cx, n.cy
    sx = (n.w / 2) / abs(dx) if dx else 1e9
    sy = (n.h / 2) / abs(dy) if dy else 1e9
    s = min(sx, sy)
    return n.cx + dx * s, n.cy + dy * s


def place_label(sx, sy, ex, ey, text, nodes):
    """联系名标签：从中点开始，沿线的法线方向逐步平移，
    直到白底不压任何实体框（允许压边框线）。"""
    tw = len(text) * 14 + 6
    th = 18
    lx, ly = ex - sx, ey - sy
    dist = (lx * lx + ly * ly) ** 0.5 or 1.0
    nx, ny = -ly / dist, lx / dist            # 单位法线
    for frac in (0.5, 0.4, 0.6, 0.32, 0.68, 0.24, 0.76):
        bx, by = sx + lx * frac, sy + ly * frac
        for d in (0, 13, -13, 26, -26, 40, -40, 54, -54):
            cx, cy = bx + nx * d, by + ny * d
            r = (cx - tw / 2, cy - th / 2, tw, th)
            hit = False
            for n in nodes:
                # 节点矩形缩 5px，视为"允许压边框、不允许压字"
                if (r[0] < n.x + n.w - 5 and r[0] + tw > n.x + 5 and
                        r[1] < n.y + n.h - 5 and r[1] + th > n.y + 5):
                    hit = True
                    break
            if not hit:
                return r
    return (sx + lx / 2 - tw / 2, sy + ly / 2 - th / 2, tw, th)  # 兜底：原中点


def build_erd():
    out = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">']
    out.append(f'''<style>
  body{{margin:0;background:#fff;font-family:{FONT};}}
  #c{{position:relative;width:{W}px;height:{H}px;background:#fff}}
  .n{{position:absolute;width:160px;height:62px;box-sizing:border-box;
     border:1.6px solid {NAVY};background:#fff;text-align:center}}
  .n .cn{{font-size:19px;font-weight:bold;line-height:26px;margin-top:8px;color:#16202b}}
  .n .en{{font-size:11px;line-height:14px;color:#5d6b7a;letter-spacing:.3px}}
  .n.derived{{border:3px double {NAVY}}}
  .n.log{{border:1.4px dashed #6b7683}}
  .n.log .cn{{color:#3f4a56}}
  svg{{position:absolute;left:0;top:0}}
</style></head><body><div id="c">''')

    lines, marks = [], []
    for a, b, name, ca, cb, color in RELS:
        na, nb = M[a], M[b]
        sx, sy = edge_point(na, nb.cx, nb.cy)
        ex, ey = edge_point(nb, na.cx, na.cy)
        lines.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                     f'stroke="{color}" stroke-width="1.3"/>')
        # 两端基数：紧贴端点，位于线的 13% / 87% 处
        for frac, txt in ((0.13, ca), (0.87, cb)):
            px, py = sx + (ex - sx) * frac, sy + (ey - sy) * frac
            marks.append(f'<text x="{px:.1f}" y="{py:.1f}" font-size="14" '
                         f'font-weight="bold" fill="{color}" text-anchor="middle" '
                         f'font-family="{FONT}">{txt}</text>')
        # 联系名：白底标签自动避让实体框
        lx, ly, tw, th = place_label(sx, sy, ex, ey, name, NODES)
        mx, my = lx + tw / 2, ly + th / 2
        marks.append(f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{tw}" '
                     f'height="{th}" fill="#fff" opacity="0.9"/>')
        marks.append(f'<text x="{mx:.1f}" y="{my + 5:.1f}" font-size="14" fill="#4a3131" '
                     f'text-anchor="middle" font-family="{FONT}">{name}</text>')

    out.append(f'<svg width="{W}" height="{H}">{"".join(lines)}</svg>')
    for n in NODES:
        out.append(f'<div class="n {n.kind}" style="left:{n.x}px;top:{n.y}px">'
                   f'<div class="cn">{n.cn}</div><div class="en">{n.en}</div></div>')
    out.append(f'<svg width="{W}" height="{H}">{"".join(marks)}</svg>')
    out.append("</div></body></html>")
    return "".join(out)


# ---------------------------------------------------------------- 架构图
def build_arch():
    W2, H2 = 900, 340
    return f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>
  body{{margin:0;background:#fff;font-family:{FONT};}}
  #c{{position:relative;width:{W2}px;height:{H2}px}}
  .layer{{position:absolute;border:1.8px solid {NAVY};background:#f6f9fc;padding:12px 14px}}
  .layer h3{{margin:0 0 9px 0;font-size:17px;color:{NAVY};letter-spacing:.5px}}
  .box{{border:1.2px solid #6b7683;background:#fff;padding:8px 10px;
        font-size:16px;line-height:23px;color:#1b1b1b}}
  .arr{{position:absolute;height:0;border-top:2px solid #a03030}}
  .arr::after{{content:"";position:absolute;right:-1px;top:-8px;
        border:8px solid transparent;border-left:13px solid #a03030}}
  .lbl{{position:absolute;font-size:15px;color:{NAVY};text-align:center;width:170px}}
</style></head><body><div id="c">
  <div class="layer" style="left:6px;top:20px;width:250px;height:290px">
    <h3>客户端（浏览器）</h3>
    <div class="box" style="margin-bottom:9px">药品目录 · 采购入库<br>销售出库 · 业务表单<br>运营报表 · 并发出库</div>
    <div class="box">HTML + 原生 JS<br>fetch 调用 REST 接口</div>
  </div>
  <div class="layer" style="left:325px;top:20px;width:250px;height:290px">
    <h3>应用层（Flask · Windows）</h3>
    <div class="box" style="margin-bottom:9px">views.py 路由与接口<br>services.py 校验·行锁·事务</div>
    <div class="box">dao.py 手写参数化 SQL<br>PyMySQL 驱动</div>
  </div>
  <div class="layer" style="left:644px;top:20px;width:250px;height:290px">
    <h3>数据库（Linux 服务器）</h3>
    <div class="box" style="margin-bottom:9px">MySQL 8.0 监听 0.0.0.0:3306<br>drug_db：13 张表</div>
    <div class="box">独立 IP 192.168.150.181<br>数据目录 /var/lib/mysql</div>
  </div>
  <div class="arr" style="left:262px;top:150px;width:58px"></div>
  <div class="lbl" style="left:206px;top:112px">① HTTP / JSON</div>
  <div class="arr" style="left:581px;top:150px;width:58px"></div>
  <div class="lbl" style="left:525px;top:112px">② TCP 3306</div>
</div></body></html>'''


# ---------------------------------------------------------------- 事务流程图
def build_txflow():
    W3, H3 = 820, 550
    left = [
        ("①", "接收入库单", "校验药品存在、数量 &gt; 0、仓库合法，计算总额"),
        ("②", "开启事务", "连接置为 REPEATABLE READ，自动提交关闭"),
        ("③", "写单据头", "INSERT purchase_order，生成 po_no"),
        ("④", "写明细", "INSERT purchase_item，逐行写入批号与单价"),
        ("⑤", "生成批次", "INSERT stock_batch，效期 = 生产日期 + 保质期"),
    ]
    right = [
        ("⑥", "锁结存行", "SELECT … FOR UPDATE 锁定 stock 行"),
        ("⑦", "更新结存", "UPDATE stock 数量 + 写入后快照"),
        ("⑧", "写流水", "INSERT stock_movement，记录 before / after"),
        ("⑨", "提交", "COMMIT；任一步异常则全部 ROLLBACK"),
    ]
    s = [f'<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>']
    s.append(f'''body{{margin:0;background:#fff;font-family:{FONT};}}
  #c{{position:relative;width:{W3}px;height:{H3}px}}
  .st{{position:absolute;width:330px;height:74px;box-sizing:border-box;
      border:1.6px solid {NAVY};background:#f6f9fc;padding:8px 12px}}
  .st b{{display:block;font-size:16px;color:{NAVY};margin-bottom:3px}}
  .st span{{font-size:15px;line-height:20px;color:#1b1b1b}}
  .dn{{position:absolute;width:2px;background:#a03030}}
  .dn::after{{content:"";position:absolute;left:-6px;bottom:-1px;border:7px solid transparent;
      border-top:11px solid #a03030;border-bottom:0}}
  .rt{{position:absolute;height:2px;background:#a03030}}
  .rt::after{{content:"";position:absolute;right:-1px;top:-6px;border:7px solid transparent;
      border-left:11px solid #a03030;border-right:0}}
  .rb{{position:absolute;border:1.6px dashed #a03030;color:#8a1f1f;background:#fff3f3;
      font-size:15px;padding:8px 12px;width:330px;box-sizing:border-box}}
''')
    s.append(f'</style></head><body><div id="c">')

    for i, (num, title, desc) in enumerate(left):
        s.append(f'<div class="st" style="left:6px;top:{14 + i * 104}px">'
                 f'<b>{num} {title}</b><span>{desc}</span></div>')
    for i, (num, title, desc) in enumerate(right):
        s.append(f'<div class="st" style="left:470px;top:{14 + i * 104}px">'
                 f'<b>{num} {title}</b><span>{desc}</span></div>')

    for i in range(4):
        s.append(f'<div class="dn" style="left:170px;top:{90 + i * 104}px;height:26px"></div>')
    for i in range(3):
        s.append(f'<div class="dn" style="left:634px;top:{90 + i * 104}px;height:26px"></div>')
    # ⑤ → ⑥ 折线
    s.append('<div class="rt" style="left:336px;top:425px;width:300px"></div>')
    s.append('<div class="dn" style="left:336px;top:420px;height:5px"></div>')

    s.append(f'<div class="rb" style="left:470px;top:442px">任一环节失败（外键不存在 / '
             f'批次冲突 / 库存不足）→ ROLLBACK，5 张表的写入全部撤销</div>')
    s.append("</div></body></html>")
    return "".join(s)


# ---------------------------------------------------------------- 渲染
def render(html_path, png_path, w, h):
    url = "file:///" + html_path.replace("\\", "/")
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=2",
           f"--window-size={w},{h}", "--screenshot=" + png_path,
           "--virtual-time-budget=4000", "--timeout=8000", url]
    subprocess.run(cmd, check=False, timeout=120)
    return os.path.exists(png_path) and os.path.getsize(png_path)


if __name__ == "__main__":
    jobs = [("erd", build_erd, 900, 615), ("arch", build_arch, 905, 348),
            ("txflow", build_txflow, 826, 556)]
    root = os.path.dirname(SRC)
    for name, fn, w, h in jobs:
        hp = os.path.join(SRC, name + ".html")
        io.open(hp, "w", encoding="utf-8").write(fn())
        png = os.path.join(root, name + ".png")
        size = render(hp, png, w, h)
        print(f"{name}: html={os.path.getsize(hp)}B png={size}B")
