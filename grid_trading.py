#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 网格交易看板生成器
=====================
核心逻辑: 网格只在震荡市/高位滞涨区赚钱, 趋势市禁用。
所以第一前提是【判断行情状态】, 再决定操作方式/止损/加仓区。

判断体系(全部可量化):
  震荡六指标: ADX / 20日均线斜率 / 20日振幅 / 布林带宽分位 / RSI位置 / 均线缠绕度
  高位滞涨三信号: 60日涨幅>15% / 近20日不创新高 / 量价背离(放量不涨或价平量缩)

行情状态四类:
  趋势市(涨/跌)  —— 禁网格, 交给三因子动量策略
  震荡市        —— 网格主战场
  高位滞涨      —— 网格+减仓双用
  观望          —— 信号不足, 不动

网格参数(仅对震荡/滞涨标的生成):
  区间=近60日高低点(过宽则缩到30日) / 格数5-10(每格2~3%)
  底仓40% / 止损=下沿-5% / 突破停止线=上沿+1%

数据源: 新浪财经公开接口(免费, 标准库only)
输出: docs/grid.html
本地用法: python grid_trading.py
"""
import json
import time
import datetime
import urllib.request
import urllib.error

# ===================== 参数区(可改) =====================
ADX_TREND   = 25      # ADX>=此值判趋势市
ADX_RANGE   = 20      # ADX<此值倾向震荡
AMP_MAX     = 0.15    # 20日振幅小于15%为窄幅
GRID_STEP   = 0.025   # 网格目标步长 2.5%
GRID_MIN_N  = 5       # 最少格数
GRID_MAX_N  = 10      # 最多格数
BASE_POS    = 40      # 建议底仓(%)
STOP_BUF    = 0.05    # 止损缓冲: 跌破区间下沿5%清仓
BREAK_BUF   = 0.01    # 突破上沿1%停止网格
STAG_GAIN   = 0.15    # 60日涨幅>15%才有"高位"前提
BOX_WIDE    = 0.25    # 60日区间宽于25%则改用30日区间

HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

# ETF池 —— 与 etf_rotation.py 保持一致
ETFS = [
    ("sz159915", "创业板"), ("sh510500", "中证500"), ("sh518880", "黄金ETF"),
    ("sh512100", "中证1000"), ("sh588000", "科创50"), ("sh516160", "新能源"),
    ("sh510300", "沪深300"), ("sh512480", "半导体"), ("sh512660", "军工"),
    ("sz159766", "旅游"), ("sh515050", "电信"), ("sh513100", "纳指ETF"),
    ("sz159981", "有色金属"), ("sh513500", "标普500"), ("sz159928", "消费"),
    ("sh513180", "恒生科技"), ("sh512690", "白酒"), ("sh512800", "银行"),
    ("sz159920", "恒生ETF"), ("sh512070", "证券"), ("sh512010", "医药"),
    ("sh510050", "上证50"), ("sh563300", "中证2000"), ("sz159901", "深证100"),
    ("sz159601", "MSCI中国A50"),
    ("sh515080", "中证红利"), ("sh512890", "红利低波"),
    ("sh515790", "光伏"), ("sh515220", "煤炭"), ("sh516150", "稀土"),
    ("sh515210", "钢铁"), ("sz159870", "化工"), ("sz159825", "农业"),
    ("sz159996", "家电"), ("sz159745", "建材"), ("sh516110", "汽车"),
    ("sh516950", "基建"), ("sz159611", "电力"),
    ("sh515980", "人工智能"), ("sh562500", "机器人"), ("sh515230", "软件"),
    ("sh512980", "传媒"),
    ("sh513520", "日经225"), ("sh513030", "德国DAX"), ("sh513080", "法国CAC"),
    ("sz159985", "豆粕"), ("sh511260", "十年国债"), ("sh511090", "三十年国债"),
]
MARKET_INDEX = "sh000001"


# ===================== 数据抓取 =====================
def fetch_kline(symbol, datalen=160):
    """新浪日K线, 返回升序 [{day,open,high,low,close,volume}]"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (symbol, datalen))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("gbk")
    data = json.loads(raw)
    rows = [{
        "day": d["day"], "open": float(d["open"]), "high": float(d["high"]),
        "low": float(d["low"]), "close": float(d["close"]), "volume": float(d["volume"]),
    } for d in data]
    rows.sort(key=lambda x: x["day"])
    return rows


def try_fetch(symbol, tries=2):
    for t in range(tries):
        try:
            r = fetch_kline(symbol)
            if len(r) >= 60:
                return r
        except Exception:
            pass
        if t < tries - 1:
            time.sleep(2)  # 重试前缓一缓, 防新浪456限流
    return None


# ===================== 指标计算 =====================
def ma(vals, n, i=None):
    """第i根(默认最后)的n日均线"""
    if i is None:
        i = len(vals) - 1
    if i < n - 1:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def rsi(closes, n=14):
    """Wilder RSI"""
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def adx(rows, n=14):
    """Wilder ADX, 返回 (adx, di+, di-)"""
    if len(rows) < n * 2 + 1:
        return 20.0, 0.0, 0.0
    trs, pdms, ndms = [], [], []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = rows[i]["high"] - rows[i - 1]["high"], rows[i - 1]["low"] - rows[i]["low"]
        pdms.append(up if up > dn and up > 0 else 0.0)
        ndms.append(dn if dn > up and dn > 0 else 0.0)
    str_ = sum(trs[:n]); spd = sum(pdms[:n]); snd = sum(ndms[:n])
    dxs = []
    for i in range(n, len(trs)):
        str_ = str_ - str_ / n + trs[i]
        spd = spd - spd / n + pdms[i]
        snd = snd - snd / n + ndms[i]
        if str_ == 0:
            continue
        pdi = 100 * spd / str_
        ndi = 100 * snd / str_
        den = pdi + ndi
        dxs.append(100 * abs(pdi - ndi) / den if den else 0)
    if len(dxs) < n:
        return 20.0, 0.0, 0.0
    a = sum(dxs[:n]) / n
    for i in range(n, len(dxs)):
        a = (a * (n - 1) + dxs[i]) / n
    return a, pdi, ndi


def lin_slope(vals, n):
    """近n日线性斜率(日均变化率%)"""
    if len(vals) < n:
        return 0.0
    seg = vals[-n:]
    xbar = (n - 1) / 2
    ybar = sum(seg) / n
    num = sum((i - xbar) * (seg[i] - ybar) for i in range(n))
    den = sum((i - xbar) ** 2 for i in range(n))
    slope = num / den if den else 0
    return slope / ybar * 100 if ybar else 0  # 每日%变化


def percentile(sorted_vals, v):
    """v 在升序序列中的分位(0-100)"""
    if not sorted_vals:
        return 50.0
    c = sum(1 for x in sorted_vals if x <= v)
    return c / len(sorted_vals) * 100


def backtest_grid(rows):
    """网格回测: 前60日定区间, 后60日机械模拟(收盘价)。
    规则: 跌穿格买价买一份, 涨破格卖价卖一份; 破止损线清仓; 破上沿停止网格转持有。
    返回 {ret网格收益%, hold持有收益%, trades, sells, stopped, broke} 或 None"""
    if len(rows) < 125:
        return None
    bt, pre = rows[-60:], rows[-120:-60]
    hi = max(r["high"] for r in pre)
    lo = min(r["low"] for r in pre)
    if (hi - lo) / lo > BOX_WIDE:
        hi = max(r["high"] for r in pre[-30:])
        lo = min(r["low"] for r in pre[-30:])
    width = (hi - lo) / lo
    n = max(GRID_MIN_N, min(GRID_MAX_N, round(width / GRID_STEP)))
    step = (hi - lo) / n
    levels = [lo + step * i for i in range(n + 1)]
    stop, brk = lo * (1 - STOP_BUF), hi * (1 + BREAK_BUF)

    # 首日: 收盘所在格及以下各持有一份, 成本=各格买价
    c0 = bt[0]["close"]
    cell = max(0, min(n - 1, int((c0 - lo) / step)))
    holding = [i <= cell for i in range(n)]
    cash = 0.0
    trades = sells = 0
    stopped = broke = False
    for r in bt[1:]:
        c = r["close"]
        if c < stop:                      # 止损: 全部清仓
            cash += sum(holding) * c
            holding = [False] * n
            stopped = True
            break
        if c > brk and not broke:         # 突破: 停止网格, 余仓转趋势持有
            broke = True
        if not broke:
            for i in range(n):
                if c < levels[i] and not holding[i]:      # 跌穿买价→买
                    holding[i] = True
                    cash -= levels[i]
                    trades += 1
                elif c >= levels[i + 1] and holding[i]:   # 涨破卖价→卖
                    holding[i] = False
                    cash += levels[i + 1]
                    trades += 1
                    sells += 1
    last = bt[-1]["close"]
    final = cash + sum(holding) * last          # 现金 + 持仓市值
    invested0 = sum(levels[i] for i in range(n) if i <= cell) or c0  # 初始持仓成本
    total_cap = sum(levels[i] for i in range(n))  # 满格占用资金(收益率分母)
    return {
        "ret": round((final - invested0) / total_cap * 100, 2),
        "hold": round((last / c0 - 1) * 100, 2),
        "trades": trades, "sells": sells,
        "stopped": stopped, "broke": broke,
    }


def analyze(rows):
    """对单只ETK的K线做全面诊断, 返回指标+状态+网格参数"""
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    vols = [r["volume"] for r in rows]
    price = closes[-1]

    a, pdi, ndi = adx(rows)
    r14 = rsi(closes)
    slope20 = lin_slope([ma(closes, 20, i) or closes[i] for i in range(len(closes))][-30:], 10)

    # 20日振幅
    hi20, lo20 = max(highs[-20:]), min(lows[-20:])
    amp20 = (hi20 - lo20) / price

    # 布林带宽(20,2) 及历史分位
    bws = []
    for i in range(19, len(closes)):
        seg = closes[i - 19:i + 1]
        m = sum(seg) / 20
        sd = (sum((x - m) ** 2 for x in seg) / 20) ** 0.5
        if m:
            bws.append(4 * sd / m)
    bw_now = bws[-1] if bws else 0.05
    bw_pct = percentile(sorted(bws[-120:]), bw_now)

    # 均线缠绕度: 近20日 ma5/ma10/ma20 的离散度均值
    tangles = []
    for i in range(max(19, len(closes) - 20), len(closes)):
        m5, m10, m20 = ma(closes, 5, i), ma(closes, 10, i), ma(closes, 20, i)
        if m5 and m10 and m20 and closes[i]:
            tangles.append((max(m5, m10, m20) - min(m5, m10, m20)) / closes[i])
    tangle = sum(tangles) / len(tangles) if tangles else 0.05

    # ---- 滞涨信号 ----
    gain60 = (price / closes[-61] - 1) if len(closes) > 61 else 0
    hi_all = max(highs[:-1])  # 历史最高(不含今天)
    days_since_high = 0
    for i in range(len(highs) - 1, -1, -1):
        if highs[i] >= max(highs) * 0.999:
            days_since_high = len(highs) - 1 - i
            break
    v5 = sum(vols[-5:]) / 5
    v20 = sum(vols[-20:]) / 20
    chg10 = price / closes[-11] - 1 if len(closes) > 11 else 0
    vol_diverge = (v5 > v20 * 1.2 and abs(chg10) < 0.02) or (v5 < v20 * 0.7 and abs(chg10) < 0.03)

    # ---- 震荡得分(0-100, 六指标) ----
    s_adx = 100 if a < 15 else (30 + (25 - a) * 7 if a < 25 else 0)
    s_slope = 100 if abs(slope20) < 0.15 else (50 if abs(slope20) < 0.4 else 10)
    s_amp = 100 if amp20 < 0.10 else (60 if amp20 < AMP_MAX else 15)
    s_bw = max(0, 100 - bw_pct * 1.2)
    d_rsi = abs(r14 - 50)
    s_rsi = 100 if d_rsi < 10 else (60 if d_rsi < 20 else 20)
    s_tan = 100 if tangle < 0.012 else (55 if tangle < 0.025 else 15)
    range_score = round((s_adx + s_slope + s_amp + s_bw + s_rsi + s_tan) / 6, 1)

    # ---- 滞涨得分 ----
    stag_score = 0
    if gain60 > STAG_GAIN:
        stag_score += 34
    if days_since_high >= 15:
        stag_score += 33
    if vol_diverge:
        stag_score += 33

    # ---- 状态判定 ----
    mom20 = price / closes[-21] - 1 if len(closes) > 21 else 0
    if a >= ADX_TREND:
        state = "趋势上涨" if mom20 > 0 else "趋势下跌"
        cls = "trend"
    elif stag_score >= 67:
        state = "高位滞涨"
        cls = "stag"
    elif range_score >= 58 and a < ADX_TREND:
        state = "震荡市"
        cls = "range"
    else:
        state = "观望"
        cls = "watch"

    # ---- 网格参数(震荡/滞涨才生成) ----
    grid = None
    if cls in ("range", "stag"):
        hi60, lo60 = max(highs[-60:]), min(lows[-60:])
        if (hi60 - lo60) / price > BOX_WIDE and len(rows) >= 30:
            hi60, lo60 = max(highs[-30:]), min(lows[-30:])
        width = (hi60 - lo60) / lo60
        n = max(GRID_MIN_N, min(GRID_MAX_N, round(width / GRID_STEP)))
        step = (hi60 - lo60) / n
        levels = [round(lo60 + step * i, 4) for i in range(n + 1)]
        pos = (price - lo60) / (hi60 - lo60) if hi60 > lo60 else 0.5
        cur_cell = max(0, min(n - 1, int((price - lo60) / step))) if step else 0
        grid = {
            "upper": round(hi60, 4), "lower": round(lo60, 4),
            "n": n, "step_pct": round(step / lo60 * 100, 2),
            "levels": levels, "cur_cell": cur_cell,
            "stop": round(lo60 * (1 - STOP_BUF), 4),
            "brk": round(hi60 * (1 + BREAK_BUF), 4),
            "zone": "下部(可买区)" if pos < 0.33 else ("上部(可卖区)" if pos > 0.66 else "中部(持有)"),
        }

    # ---- 网格回测(仅震荡/滞涨) ----
    bt_result = backtest_grid(rows) if cls in ("range", "stag") else None

    return {
        "price": price, "adx": round(a, 1), "rsi": round(r14, 1),
        "amp20": round(amp20 * 100, 1), "bw_pct": round(bw_pct, 0),
        "slope": round(slope20, 2), "tangle": round(tangle * 100, 2),
        "gain60": round(gain60 * 100, 1), "hi_days": days_since_high,
        "range_score": range_score, "stag_score": stag_score,
        "state": state, "cls": cls, "mom20": round(mom20 * 100, 2),
        "grid": grid, "bt": bt_result, "day": rows[-1]["day"],
    }


# ===================== HTML 渲染 =====================
def fmt_pct(x, signed=True):
    return ("%+.2f%%" % x) if signed else ("%.2f%%" % x)


def color_pct(x):
    c = "#f85149" if x > 0 else ("#3fb950" if x < 0 else "#8b949e")
    return '<span style="color:%s">%s</span>' % (c, fmt_pct(x))


def state_badge(cls, state):
    return '<span class="st %s">%s</span>' % (cls, state)


def grid_card(name, code, d):
    g = d["grid"]
    bt = d.get("bt")
    rows_html = ""
    for i in range(g["n"]):
        buy, sell = g["levels"][i], g["levels"][i + 1]
        cur = ' class="cur"' if i == g["cur_cell"] else ""
        rows_html += ("<tr%s><td>第%d格</td><td>%.3f</td><td>→</td><td>%.3f</td>"
                      "<td>买入</td><td>卖出</td></tr>\n" % (cur, i + 1, buy, sell))
    bt_html = ""
    if bt:
        flag = ""
        if bt["stopped"]:
            flag = ' · <b style="color:#f85149">⚠ 曾止损出场</b>'
        elif bt["broke"]:
            flag = ' · <b style="color:#3fb950">↗ 曾突破转趋势</b>'
        bt_html = ('<div class="bline">📊 近60日回测(区间按60日前定): 网格 <b>%s</b> vs 持有 %s · 成交%d次/套利%d次%s</div>'
                   % (color_pct(bt["ret"]), color_pct(bt["hold"]), bt["trades"], bt["sells"], flag))
    return """
<div class="gcard">
  <div class="ghead">%s <span class="code">%s</span> %s
    <span class="gp">现价 %.3f · %s</span></div>
  <div class="gmeta">
    <span>区间 <b>%.3f ~ %.3f</b></span>
    <span>分 <b>%d</b> 格 · 每格约 <b>%.2f%%</b></span>
    <span>底仓 <b>%d%%</b></span>
    <span class="stop">止损 <b>%.3f</b>(破区间-5%%清仓)</span>
    <span class="brk">突破 <b>%.3f</b>(放量站上停网格转趋势)</span>
  </div>
  %s
  <table class="gt"><tr><th>格</th><th>买价</th><th></th><th>卖价</th><th>到买价</th><th>到卖价</th></tr>
  %s</table>
  <div class="gnote">当前处于 <b>%s</b>(第%d格)。机械执行: 跌穿买价买一份, 涨破卖价卖一份, 不预测不感情。</div>
</div>""" % (name, code, state_badge(d["cls"], d["state"]), d["price"], g["zone"],
            g["lower"], g["upper"], g["n"], g["step_pct"], BASE_POS,
            g["stop"], g["brk"], bt_html, rows_html, g["zone"], g["cur_cell"] + 1)


def render(items, now_str, live, mkt):
    items.sort(key=lambda x: (0 if x["cls"] in ("range", "stag") else 1, -x["range_score"]))
    grids = [x for x in items if x["grid"]]
    n_range = sum(1 for x in items if x["cls"] == "range")
    n_stag = sum(1 for x in items if x["cls"] == "stag")
    n_trend = sum(1 for x in items if x["cls"] == "trend")
    n_watch = sum(1 for x in items if x["cls"] == "watch")

    body_rows = ""
    for d in items:
        badge = state_badge(d["cls"], d["state"])
        if d["grid"]:
            g = d["grid"]
            box = "%.3f~%.3f" % (g["lower"], g["upper"])
            cell = "%d格/%.2f%%" % (g["n"], g["step_pct"])
            stop = "%.3f" % g["stop"]
            op = '<span class="opb grid">网格</span>'
        elif d["cls"] == "trend":
            box = cell = stop = "—"
            op = '<span class="opb no">禁网格</span>'
        else:
            box = cell = stop = "—"
            op = '<span class="opb wait">观望</span>'
        if d.get("bt"):
            b = d["bt"]
            bt_cell = ("%s<br><span class='btsub'>持有%s·套利%d次</span>"
                       % (color_pct(b["ret"]), fmt_pct(b["hold"]), b["sells"]))
            if b["stopped"]:
                bt_cell += "<br><span class='btwarn'>曾止损</span>"
            elif b["broke"]:
                bt_cell += "<br><span class='btbrk'>曾突破</span>"
        else:
            bt_cell = "—"
        body_rows += ("<tr><td>%s</td><td class='nm'>%s</td><td>%.3f</td>"
                      "<td>%s</td><td>%s</td><td>%.1f</td><td>%.1f</td><td>%s</td>"
                      "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>\n"
                      % (d["code"], d["name"], d["price"], color_pct(d["mom20"]),
                         badge, d["adx"], d["range_score"],
                         ("<b>%d</b>" % d["stag_score"]) if d["stag_score"] >= 67 else str(d["stag_score"]),
                         box, cell, stop, bt_cell, op,
                         '<span class="zone">%s</span>' % d["grid"]["zone"] if d["grid"] else "—"))

    cards = "\n".join(grid_card(x["name"], x["code"], x) for x in grids)
    if not cards:
        cards = '<div class="empty">当前没有处于震荡/滞涨的标的, 网格空仓等待。趋势市请用三因子动量看板。</div>'

    mkt_html = ""
    if mkt:
        if mkt["cls"] == "trend":
            if mkt["mom20"] > 0:
                gcls, gpos, gtip = "up", 30, "网格易卖飞: 只做滞涨板块箱体, 主力仓位交给三因子动量策略"
            else:
                gcls, gpos, gtip = "down", 20, "防假箱体接飞刀: 只做回测无止损的真箱体标的, 轻仓跑"
        elif mkt["cls"] == "range":
            gcls, gpos, gtip = "range", 100, "网格黄金期: 仓位可拉满, 按下方卡片参数机械执行"
        else:
            gcls, gpos, gtip = "watch", 50, "方向不明: 半仓试运行, 标的从严"
        mkt_html = ('<div class="mk %s"><b>🎚️ 大盘开关</b> 上证 %.2f · ADX %.1f · 20日动量 %s · 判定 %s '
                    '→ <b>网格总仓位 ≤ %d%%</b><br><span class="mktip">%s</span></div>'
                    % (gcls, mkt["price"], mkt["adx"], color_pct(mkt["mom20"]),
                       state_badge(mkt["cls"], mkt["state"]), gpos, gtip))

    src = "实时数据(新浪)" if live else "离线快照"
    return TEMPLATE % {
        "now": now_str, "src": src, "mkt": mkt_html, "base": BASE_POS,
        "n_range": n_range, "n_stag": n_stag, "n_trend": n_trend, "n_watch": n_watch,
        "rows": body_rows, "cards": cards, "n_grid": len(grids),
    }


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF网格交易看板</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--bd:#30363d;--t:#c9d1d9;--td:#8b949e;
--g:#3fb950;--r:#f85149;--y:#d29922;--b:#58a6ff;--p:#bc8cff;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--t);padding:20px;}
h1{font-size:22px;margin-bottom:4px;}
.sub{color:var(--td);font-size:13px;margin-bottom:16px;}
.mk{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:14px;line-height:1.7;}
.mk.up{border-left:5px solid var(--y);}
.mk.down{border-left:5px solid var(--r);}
.mk.range{border-left:5px solid var(--g);}
.mk.watch{border-left:5px solid var(--td);}
.mktip{font-size:12px;color:var(--td);}
.sum{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;}
.sbox{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 22px;text-align:center;min-width:130px;}
.sbox .n{font-size:28px;font-weight:700;}
.sbox .l{font-size:12px;color:var(--td);margin-top:2px;}
.st{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;white-space:nowrap;}
.st.range{background:rgba(88,166,255,.15);border:1px solid var(--b);color:var(--b);}
.st.stag{background:rgba(188,140,255,.15);border:1px solid var(--p);color:var(--p);}
.st.trend{background:rgba(63,185,80,.12);border:1px solid var(--g);color:var(--g);}
.st.watch{background:rgba(139,148,158,.12);border:1px solid var(--td);color:var(--td);}
.opb{display:inline-block;padding:2px 10px;border-radius:6px;font-size:12px;font-weight:600;}
.opb.grid{background:rgba(88,166,255,.15);color:var(--b);}
.opb.no{background:rgba(248,81,73,.12);color:var(--r);}
.opb.wait{background:rgba(139,148,158,.12);color:var(--td);}
.zone{font-size:12px;color:var(--y);}
.btsub{font-size:11px;color:var(--td);}
.btwarn{font-size:11px;color:var(--r);font-weight:600;}
.btbrk{font-size:11px;color:var(--g);font-weight:600;}
.bline{background:rgba(88,166,255,.07);border:1px solid var(--bd);border-radius:8px;padding:8px 14px;font-size:13px;margin-bottom:12px;}
table{width:100%%;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden;font-size:13px;margin-bottom:24px;}
th{background:#21262d;color:var(--td);padding:10px 6px;text-align:center;font-weight:600;border-bottom:2px solid var(--bd);white-space:nowrap;}
td{padding:9px 6px;text-align:center;border-bottom:1px solid var(--bd);white-space:nowrap;}
td.nm{font-weight:600;}
tr:hover{background:rgba(88,166,255,.05);}
h2{font-size:17px;margin:26px 0 12px;color:var(--b);}
.gcard{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:18px;}
.ghead{font-size:16px;font-weight:700;margin-bottom:10px;}
.ghead .code{color:var(--td);font-weight:400;font-size:13px;margin:0 6px;}
.ghead .gp{font-size:13px;color:var(--td);font-weight:400;margin-left:10px;}
.gmeta{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--td);margin-bottom:12px;}
.gmeta b{color:var(--t);}
.gmeta .stop b{color:var(--r);}
.gmeta .brk b{color:var(--g);}
table.gt{font-size:12px;margin-bottom:10px;}
table.gt td,table.gt th{padding:5px 8px;}
table.gt tr.cur{background:rgba(210,153,34,.14);}
table.gt tr.cur td:first-child{color:var(--y);font-weight:700;}
.gnote{font-size:12px;color:var(--td);line-height:1.7;}
.empty{background:var(--card);border:2px solid var(--y);border-radius:12px;padding:24px;text-align:center;font-size:15px;color:var(--y);}
.tip{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:14px 18px;margin:18px 0;font-size:13px;color:var(--td);line-height:1.9;}
.tip b{color:var(--t);}
.foot{color:var(--td);font-size:12px;margin-top:20px;text-align:center;}
a{color:var(--b);text-decoration:none;}
</style></head><body>
<h1>🧮 ETF 网格交易看板</h1>
<div class="sub">📅 %(now)s · 数据源: %(src)s · <a href="index.html">← 返回三因子轮动看板</a></div>
%(mkt)s
<div class="sum">
  <div class="sbox"><div class="n" style="color:var(--b)">%(n_range)d</div><div class="l">震荡市(网格)</div></div>
  <div class="sbox"><div class="n" style="color:var(--p)">%(n_stag)d</div><div class="l">高位滞涨(网格)</div></div>
  <div class="sbox"><div class="n" style="color:var(--g)">%(n_trend)d</div><div class="l">趋势市(禁网格)</div></div>
  <div class="sbox"><div class="n" style="color:var(--td)">%(n_watch)d</div><div class="l">观望</div></div>
</div>
<div class="tip"><b>使用规则:</b>
① <b>网格第一前提</b>——只做「震荡市」和「高位滞涨」标的, 趋势市(ADX≥25)网格必亏, 坚决不碰;
② 每个标的: 底仓%(base)s%% + 每格等份资金, 跌穿买价买一份、涨破卖价卖一份;
③ <b>止损铁律</b>: 收盘跌破区间下沿5%% = 震荡判断失效, 清仓认赔不补仓;
④ <b>突破处理</b>: 放量涨破区间上沿 = 停止网格, 余仓转趋势持有(看三因子看板);
⑤ 判断依据: ADX / 均线斜率 / 20日振幅 / 布林带宽分位 / RSI / 均线缠绕 六指标共振。</div>
<table>
<tr><th>代码</th><th>名称</th><th>现价</th><th>20日动量</th><th>行情状态</th><th>ADX</th><th>震荡分</th><th>滞涨分</th><th>网格区间</th><th>格数/步长</th><th>止损价</th><th>60日回测</th><th>操作</th><th>当前位置</th></tr>
%(rows)s
</table>
<h2>📐 网格明细 (%(n_grid)d 只可操作标的)</h2>
%(cards)s
<div class="foot">网格交易看板 · 每个交易日收盘后更新 · 判断仅供参考, 不构成投资建议</div>
</body></html>"""


# ===================== 主流程 =====================
def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    items, live = [], True
    mkt = None

    r = try_fetch(MARKET_INDEX)
    if r:
        mkt = analyze(r)
    for code, name in ETFS:
        rows = try_fetch(code)
        if not rows:
            live = False
            continue
        d = analyze(rows)
        d["code"] = code
        d["name"] = name
        items.append(d)
        time.sleep(0.6)  # 每只间隔0.6s, 48只约30s, 防新浪456限流

    if not items:
        print("数据抓取失败, 无网络?")
        return

    html = render(items, now, live and mkt is not None, mkt)
    with open("docs/grid.html", "w", encoding="utf-8") as f:
        f.write(html)
    n_grid = sum(1 for x in items if x["grid"])
    print("OK 数据日=%s 标的=%d 可网格=%d (震荡%d/滞涨%d) -> docs/grid.html"
          % (items[0]["day"], len(items), n_grid,
             sum(1 for x in items if x["cls"] == "range"),
             sum(1 for x in items if x["cls"] == "stag")))


if __name__ == "__main__":
    main()
