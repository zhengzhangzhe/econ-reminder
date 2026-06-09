#!/usr/bin/env python3
"""
经济指标发布提醒系统

每周一北京时间 8:00 运行，推送本周（周一至周五）美国要发布的
经济指标，包括对股市、金价的影响分析。

数据来源：
  - FOMC: federalreserve.gov 官网
  - 其余指标: 2026 下半年精确排期 + 算法兜底
"""

import os
import sys
import json
import logging
from datetime import date, datetime, timezone, timedelta

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("econ-reminder")

BEIJING_TZ = timezone(timedelta(hours=8))
WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ── 配置 ──────────────────────────────────────────────────

def load_config() -> dict:
    config = {
        "fred_api_key": os.environ.get("FRED_API_KEY", ""),
        "server_chan_key": os.environ.get("SERVER_CHAN_KEY", ""),
    }
    if not config["fred_api_key"] or not config["server_chan_key"]:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                fc = json.load(f)
                if not config["fred_api_key"]:
                    config["fred_api_key"] = fc.get("fred_api_key", "")
                if not config["server_chan_key"]:
                    config["server_chan_key"] = fc.get("server_chan_key", "")
    return config


# ── 夏令时 ─────────────────────────────────────────────────
# 美国 DST: 3月第二个周日 → 11月第一个周日
# 夏季 (EDT): 3-10月, 8:30 ET = 20:30 北京
# 冬季 (EST): 11-2月, 8:30 ET = 21:30 北京

def _is_dst(d: date) -> bool:
    """date 是否在美国夏令时期间"""
    # DST starts: 2nd Sunday of March
    mar_1 = date(d.year, 3, 1)
    dst_start = mar_1 + timedelta(days=(6 - mar_1.weekday() + 7) % 7 + 7)
    # DST ends: 1st Sunday of November
    nov_1 = date(d.year, 11, 1)
    dst_end = nov_1 + timedelta(days=(6 - nov_1.weekday() + 7) % 7)
    return dst_start <= d < dst_end


def _et_time(d: date, us_hour: int = 8, us_minute: int = 30) -> str:
    """将美国东部时间转为北京时间字符串"""
    offset = 12 if _is_dst(d) else 13
    beijing_hour = us_hour + offset
    return f"{beijing_hour:02d}:{us_minute:02d}"


# ── 指标定义 ──────────────────────────────────────────────
# name, impact, gold_msg, stock_msg

INDICATOR_META = {
    "FOMC": {
        "impact": "high",
        "gold": "鹰派 → 利空金价\n鸽派 → 利好金价",
        "stock": "鹰派 → 利空股市\n鸽派 → 利好股市",
    },
    "NFP": {
        "impact": "high",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利好股市（软着陆）\n低于预期 → 利空股市",
    },
    "CPI": {
        "impact": "high",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市（加息预期）\n低于预期 → 利好股市",
    },
    "PCE": {
        "impact": "medium",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市\n低于预期 → 利好股市",
    },
    "PPI": {
        "impact": "medium",
        "gold": "超预期 → 利空金价（通胀压力）\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市\n低于预期 → 利好股市",
    },
    "ISM_MFG": {
        "impact": "medium",
        "gold": "超预期 → 利空金价（经济强）\n低于预期 → 利好金价（避险）",
        "stock": "超预期 → 利好股市（经济强）\n低于预期 → 利空股市（衰退信号）",
    },
    "ISM_SVC": {
        "impact": "low",
        "gold": "超预期 → 略利空金价\n低于预期 → 略利好金价",
        "stock": "超预期 → 利好股市\n低于预期 → 利空股市",
    },
    "RetailSales": {
        "impact": "medium",
        "gold": "超预期 → 利空金价（消费强）\n低于预期 → 利好金价",
        "stock": "超预期 → 利好消费股\n低于预期 → 利空消费股",
    },
    "JOLTS": {
        "impact": "medium",
        "gold": "超预期 → 利空金价（就业紧）\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市（工资通胀）\n低于预期 → 利好股市",
    },
    "JoblessClaims": {
        "impact": "low",
        "gold": "高于预期 → 利好金价（避险）\n低于预期 → 利空金价",
        "stock": "高于预期 → 利空股市\n低于预期 → 利好股市",
    },
    "GDP": {
        "impact": "medium",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利好股市\n低于预期 → 利空股市",
    },
    "Michigan": {
        "impact": "low",
        "gold": "通胀预期升 → 利空金价\n通胀预期降 → 利好金价",
        "stock": "通胀预期升 → 利空股市\n信心强 → 利好消费股",
    },
}

INDICATOR_NAMES = {
    "FOMC": "FOMC 利率决议",
    "NFP": "非农就业 (NFP)",
    "CPI": "CPI 消费者物价指数",
    "PCE": "核心 PCE 物价指数",
    "PPI": "PPI 生产者物价指数",
    "ISM_MFG": "ISM 制造业 PMI",
    "ISM_SVC": "ISM 服务业 PMI",
    "RetailSales": "零售销售月率",
    "JOLTS": "JOLTs 职位空缺",
    "JoblessClaims": "初请失业金人数",
    "GDP": "GDP 季率",
    "Michigan": "密歇根消费者信心初值",
}


# ── FRED 前值数据 ─────────────────────────────────────────
# 每个指标对应的 FRED series_id 和计算方式

FRED_SERIES = {
    "CPI":     ("CPIAUCSL", "yoy_pct"),   # 消费者物价指数 → 年率
    "NFP":     ("PAYEMS",   "mom_chg"),   # 非农就业总人数 → 月度变化(千)
    "PCE":     ("PCEPI",    "yoy_pct"),   # PCE 物价指数 → 年率
    "GDP":     ("GDP",      "level"),     # GDP → 季度值(十亿)
    "PPI":     ("WPSFD4131","yoy_pct"),   # PPI 最终需求 → 年率
    "JoblessClaims": ("ICSA", "level"),   # 初请失业金(周) → 万人
    "RetailSales":   ("RSAFS", "mom_pct"), # 零售销售 → 月率
    "ISM_MFG":  ("NAPM",    "level"),     # ISM 制造业 PMI → 指数
}

# FRED 数据缓存（一次请求全拿，避免多次调 API）
_fred_cache: dict[str, list[tuple[str, float]]] = {}

def _fetch_fred_series(series_id: str, api_key: str) -> list[tuple[str, float]]:
    """获取 FRED 序列数据，返回 [(date_str, value), ...] 按日期降序"""
    if series_id in _fred_cache:
        return _fred_cache[series_id]
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": series_id, "sort_order": "desc", "limit": 25,
            "api_key": api_key, "file_type": "json",
        }, timeout=15)
        data = r.json()
        values = [(o["date"], float(o["value"])) for o in data.get("observations", [])
                  if o["value"] != "."]
        _fred_cache[series_id] = values
        return values
    except Exception as e:
        logger.warning(f"FRED {series_id} 获取失败: {e}")
        return []


def _calc_previous(key: str, api_key: str) -> str:
    """根据指标 key 计算前值字符串，如 '3.8%' 或 '+172k'"""
    if key not in FRED_SERIES:
        return ""
    series_id, method = FRED_SERIES[key]
    values = _fetch_fred_series(series_id, api_key)
    if len(values) < 2:
        return ""

    curr_val = values[0][1]
    prev_val = values[1][1]

    if method == "yoy_pct":
        # 找去年同月
        curr_date = values[0][0]
        year = int(curr_date[:4])
        month = curr_date[5:7]
        target = f"{year-1}-{month}"
        matches = [v for v in values if v[0].startswith(target)]
        if matches:
            yoy = (curr_val / matches[0][1] - 1) * 100
            return f"{yoy:+.1f}%"
        return ""

    elif method == "mom_chg":
        change = curr_val - prev_val
        return f"{change:+.0f}k"

    elif method == "mom_pct":
        if prev_val != 0:
            mom = (curr_val / prev_val - 1) * 100
            return f"{mom:+.1f}%"
        return ""

    elif method == "level":
        # 直接显示值
        if key == "JoblessClaims":
            return f"{curr_val/10000:.1f}万"  # 直接数 → 万
        elif key == "ISM_MFG":
            return f"{curr_val:.1f}"
        elif key == "GDP":
            return f"{curr_val:.0f}B"
        return f"{curr_val:.1f}"

    return ""


# ── 2026 下半年精确排期（硬编码） ─────────────────────────
# 格式: { date: [(key, time_str, notes), ...] }
# 时间已是北京时间

CALENDAR_2026_H2: dict[date, list[tuple[str, str, str]]] = {}

def _add(d: date, key: str, time_str: str):
    CALENDAR_2026_H2.setdefault(d, []).append((key, time_str, ""))

# ── 6月 ──
_add(date(2026,6,10), "CPI", "20:30")
_add(date(2026,6,11), "PPI", "20:30")
_add(date(2026,6,17), "RetailSales", "20:30")
_add(date(2026,6,18), "FOMC", "02:00")
_add(date(2026,6,25), "PCE", "20:30")

# ── 7月 ──
_add(date(2026,7,2), "NFP", "20:30")  # 独立日提前
_add(date(2026,7,14), "CPI", "20:30")
_add(date(2026,7,15), "PPI", "20:30")
_add(date(2026,7,16), "RetailSales", "20:30")
_add(date(2026,7,30), "PCE", "20:30")
_add(date(2026,7,30), "GDP", "20:30")
_add(date(2026,7,30), "FOMC", "02:00")

# ── 8月 ──
_add(date(2026,8,7), "NFP", "20:30")
_add(date(2026,8,12), "CPI", "20:30")
_add(date(2026,8,13), "PPI", "20:30")
_add(date(2026,8,14), "RetailSales", "20:30")
_add(date(2026,8,26), "PCE", "20:30")

# ── 9月 ──
_add(date(2026,9,4), "NFP", "20:30")
_add(date(2026,9,10), "PPI", "20:30")
_add(date(2026,9,11), "CPI", "20:30")
_add(date(2026,9,16), "RetailSales", "20:30")
_add(date(2026,9,17), "FOMC", "02:00")
_add(date(2026,9,30), "PCE", "20:30")

# ── 10月 ──
_add(date(2026,10,2), "NFP", "20:30")
_add(date(2026,10,14), "CPI", "20:30")
_add(date(2026,10,15), "RetailSales", "20:30")
_add(date(2026,10,29), "FOMC", "02:00")
_add(date(2026,10,29), "PCE", "20:30")
_add(date(2026,10,29), "GDP", "20:30")

# ── 11月（冬令时开始） ──
_add(date(2026,11,6), "NFP", "21:30")
_add(date(2026,11,10), "CPI", "21:30")
_add(date(2026,11,17), "RetailSales", "21:30")
_add(date(2026,11,25), "PCE", "21:30")  # 感恩节提前

# ── 12月 ──
_add(date(2026,12,4), "NFP", "21:30")
_add(date(2026,12,10), "CPI", "21:30")
_add(date(2026,12,10), "FOMC", "03:00")  # 冬令时 2PM ET = 3AM 北京
_add(date(2026,12,16), "RetailSales", "21:30")
_add(date(2026,12,23), "PCE", "21:30")  # 圣诞提前


# ── FOMC 2026 上半年（官网已验证，北京时间） ─────────────
FOMC_H1_BEIJING = {
    date(2026, 1, 29),  # Jan 27-28 → Jan 29 Beijing
    date(2026, 3, 19),  # Mar 17-18 → Mar 19 Beijing
    date(2026, 4, 30),  # Apr 28-29 → Apr 30 Beijing
}


# ── 算法兜底 ──────────────────────────────────────────────

def _first_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def _weekday_near(year: int, month: int, day: int) -> date:
    d = date(year, month, day)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _calc_fallback(mon: date, fri: date) -> list[dict]:
    """算法计算补充指标（Jobless Claims, ISM PMI, JOLTs, Michigan 等）"""
    results = []

    # ── 初请失业金：每周四 ──
    d = mon
    while d <= fri:
        if d.weekday() == 3:
            results.append({
                "date": d, "key": "JoblessClaims",
                "time": _et_time(d),  # 8:30 ET
            })
        d += timedelta(days=1)

    # ── NFP 算法兜底（仅当该月无硬编码 NFP 时） ──
    nfp_hardcoded_months = {
        d.month for d, items in CALENDAR_2026_H2.items()
        if any(x[0] == "NFP" for x in items)
    }
    for m in sorted({mon.month, fri.month}):
        if m in nfp_hardcoded_months:
            continue  # 已被硬编码覆盖
        nfp = _first_weekday(mon.year if m >= mon.month else fri.year, m, 4)
        if mon <= nfp <= fri:
            results.append({
                "date": nfp, "key": "NFP",
                "time": _et_time(nfp),
            })

    # ── ISM 制造业 PMI：每月第 1 个工作日 ──
    for m in sorted({mon.month, fri.month}):
        ism = date(mon.year if m >= mon.month else fri.year, m, 1)
        while ism.weekday() >= 5:
            ism += timedelta(days=1)
        if mon <= ism <= fri:
            results.append({
                "date": ism, "key": "ISM_MFG",
                "time": _et_time(ism, 10, 0),  # 10:00 ET
            })

    # ── ISM 服务业 PMI：每月第 3 个工作日 ──
    for m in sorted({mon.month, fri.month}):
        ism = date(mon.year if m >= mon.month else fri.year, m, 1)
        workdays = 0
        while workdays < 3:
            if ism.weekday() < 5:
                workdays += 1
            if workdays < 3:
                ism += timedelta(days=1)
        if mon <= ism <= fri:
            results.append({
                "date": ism, "key": "ISM_SVC",
                "time": _et_time(ism, 10, 0),
            })

    # ── JOLTs：每月第一周（大约 1-3 号附近） ──
    for m in sorted({mon.month, fri.month}):
        jolts = _weekday_near(mon.year if m >= mon.month else fri.year, m, 2)
        while jolts.day > 7:
            jolts -= timedelta(days=7)
        while jolts.month != m:
            jolts += timedelta(days=1)
        if mon <= jolts <= fri:
            results.append({
                "date": jolts, "key": "JOLTS",
                "time": _et_time(jolts, 10, 0),
            })

    # ── 密歇根消费者信心初值：每月第 2 或第 3 个周五 ──
    for m in sorted({mon.month, fri.month}):
        d2 = date(mon.year if m >= mon.month else fri.year, m, 8)
        while d2.weekday() != 4:
            d2 += timedelta(days=1)
        if mon <= d2 <= fri:
            results.append({
                "date": d2, "key": "Michigan",
                "time": _et_time(d2, 10, 0),
            })

    # ── FOMC 上半年 ──
    for fomc_date in FOMC_H1_BEIJING:
        if mon <= fomc_date <= fri:
            results.append({
                "date": fomc_date, "key": "FOMC",
                "time": "03:00" if fomc_date.month <= 3 else "02:00",
            })

    return results


# ── 汇总 ──────────────────────────────────────────────────

def _this_week_range() -> tuple[date, date]:
    now = datetime.now(BEIJING_TZ).date()
    mon = now - timedelta(days=now.weekday())
    return mon, mon + timedelta(days=4)


def get_week_indicators(mon: date, fri: date, fred_api_key: str = "") -> list[dict]:
    """汇总硬编码 + 算法兜底，返回本周指标列表"""
    by_date: dict[date, list[dict]] = {}
    seen: set[tuple[date, str]] = set()

    def add(d: date, key: str, time_str: str, estimated: bool):
        if (d, key) in seen:
            return
        seen.add((d, key))
        meta = INDICATOR_META[key]
        previous = _calc_previous(key, fred_api_key) if fred_api_key else ""
        by_date.setdefault(d, []).append({
            "date": d,
            "name": INDICATOR_NAMES[key],
            "time": time_str,
            "impact": meta["impact"],
            "estimated": estimated,
            "previous": previous,
            "gold": meta["gold"],
            "stock": meta["stock"],
        })

    # 1. 硬编码日历
    for d, items in CALENDAR_2026_H2.items():
        if mon <= d <= fri:
            for key, time_str, _ in items:
                add(d, key, time_str, estimated=False)

    # 2. 算法兜底
    for r in _calc_fallback(mon, fri):
        add(r["date"], r["key"], r["time"], estimated=True)

    # 3. 按日期 + 影响排序
    result = []
    for d in sorted(by_date.keys()):
        items = by_date[d]
        items.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["impact"]])
        result.extend(items)

    return result


# ── 格式化 ────────────────────────────────────────────────

def _direction_hint(name: str, prev: str) -> str:
    """根据前值给出一句话市场影响提示"""
    hints = {
        "CPI": "通胀仍高 → 关注是否超预期上行",
        "PCE": "PCE 是美联储最看重的通胀指标",
        "PPI": "PPI 是 CPI 的先行指标，关注传导效应",
        "非农": "就业强劲 → 降息预期推迟；走弱 → 降息预期提前",
        "零售": "消费是美国经济 70%，直接影响 GDP 预期",
        "GDP": "观察经济是否在放缓通道",
        "初请": "连续走高需警惕就业市场恶化",
        "ISM": "50 = 荣枯线，低于 50 为收缩信号",
        "JOLTs": "职位空缺下降 → 就业市场降温",
        "密歇根": "通胀预期是美联储关注的前瞻指标",
        "FOMC": "本次重点关注点阵图和鲍威尔措辞",
    }
    for kw, hint in hints.items():
        if kw in name:
            return f"💡 {hint}"
    return ""


def format_weekly(indicators: list[dict]) -> str:
    mon, fri = _this_week_range()
    impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    # 按天分组
    by_date: dict[date, list[dict]] = {}
    for r in indicators:
        by_date.setdefault(r["date"], []).append(r)

    lines = [f"## 📅 本周经济指标（{mon.strftime('%m/%d')} - {fri.strftime('%m/%d')}）\n"]
    total = len(indicators)
    lines.append(f"共 **{total}** 个事件\n")

    d = mon
    while d <= fri:
        day_label = f"{d.strftime('%m/%d')} {WD[d.weekday()]}"
        lines.append(f"### {day_label}")

        if d not in by_date:
            lines.append("🟢 _暂无_\n")
        else:
            for r in by_date[d]:
                emoji = impact_emoji.get(r["impact"], "⚪")
                est = "（预计）" if r.get("estimated") else ""
                prev = r.get("previous", "")
                lines.append(f"{emoji} **{r['name']}**{est}  {r['time']}（北京）")
                if prev:
                    lines.append(f"　前值 {prev}")
                    # 一行方向判断
                    direction = _direction_hint(r["name"], prev)
                    if direction:
                        lines.append(f"　{direction}")
                lines.append(f"　📈 股市：{r['stock'].split(chr(10))[0]}")
                lines.append(f"　🥇 金价：{r['gold'].split(chr(10))[0]}")
                lines.append("")
        lines.append("")
        d += timedelta(days=1)

    lines.append("> 🤖 每周一早 8:00 自动推送 | 精确排期 + 算法兜底")
    return "\n".join(lines)


def send_wechat(send_key: str, title: str, content: str) -> bool:
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        return resp.json().get("code") == 0
    except requests.RequestException as e:
        logger.error(f"Server酱 失败: {e}")
        return False


# ── 入口 ──────────────────────────────────────────────────

def main():
    config = load_config()

    if "--test" in sys.argv:
        send_wechat(config["server_chan_key"],
                     "🧪 测试消息", "配置正确！")
        print("已发送测试")
        return

    mon, fri = _this_week_range()
    indicators = get_week_indicators(mon, fri, config["fred_api_key"])
    logger.info(f"本周 {mon} ~ {fri}，共 {len(indicators)} 个指标")
    for r in indicators:
        logger.info(f"  {r['date']} {r['name']} ({r['time']})")

    if not indicators:
        logger.info("本周无指标")
        return

    content = format_weekly(indicators)

    if "--dry-run" in sys.argv:
        print(content)
        return

    title = f"📅 本周经济指标（{mon.strftime('%m/%d')}-{fri.strftime('%m/%d')}）"
    ok = send_wechat(config["server_chan_key"], title, content)
    logger.info("推送成功" if ok else "推送失败")


if __name__ == "__main__":
    main()
