#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF三因子轮动看板生成器
因子: M动量(40%) + V相对低位(30%) + F资金流(30%)
数据源: 新浪财经公开接口 (免费)
输出: docs/index.html (看板) + docs/history.json (历史记录)

本地用法:  python etf_rotation.py
GitHub Actions 每日19:00(北京时间)自动运行
"""
import json
import os
import datetime
import urllib.request
import urllib.error

# ===================== 参数区 (可改) =====================
MOMENTUM_SHORT = 10        # 动量短周期(天)
MOMENTUM_LONG  = 20        # 动量长周期(天)
V_PERIOD        = 60        # 价格分位窗口(天)
MA_PERIOD       = 20        # 均线周期
F_SHORT         = 5         # 资金流短周期(均量)
F_LONG          = 20        # 资金流长周期(均量)
MAX_HOLD        = 2         # 持仓数量
WEIGHT          = 50        # 每只建议仓位(%)
STOP_LOSS       = -5.0      # 3日跌幅止损线(%)
W_M, W_V, W_F   = 0.4, 0.3, 0.3

# ===================== ETF池 (21只主流) =====================
ETFS = [
    ("sz159915", "创业板"), ("sh510500", "中证500"), ("sh518880", "黄金ETF"),
    ("sh512100", "中证1000"), ("sh588000", "科创50"), ("sh516160", "新能源"),
    ("sh510300", "沪深300"), ("sh512480", "半导体"), ("sh512660", "军工"),
    ("sz159766", "旅游"), ("sh515050", "电信"), ("sh513100", "纳指ETF"),
    ("sz159981", "有色金属"), ("sh513500", "标普500"), ("sz159928", "消费"),
    ("sh513180", "恒生科技"), ("sh512690", "白酒"), ("sh512800", "银行"),
    ("sz159920", "恒生ETF"), ("sh512070", "证券"), ("sh512010", "医药"),
]

HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

# ===================== 离线兜底快照 (无网络时使用) =====================
# 字段: code,name,price,day(+%),ten(+%),M,Vpct,Vsc,Fr,Fsc,total,pass
DEMO = [
    ("sz159915","创业板",3.368,3.00,-2.74,-8.27,8.7,91.3,1.36,68.0,44.49,False),
    ("sh510500","中证500",7.5,2.24,-0.07,-6.34,8.8,91.2,1.20,59.9,42.78,False),
    ("sh518880","黄金ETF",8.433,0.87,1.47,-0.22,10.1,89.9,0.99,49.4,41.72,False),
    ("sh512100","中证1000",2.864,2.36,-1.51,-8.06,8.2,91.8,1.14,56.9,41.37,False),
    ("sh588000","科创50",1.728,3.54,-4.37,-9.74,8.7,91.3,1.18,58.8,41.13,False),
    ("sh516160","新能源",2.436,0.87,3.66,-3.66,12.1,87.9,1.05,52.4,40.64,False),
    ("sh510300","沪深300",4.653,1.04,1.39,-0.99,12.7,87.2,0.98,48.9,40.45,False),
    ("sh512480","半导体",0.992,3.55,-7.29,-14.56,1.7,98.3,1.10,54.8,40.10,False),
    ("sh512660","军工",1.093,1.86,2.92,-4.64,8.8,91.2,0.94,46.9,39.58,False),
    ("sz159766","旅游",0.579,1.22,6.63,5.78,38.2,61.8,1.13,56.7,37.85,True),
    ("sh515050","电信",0.92,4.43,-12.71,-18.00,1.6,98.4,0.98,49.2,37.10,False),
    ("sh513100","纳指ETF",2.112,3.48,0.24,-0.87,21.9,78.1,0.70,35.1,33.60,False),
    ("sz159981","有色金属",1.485,-1.13,0.41,3.01,36.7,63.3,0.81,40.5,32.32,True),
    ("sh513500","标普500",2.509,1.21,0.24,0.24,39.9,60.1,0.93,46.7,32.14,False),
    ("sz159928","消费",0.697,-0.29,6.74,8.44,70.7,29.3,1.05,52.4,27.88,True),
    ("sh513180","恒生科技",0.609,0.00,3.92,5.09,60.4,39.6,0.86,43.1,26.85,True),
    ("sh512690","白酒",0.453,0.22,9.69,11.57,75.3,24.7,0.95,47.4,26.25,True),
    ("sh512800","银行",0.836,-0.48,5.96,8.34,96.2,3.8,1.07,53.4,20.47,True),
    ("sz159920","恒生ETF",1.522,-0.65,4.82,7.01,85.0,14.9,0.86,42.9,20.16,True),
    ("sh512070","证券",0.813,-0.37,3.17,2.15,86.6,13.4,0.74,36.9,15.97,True),
    ("sh512010","医药",0.377,0.27,3.57,3.69,96.6,3.4,0.68,34.0,12.70,True),
]


def fetch_kline(symbol, datalen=70):
    """从新浪财经抓取日K线, 返回按时间升序的 [{day,close,volume}]"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (symbol, datalen))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("gbk")
    data = json.loads(raw)
    rows = [{"day": d["day"], "close": float(d["close"]), "volume": float(d["volume"])}
            for d in data]
    rows.sort(key=lambda x: x["day"])
    return rows


def compute(rows):
    """根据K线计算三因子与风控, 返回因子字典"""
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    n = len(closes)
    cur = closes[-1]
    prev = closes[-2]

    # M 动量: 近10日×60% + 近20日×40%
    p10 = closes[-1 - MOMENTUM_SHORT]
    p20 = closes[-1 - MOMENTUM_LONG]
    r10 = (cur / p10 - 1) * 100
    r20 = (cur / p20 - 1) * 100
    m = r10 * 0.6 + r20 * 0.4

    # V 相对低位: 60日价格分位
    win = closes[-V_PERIOD:]
    lo, hi = min(win), max(win)
    v_pct = (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0
    v_score = 100 - v_pct

    # F 资金流: 5日均量 / 20日均量 ×50
    f_ratio = (sum(vols[-F_SHORT:]) / F_SHORT) / (sum(vols[-F_LONG:]) / F_LONG)
    f_score = f_ratio * 50

    total = W_M * m + W_V * v_score + W_F * f_score

    # 三重风控
    ma_now = sum(closes[-MA_PERIOD:]) / MA_PERIOD
    ma_prev = sum(closes[-MA_PERIOD - 1:-1]) / MA_PERIOD
    p3 = closes[-4]
    drop3 = (cur / p3 - 1) * 100
    rc1 = cur > ma_now
    rc2 = ma_now > ma_prev
    rc3 = drop3 >= STOP_LOSS
    passed = rc1 and rc2 and rc3

    return {
        "price": cur, "day_chg": (cur / prev - 1) * 100, "ten_chg": r10,
        "m": m, "v_pct": v_pct, "v_score": v_score,
        "f_ratio": f_ratio, "f_score": f_score, "total": total, "pass": passed,
    }


def build_records(live=True):
    records = []
    for code, name in ETFS:
        if live:
            try:
                rows = fetch_kline(code)
                f = compute(rows)
            except Exception as e:
                print("  [跳过] %s 抓取失败: %s" % (code, e))
                continue
        else:
            d = dict(zip(
                ["code", "name", "price", "day_chg", "ten_chg", "m",
                 "v_pct", "v_score", "f_ratio", "f_score", "total", "pass"],
                next(x for x in DEMO if x[0] == code)))
            f = d
        f["code"] = code
        f["name"] = name
        records.append(f)
    return records


def sgn(x, suf="%"):
    return ("+" if x >= 0 else "") + ("%.2f" % x) + suf


def render(records, now_str, live):
    # 按综合得分降序
    ranked = sorted(records, key=lambda r: r["total"], reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    # 推荐: 风控通过中综合前 MAX_HOLD
    passers = [r for r in ranked if r["pass"]]
    holds = passers[:MAX_HOLD]

    # 卡片
    if holds:
        cards = "\n".join(
            '<div class="pcard">\n'
            '  <div class="prank">🏆 第%d名</div>\n'
            '  <div class="pname">%s <span class="pcode">(%s)</span></div>\n'
            '  <div class="pdetail">\n'
            '    <span>现价: <b>%.3f</b></span> |\n'
            '    <span class="fc-m">M: %.2f</span> |\n'
            '    <span class="fc-v">V: %.1f</span> |\n'
            '    <span class="fc-f">F: %.1f</span> |\n'
            '    <span>综合: <b class="fc-t">%.2f</b></span>\n'
            '  </div>\n'
            '  <div class="pw">💰 建议仓位: %d%%</div>\n'
            '  <div class="pr">✅ 三重风控通过 | 日涨跌: %s</div>\n'
            '</div>' % (
                i + 1, r["name"], r["code"], r["price"], r["m"], r["v_score"],
                r["f_score"], r["total"], WEIGHT, sgn(r["day_chg"]))
            for i, r in enumerate(holds)
        )
    else:
        cards = '<div class="empty">⚠️ 今日无通过三重风控标的，建议空仓观望</div>'

    # 表格行
    rows_html = ""
    for r in ranked:
        hl = ' class="hl"' if r["pass"] else ""
        badge = ('<span class="badge pass">✅通过</span>' if r["pass"]
                 else '<span class="badge fail">❌未通过</span>')
        dcls = "pos" if r["day_chg"] >= 0 else "neg"
        tcls = "pos" if r["ten_chg"] >= 0 else "neg"
        rows_html += (
            "<tr%s>\n"
            "  <td>%d</td><td class=\"code\">%s</td><td class=\"name\">%s</td>\n"
            "  <td>%.3f</td><td class=\"%s\">%s</td>\n"
            "  <td class=\"%s\">%s</td><td>%.2f</td>\n"
            "  <td>%.1f%%</td><td>%.1f</td>\n"
            "  <td>%.2f</td><td>%.1f</td>\n"
            "  <td class=\"total\">%.2f</td><td>%s</td>\n"
            "</tr>\n" % (
                hl, r["rank"], r["code"], r["name"], r["price"], dcls, sgn(r["day_chg"]),
                tcls, sgn(r["ten_chg"]), r["m"], r["v_pct"], r["v_score"],
                r["f_ratio"], r["f_score"], r["total"], badge)
        )

    # 历史记录
    hist = load_history()
    if holds:
        hold_str = " | ".join("%s(%.1f)" % (r["name"], r["total"]) for r in holds)
    else:
        hold_str = "空仓"
    entry = {
        "time": now_str, "holds": hold_str,
        "total": len(ranked), "pass": len(passers),
    }
    hist.append(entry)
    hist = hist[-30:]
    save_history(hist)

    hist_rows = "\n".join(
        "<tr>\n  <td>%s</td><td>%s</td><td>%d</td><td>%d</td>\n</tr>" % (
            h["time"], h["holds"], h["total"], h["pass"]) for h in reversed(hist[-10:])
    )

    src = "新浪财经(实时)" if live else "新浪财经(离线快照)"
    html = TEMPLATE % (
        now_str, src, cards, rows_html, hist_rows,
    )
    return html, entry


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="description" content="ETF三因子轮动看板 - 动量M+相对低位V+资金流F - 每日自动更新">
<title>ETF三因子轮动看板 | 每日自动更新</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--bd:#30363d;--t:#c9d1d9;--td:#8b949e;--g:#3fb950;--r:#f85149;--y:#d29922;--b:#58a6ff;--tt:#e3b341;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--t);padding:20px;}
.hd{text-align:center;margin-bottom:24px;}
.hd h1{font-size:30px;color:var(--b);margin-bottom:8px;}
.hd .sub{color:var(--td);font-size:14px;}
.hd .dt{color:var(--g);font-size:16px;margin-top:4px;}
.hd .auto{color:var(--y);font-size:12px;margin-top:4px;}
.lg{display:flex;gap:24px;justify-content:center;margin-bottom:16px;font-size:12px;color:var(--td);flex-wrap:wrap;}
.pc{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap;justify-content:center;}
.pcard{background:var(--card);border:2px solid var(--g);border-radius:12px;padding:16px 24px;min-width:360px;text-align:center;}
.prank{color:var(--y);font-size:14px;font-weight:bold;margin-bottom:4px;}
.pname{font-size:22px;font-weight:bold;margin-bottom:8px;}
.pcode{font-size:14px;color:var(--td);}
.pdetail{font-size:13px;color:var(--td);margin-bottom:8px;}
.pw{color:var(--g);font-size:16px;font-weight:bold;margin-bottom:4px;}
.pr{font-size:12px;color:var(--td);}
.fc-m{color:var(--b);}.fc-v{color:var(--g);}.fc-f{color:var(--y);}.fc-t{color:var(--tt);}
.empty{background:var(--card);border:2px solid var(--y);border-radius:12px;padding:20px 40px;font-size:18px;color:var(--y);}
table{width:100%%;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden;font-size:13px;margin-bottom:24px;}
th{background:#21262d;color:var(--td);padding:10px 8px;text-align:center;font-weight:600;border-bottom:2px solid var(--bd);white-space:nowrap;}
td{padding:8px;text-align:center;border-bottom:1px solid var(--bd);}
tr.hl{background:rgba(63,185,80,0.1);}
tr.hl td{border-color:rgba(63,185,80,0.3);}
.code{color:var(--b);font-family:monospace;}.name{font-weight:600;}
.pos{color:var(--g);}.neg{color:var(--r);}.total{color:var(--tt);font-weight:bold;font-size:15px;}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
.pass{background:rgba(63,185,80,0.2);color:var(--g);}
.fail{background:rgba(248,81,73,0.15);color:var(--r);}
.warn{background:rgba(210,153,34,0.2);color:var(--y);}
.ft{text-align:center;margin-top:24px;color:var(--td);font-size:12px;}
.tip{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 16px;margin:16px 0;font-size:13px;color:var(--td);line-height:1.8;}
.tip b{color:var(--y);}
.hist-title{color:var(--b);font-size:18px;font-weight:bold;margin:24px 0 12px 0;text-align:center;}
.hist-table{font-size:12px;}
@media(max-width:768px){table{font-size:11px;}th,td{padding:6px 4px;}.lg{gap:8px;}}
</style></head><body>
<div class="hd"><h1>📊 ETF三因子轮动看板</h1>
<div class="sub">动量M(40%%) + 相对低位V(30%%) + 资金流F(30%%) | 21只主流ETF全覆盖</div>
<div class="dt">📅 %s</div>
<div class="auto">⚡ 每个交易日19:00自动更新 | 数据源: %s</div></div>
<div class="lg">
<span>🟦 M=近10日×60%%+近20日×40%%</span>
<span>🟩 V=60日价格分位(越低分越高)</span>
<span>🟨 F=5日均量/20日均量×50</span>
</div>
<div class="pc">%s</div>
<table><thead><tr>
<th>#</th><th>代码</th><th>名称</th><th>现价</th><th>日涨幅</th><th>10日收益</th>
<th>M得分</th><th>V分位</th><th>V得分</th><th>F比率</th><th>F得分</th><th>综合</th><th>风控</th>
</tr></thead><tbody>%s</tbody></table>

<div class="hist-title">📋 历史推荐记录</div>
<table class="hist-table"><thead><tr>
<th>更新时间</th><th>推荐持仓</th><th>ETF总数</th><th>风控通过数</th>
</tr></thead><tbody>%s</tbody></table>

<div class="tip">
📌 <b>使用说明：</b>每天19:00后查看最新看板 → 选综合得分前2名(各50%%) → 单日跌超-5%%止损 → 三重风控未通过则空仓<br>
📌 <b>三重风控：</b>①价格站上20日均线 ②均线方向向上 ③3日跌幅不超过5%%<br>
📌 <b>与单因子动量区别：</b>增加V(低位逆向)和F(资金流)双重过滤，避免追高踩坑
</div>
<div class="ft">
<p>⚡ 每个交易日19:00自动更新 | 数据源: 新浪财经(免费公开) | ⚠️ 仅供参考，投资有风险</p>
<p>策略: 买入综合得分前2名(各50%%) | 单日跌超-5%%止损 | 三重风控验证</p>
</div>
</body></html>
"""


def load_history():
    p = os.path.join(DOCS, "history.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(hist):
    p = os.path.join(DOCS, "history.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def main():
    global DOCS
    base = os.path.dirname(os.path.abspath(__file__))
    DOCS = os.path.join(base, "docs")
    os.makedirs(DOCS, exist_ok=True)

    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    print("[1/3] 抓取并计算因子 ...")
    records = build_records(live=True)
    live = True
    if not records:
        print("  ⚠️ 实时数据全部抓取失败, 回退离线快照")
        records = build_records(live=False)
        live = False

    print("[2/3] 生成看板 HTML ...")
    html, entry = render(records, now_str, live)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print("  看板已写入 docs/index.html")

    print("[3/3] 更新历史记录 ...")
    print("  本次推荐: %s | 通过风控: %d/%d" % (
        entry["holds"], entry["pass"], entry["total"]))
    print("✅ 完成 (%s)" % ("实时" if live else "离线快照"))


if __name__ == "__main__":
    main()
