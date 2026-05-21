import os
from datetime import datetime
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
INDEX_DIR = os.path.join(BASE_DIR, "index.html")


def update_index():

    files = sorted(
        [
            f for f in os.listdir(REPORT_DIR)
            if f.endswith(".html") and os.path.isfile(os.path.join(REPORT_DIR, f))
        ],
        reverse=True
    )

    items = ""

    for f in files:
        if f.endswith(".html"):
            items += f"""
            <li>
                <a href="report_daily/{f}" target="_blank">{f.replace('.html','')}</a>
            </li>
            """

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <title>AI量化看板</title>
    <style>
        body {{
            font-family: Microsoft YaHei;
            margin: 40px;
        }}

        h1 {{
            color: #1f4e79;
        }}

        ul {{
            line-height: 30px;
        }}

        a {{
            text-decoration: none;
            color: #007acc;
        }}

        a:hover {{
            color: red;
        }}

        .box {{
            padding: 20px;
            border: 1px solid #ddd;
            border-radius: 10px;
        }}
    </style>
    </head>

    <body>

    <h1>📊 AI量化选股看板</h1>

    <div class="box">
        <h2>📅 历史日报</h2>
        <ul>
            {items}
        </ul>
    </div>

    </body>
    </html>
    """

    with open(INDEX_DIR, "w", encoding="utf-8") as f:
        f.write(html)


def run_dashboard():

    # 1. 保存日报
    # 2. 更新首页
    update_index()

    print("index.html 已更新")

# =========================
# 启动
# =========================
if __name__ == "__main__":

    run_dashboard()
