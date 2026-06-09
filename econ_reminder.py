#!/usr/bin/env python3
"""
经济指标发布提醒系统

每周一北京时间 8:00 运行，推送本周（周一至周五）美国要发布的
经济指标，包括对股市、金价的影响分析。

数据来源：FRED API (https://fred.stlouisfed.org/)
推送服务：Server酱 (https://sct.ftqq.com/)
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

import requests

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("econ-reminder")

# ── 配置 ──────────────────────────────────────────────────
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


# ── 指标定义 ──────────────────────────────────────────────
INDICATORS = {
    50: {
        "name": "非农就业 (NFP)",
        "time": "20:30",
        "impact": "high",
        "description": "美国就业市场最全面的月度报告",
        "gold": "超预期 → 利空金价（美元走强）\n低于预期 → 利好金价（避险升温）",
        "stock": "超预期 → 利好股市（经济强劲）\n低于预期 → 利空股市（衰退担忧）",
    },
    10: {
        "name": "CPI 消费者物价指数",
        "time": "20:30",
        "impact": "high",
        "description": "衡量通货膨胀最核心的指标",
        "gold": "超预期 → 利空金价（加息预期升温）\n低于预期 → 利好金价（加息预期降温）",
        "stock": "超预期 → 利空股市（加息预期）\n低于预期 → 利好股市（宽松预期）",
    },
    101: {
        "name": "FOMC 利率决议",
        "time": "02:00（次日凌晨）",
        "impact": "high",
        "description": "美联储议息会议利率决定",
        "gold": "鹰派（加息/偏鹰）→ 利空金价\n鸽派（降息/偏鸽）→ 利好金价",
        "stock": "鹰派 → 利空股市\n鸽派 → 利好股市（资金面宽松）",
    },
    54: {
        "name": "核心 PCE 物价指数",
        "time": "20:30",
        "impact": "medium",
        "description": "美联储最看重的通胀指标",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市（加息压力）\n低于预期 → 利好股市",
    },
    180: {
        "name": "初请失业金人数",
        "time": "20:30",
        "impact": "medium",
        "description": "美国劳动力市场高频领先指标",
        "gold": "高于预期 → 利好金价（避险需求）\n低于预期 → 利空金价",
        "stock": "高于预期 → 利空股市\n低于预期 → 利好股市",
    },
    53: {
        "name": "GDP 季率",
        "time": "20:30",
        "impact": "medium",
        "description": "美国经济增长综合指标",
        "gold": "超预期 → 利空金价（美元强）\n低于预期 → 利好金价（避险）",
        "stock": "超预期 → 利好股市\n低于预期 → 利空股市",
    },
    9: {
        "name": "零售销售月率",
        "time": "20:30",
        "impact": "low",
        "description": "美国消费支出强度指标",
        "gold": "超预期 → 略利空金价\n低于预期 → 略利好金价",
        "stock": "超预期 → 利好消费板块\n低于预期 → 利空消费板块",
    },
}


# ── 核心逻辑 ──────────────────────────────────────────────

def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _this_week_range() -> tuple[str, str, str, str]:
    """返回本周周一-周五的日期范围和显示字符串"""
    now = _beijing_now()
    monday = now - timedelta(days=now.weekday())  # 本周一
    friday = monday + timedelta(days=4)            # 本周五

    monday_str = monday.strftime("%Y-%m-%d")
    friday_str = friday.strftime("%Y-%m-%d")
    display_start = monday.strftime("%m/%d")
    display_end = friday.strftime("%m/%d")

    return monday_str, friday_str, display_start, display_end


def fetch_week_releases(api_key: str) -> dict[str, list[dict]]:
    """查询本周一至周五的指标，按日期分组返回"""
    monday_str, friday_str, _, _ = _this_week_range()
    logger.info(f"查询 FRED API，范围：{monday_str} ~ {friday_str}")

    url = "https://api.stlouisfed.org/fred/releases/dates"
    params = {
        "realtime_start": monday_str,
        "realtime_end": friday_str,
        "api_key": api_key,
        "file_type": "json",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error(f"FRED API 请求失败: {e}")
        return {}

    tracked_ids = set(INDICATORS.keys())
    seen_rids: set[int] = set()  # 同指标只保留最早日期
    by_date: dict[str, list[dict]] = {}

    for entry in data.get("release_dates", []):
        rid = entry.get("release_id")
        date = entry.get("date", "")
        if rid in tracked_ids and monday_str <= date <= friday_str:
            if rid in seen_rids:
                continue  # 跳过同一指标的后续日期
            seen_rids.add(rid)
            info = INDICATORS[rid]
            by_date.setdefault(date, []).append({
                "name": info["name"],
                "time": info["time"],
                "impact": info["impact"],
                "description": info["description"],
                "gold": info["gold"],
                "stock": info["stock"],
            })

    logger.info(f"本周共 {sum(len(v) for v in by_date.values())} 个指标，分布在 {len(by_date)} 天")
    return by_date


def format_weekly_message(by_date: dict[str, list[dict]]) -> str:
    """格式化为本周预览消息"""
    _, _, display_start, display_end = _this_week_range()
    impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    lines = [f"## 📅 本周经济指标预览（{display_start} - {display_end}）\n"]

    for date_str in sorted(by_date.keys()):
        releases = by_date[date_str]
        # 按影响排序
        releases.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["impact"]])

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_label = f"{dt.strftime('%m/%d')} {WEEKDAY_NAMES[dt.weekday()]}"

        lines.append(f"### 📆 {day_label}")

        for r in releases:
            emoji = impact_emoji.get(r["impact"], "⚪")
            lines.append(f"{emoji} **{r['name']}**  {r['time']}（北京）")
            lines.append(f"　_{r['description']}_")
            lines.append(f"　📈 股市：{r['stock'].split(chr(10))[0]}")
            lines.append(f"　🥇 金价：{r['gold'].split(chr(10))[0]}")
            lines.append("")

        lines.append("")

    lines.append(f"> 🤖 每周一早 8:00 自动推送 | 数据来源：FRED")
    return "\n".join(lines)


def send_wechat(send_key: str, title: str, content: str) -> bool:
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        return resp.json().get("code") == 0
    except requests.RequestException as e:
        logger.error(f"Server酱 请求失败: {e}")
        return False


def main():
    config = load_config()

    if "--test" in sys.argv:
        logger.info("发送测试消息...")
        ok = send_wechat(
            config["server_chan_key"],
            "🧪 经济指标提醒 - 测试消息",
            "如果你收到这条消息，说明 Server酱 配置正确！",
        )
        print("✅ 测试消息发送成功" if ok else "❌ 测试消息发送失败")
        return

    # 查询本周
    by_date = fetch_week_releases(config["fred_api_key"])

    if not by_date:
        logger.info("本周无关注的经济指标发布")
        return

    if "--dry-run" in sys.argv:
        print(format_weekly_message(by_date))
        return

    # 推送微信
    _, _, display_start, display_end = _this_week_range()
    total = sum(len(v) for v in by_date.values())
    title = f"📅 本周经济指标（{display_start}-{display_end}）共{total}个"
    content = format_weekly_message(by_date)
    ok = send_wechat(config["server_chan_key"], title, content)

    logger.info("完成" if ok else "推送失败")
    for date_str in sorted(by_date.keys()):
        for r in by_date[date_str]:
            logger.info(f"  {date_str}  {r['name']} ({r['time']})")


if __name__ == "__main__":
    main()
