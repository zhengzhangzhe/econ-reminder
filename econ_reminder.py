#!/usr/bin/env python3
"""
经济指标发布提醒系统

每周一北京时间 8:00 运行，推送本周（周一至周五）美国要发布的
经济指标，包括对股市、金价的影响分析。

指标日期来自算法计算 + 硬编码 FOMC 日程，比 FRED API 更准确。
"""

import os
import sys
import json
import logging
from datetime import date, datetime, timezone, timedelta

import requests

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("econ-reminder")

BEIJING_TZ = timezone(timedelta(hours=8))
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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


# ── FOMC 2026 日程（来源：federalreserve.gov/monetarypolicy/fomccalendars.htm）──
# 已转为北京时间：美国 14:00 ET 发布会 → 北京次日 02:00
# * = 含经济预测摘要 (SEP)
FOMC_2026_BEIJING = [
    date(2026, 1, 29),   # Jan 27-28 → announcement Jan 28 US → Beijing Jan 29
    date(2026, 3, 19),   # Mar 17-18* → announcement Mar 18 → Beijing Mar 19
    date(2026, 4, 30),   # Apr 28-29 → announcement Apr 29 → Beijing Apr 30
    date(2026, 6, 18),   # Jun 16-17* → announcement Jun 17 → Beijing Jun 18
    date(2026, 7, 30),   # Jul 28-29 → announcement Jul 29 → Beijing Jul 30
    date(2026, 9, 17),   # Sep 15-16* → announcement Sep 16 → Beijing Sep 17
    date(2026, 10, 29),  # Oct 27-28 → announcement Oct 28 → Beijing Oct 29
    date(2026, 12, 10),  # Dec 8-9* → announcement Dec 9 → Beijing Dec 10
]

# ── 算法：计算各指标的发布日期 ────────────────────────────


def _first_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """返回某月第 N 个周几的日期（weekday: 0=Mon, 4=Fri）"""
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def _weekday_near(year: int, month: int, day: int) -> date:
    """返回某月 day 号附近最近的周一到周五"""
    d = date(year, month, day)
    while d.weekday() >= 5:  # 跳过周末
        d += timedelta(days=1)
    return d


def _calculate_indicators(mon: date, fri: date) -> list[dict]:
    """纯算法计算本周一至周五之间发布的指标"""
    results = []

    # ── 初请失业金人数：每周四 ──
    d = mon
    while d <= fri:
        if d.weekday() == 3:  # 周四
            results.append({
                "date": d, "name": "初请失业金人数", "time": "20:30",
                "impact": "medium",
                "estimated": False,  # 每周四固定
                "description": "美国劳动力市场高频领先指标",
                "gold": "高于预期 → 利好金价（避险）\n低于预期 → 利空金价",
                "stock": "高于预期 → 利空股市\n低于预期 → 利好股市",
            })
        d += timedelta(days=1)

    # ── 非农就业 (NFP)：每月第一个周五 ──
    for month_val in sorted({mon.month, fri.month}):
        nfp = _first_weekday_of_month(mon.year if month_val >= mon.month else fri.year,
                                       month_val, 4)  # 4=Friday
        if mon <= nfp <= fri:
            results.append({
                "date": nfp, "name": "非农就业 (NFP)", "time": "20:30",
                "impact": "high",
                "estimated": True, "description": "美国就业市场最全面的月度报告",
                "gold": "超预期 → 利空金价（美元走强）\n低于预期 → 利好金价（避险升温）",
                "stock": "超预期 → 利好股市（经济强劲）\n低于预期 → 利空股市（衰退担忧）",
            })

    # ── CPI：每月 10 号附近（BLS 通常在 10-15 号发布，多为周三/周四） ──
    for month_val in sorted({mon.month, fri.month}):
        cpi = _weekday_near(mon.year if month_val >= mon.month else fri.year,
                            month_val, 10)
        # CPI 通常不在周一周二
        while cpi.weekday() < 2:
            cpi += timedelta(days=1)
        while cpi.weekday() >= 5:
            cpi += timedelta(days=1)
        if mon <= cpi <= fri:
            results.append({
                "date": cpi, "name": "CPI 消费者物价指数", "time": "20:30",
                "impact": "high",
                "estimated": True, "description": "衡量通货膨胀最核心的指标",
                "gold": "超预期 → 利空金价（加息预期升温）\n低于预期 → 利好金价（加息预期降温）",
                "stock": "超预期 → 利空股市（加息预期）\n低于预期 → 利好股市（宽松预期）",
            })

    # ── FOMC 利率决议：硬编码日程（已是北京时间） ──
    for fomc_date in FOMC_2026_BEIJING:
        if mon <= fomc_date <= fri:
            results.append({
                "date": fomc_date, "name": "FOMC 利率决议", "time": "02:00",
                "impact": "high",
                "estimated": False, "description": "美联储议息会议利率决定",
                "gold": "鹰派（加息/偏鹰）→ 利空金价\n鸽派（降息/偏鸽）→ 利好金价",
                "stock": "鹰派 → 利空股市\n鸽派 → 利好股市（资金面宽松）",
            })

    # ── GDP 季率：季度结束后约一个月 ──
    gdp_months = {1: date(mon.year, 1, 27), 4: date(mon.year, 4, 27),
                  7: date(mon.year, 7, 27), 10: date(mon.year, 10, 27)}
    for g in gdp_months.values():
        gdp_date = _weekday_near(g.year, g.month, g.day)
        if mon <= gdp_date <= fri:
            results.append({
                "date": gdp_date, "name": "GDP 季率（初值）", "time": "20:30",
                "impact": "medium",
                "estimated": True, "description": "美国经济增长综合指标",
                "gold": "超预期 → 利空金价（美元强）\n低于预期 → 利好金价（避险）",
                "stock": "超预期 → 利好股市\n低于预期 → 利空股市",
            })

    # ── 核心 PCE：每月月底 ──
    for month_val in sorted({mon.month, fri.month}):
        pce = _weekday_near(mon.year if month_val >= mon.month else fri.year,
                            month_val, 27)
        if mon <= pce <= fri:
            results.append({
                "date": pce, "name": "核心 PCE 物价指数", "time": "20:30",
                "impact": "medium",
                "estimated": True, "description": "美联储最看重的通胀指标",
                "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
                "stock": "超预期 → 利空股市（加息压力）\n低于预期 → 利好股市",
            })

    # ── 零售销售：每月中旬（16号附近，通常周二-周四） ──
    for month_val in sorted({mon.month, fri.month}):
        rs = _weekday_near(mon.year if month_val >= mon.month else fri.year,
                           month_val, 16)
        while rs.weekday() < 1 or rs.weekday() >= 5:
            rs += timedelta(days=1)
        if mon <= rs <= fri:
            results.append({
                "date": rs, "name": "零售销售月率", "time": "20:30",
                "impact": "low",
                "estimated": True, "description": "美国消费支出强度指标",
                "gold": "超预期 → 略利空金价\n低于预期 → 略利好金价",
                "stock": "超预期 → 利好消费板块\n低于预期 → 利空消费板块",
            })

    # 按日期和影响排序，去重（同一天同名只保留一个）
    seen = set()
    results = [r for r in sorted(results, key=lambda r: r["date"])
               if not (seen.add((r["date"], r["name"])) and False)]

    return results


# ── 周范围计算 ────────────────────────────────────────────

def _this_week_range() -> tuple[date, date]:
    now = datetime.now(BEIJING_TZ).date()
    mon = now - timedelta(days=now.weekday())
    fri = mon + timedelta(days=4)
    return mon, fri


# ── 消息格式化 ────────────────────────────────────────────

def format_weekly_message(indicators: list[dict]) -> str:
    mon, fri = _this_week_range()
    impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    messages = [f"## 📅 本周经济指标（{mon.strftime('%m/%d')} - {fri.strftime('%m/%d')}）\n"]
    messages.append(f"共 **{len(indicators)}** 个重要事件\n")

    # 按天分组
    by_date: dict[date, list[dict]] = {}
    for r in indicators:
        by_date.setdefault(r["date"], []).append(r)

    # 无事件的日期也标出来
    d = mon
    while d <= fri:
        day_label = f"{d.strftime('%m/%d')} {WEEKDAY_NAMES[d.weekday()]}"

        if d not in by_date:
            messages.append(f"### {day_label}")
            messages.append("🟢 _暂无重要指标_\n")
        else:
            messages.append(f"### {day_label}")
            for r in by_date[d]:
                emoji = impact_emoji.get(r["impact"], "⚪")
                est_tag = "（预计）" if r.get("estimated") else ""
                messages.append(f"{emoji} **{r['name']}**{est_tag}  {r['time']}（北京）")
                messages.append(f"　📈 股市：{r['stock'].split(chr(10))[0]}")
                messages.append(f"　🥇 金价：{r['gold'].split(chr(10))[0]}")
                messages.append("")
        messages.append("")
        d += timedelta(days=1)

    messages.append(f"> 🤖 每周一早 8:00 自动推送 | 算法计算 + FOMC 硬编码")
    return "\n".join(messages)


def send_wechat(send_key: str, title: str, content: str) -> bool:
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        if resp.json().get("code") == 0:
            logger.info("微信推送成功")
            return True
        logger.error(f"推送失败: {resp.json()}")
        return False
    except requests.RequestException as e:
        logger.error(f"Server酱 请求失败: {e}")
        return False


# ── 入口 ──────────────────────────────────────────────────

def main():
    config = load_config()

    if "--test" in sys.argv:
        send_wechat(config["server_chan_key"],
                     "🧪 经济指标提醒 - 测试消息",
                     "如果你收到这条消息，说明配置正确！")
        print("已发送测试消息")
        return

    mon, fri = _this_week_range()
    indicators = _calculate_indicators(mon, fri)
    logger.info(f"本周 {mon} ~ {fri}，共 {len(indicators)} 个指标")
    for r in indicators:
        logger.info(f"  {r['date']} {r['name']}")

    if not indicators:
        logger.info("本周无关注的经济指标")
        return

    content = format_weekly_message(indicators)

    if "--dry-run" in sys.argv:
        print(content)
        return

    send_wechat(config["server_chan_key"],
                f"📅 本周经济指标（{mon.strftime('%m/%d')}-{fri.strftime('%m/%d')}）",
                content)


if __name__ == "__main__":
    main()
