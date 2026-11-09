# -*- coding: utf-8 -*-
"""把 model_gen 生成的 Mermaid ER 图渲染成 HTML（供 Chrome 截图成 PNG）"""
import io, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mmd_path = os.path.join(ROOT, "sql", "er_diagram.mmd")
html_path = os.path.join(ROOT, "sql", "_er_preview.html")
png_path = os.path.join(ROOT, "docs", "images", "er_diagram.png")

mmd = io.open(mmd_path, encoding="utf-8").read()
html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{margin:0;padding:28px 32px;background:#fff;font-family:"Microsoft YaHei",sans-serif}
h1{font-size:19px;color:#1f2937;margin:0 0 6px}
p.sub{font-size:12px;color:#6b7280;margin:0 0 18px}
svg{max-width:none !important}
</style></head><body>
<h1>智慧医疗 · 社区门诊预约与处方管理系统　ER 图</h1>
<p class="sub">11 个实体 · 1-1（预约↔就诊）/ 1-N（科室→医生→排班→预约）/ N-M（处方↔药品，经 prescription_item）·
由 tools/model_gen.py 从 sql/er_model.json 自动生成</p>
<pre class="mermaid">""" + mmd + """</pre>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
mermaid.initialize({startOnLoad:true, theme:"neutral",
  er:{layoutDirection:"TB", minEntityWidth:110, minEntityHeight:50, entityPadding:14},
  themeVariables:{fontSize:"13px", fontFamily:"Microsoft YaHei"}});
</script></body></html>"""
io.open(html_path, "w", encoding="utf-8").write(html)
print("HTML:", html_path)

os.makedirs(os.path.dirname(png_path), exist_ok=True)
chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(chrome):
    print("未找到 Chrome，跳过截图")
    sys.exit(0)
subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--window-size=1250,2230", "--screenshot=" + png_path,
                "--virtual-time-budget=15000", "file:///" + html_path.replace("\\", "/")],
               check=False, timeout=120)
print("PNG:", png_path, os.path.getsize(png_path) if os.path.exists(png_path) else "缺失")
