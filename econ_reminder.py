#!/usr/bin/env python3
"""
经济指标发布提醒系统

每天北京时间 8:00 运行，查询当晚美国要发布的经济指标，
通过 Server酱 推送到微信。

数据来源：FRED API (https://fred.stlouisfed.org/)
推送服务：Server酱 (https://sct.ftqq.com/)
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("econ-reminder")

# ── 配置 ──────────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))

# 优先从环境变量读取（GitHub Actions），否则从 config.json 读取
def load_config() -> dict:
    config = {
        "fred_api_key": os.environ.get("FRED_API_KEY", ""),
        "server_chan_key": os.environ.get("SERVER_CHAN_KEY", ""),
    }

    # 如果环境变量没有，尝试从 config.json 读取
    if not config["fred_api_key"] or not config["server_chan_key"]:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                file_config = json.load(f)
                if not config["fred_api_key"]:
                    config["fred_api_key"] = file_config.get("fred_api_key", "")
                if not config["server_chan_key"]:
                    config["server_chan_key"] = file_config.get("server_chan_key", "")

    return config


# ── 指标定义 ──────────────────────────────────────────────
# FRED release_id → 显示信息
INDICATORS = {
    50: {
        "name": "非农就业 (NFP)",
        "time": "20:30",
        "impact": "high",
        "frequency": "每月第一个周五",
        "description": "美国就业市场最全面的月度报告",
        "gold": "超预期 → 利空金价（美元走强）\n低于预期 → 利好金价（避险升温）",
        "stock": "超预期 → 利好股市（经济强劲）\n低于预期 → 利空股市（衰退担忧）",
    },
    10: {
        "name": "CPI 消费者物价指数",
        "time": "20:30",
        "impact": "high",
        "frequency": "每月中旬",
        "description": "衡量通货膨胀最核心的指标",
        "gold": "超预期 → 利空金价（加息预期升温）\n低于预期 → 利好金价（加息预期降温）",
        "stock": "超预期 → 利空股市（加息预期）\n低于预期 → 利好股市（宽松预期）",
    },
    101: {
        "name": "FOMC 利率决议",
        "time": "02:00（次日凌晨）",
        "impact": "high",
        "frequency": "每年 8 次",
        "description": "美联储议息会议利率决定",
        "gold": "鹰派（加息/偏鹰）→ 利空金价\n鸽派（降息/偏鸽）→ 利好金价",
        "stock": "鹰派 → 利空股市\n鸽派 → 利好股市（资金面宽松）",
    },
    54: {
        "name": "核心 PCE 物价指数",
        "time": "20:30",
        "impact": "medium",
        "frequency": "每月底",
        "description": "美联储最看重的通胀指标（个人消费支出）",
        "gold": "超预期 → 利空金价\n低于预期 → 利好金价",
        "stock": "超预期 → 利空股市（加息压力）\n低于预期 → 利好股市",
    },
    180: {
        "name": "初请失业金人数",
        "time": "20:30",
        "impact": "medium",
        "frequency": "每周四",
        "description": "美国劳动力市场的高频领先指标",
        "gold": "高于预期 → 利好金价（避险需求）\n低于预期 → 利空金价",
        "stock": "高于预期 → 利空股市\n低于预期 → 利好股市",
    },
    53: {
        "name": "GDP 季率",
        "time": "20:30",
        "impact": "medium",
        "frequency": "每季度",
        "description": "美国经济增长的综合指标",
        "gold": "超预期 → 利空金价（美元强）\n低于预期 → 利好金价（避险）",
        "stock": "超预期 → 利好股市\n低于预期 → 利空股市",
    },
    9: {
        "name": "零售销售月率",
        "time": "20:30",
        "impact": "low",
        "frequency": "每月中旬",
        "description": "美国消费支出强度指标",
        "gold": "超预期 → 略利空金价\n低于预期 → 略利好金价",
        "stock": "超预期 → 利好消费板块\n低于预期 → 利空消费板块",
    },
}


# ── 核心逻辑 ──────────────────────────────────────────────

def get_beijing_now() -> datetime:
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)


def is_weekend() -> bool:
    """判断今天是否为周六或周日（北京时间）"""
    return get_beijing_now().weekday() >= 5  # 5=Sat, 6=Sun


def fetch_todays_releases(api_key: str) -> list[dict]:
    """
    从 FRED API 获取今天发布的经济指标。
    返回匹配 INDICATORS 字典的指标列表。
    """
    today = get_beijing_now().strftime("%Y-%m-%d")
    logger.info(f"查询 FRED API，日期：{today}")

    url = "https://api.stlouisfed.org/fred/releases/dates"
    params = {
        "realtime_start": today,
        "realtime_end": today,
        "api_key": api_key,
        "file_type": "json",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error(f"FRED API 请求失败: {e}")
        return []

    tracked_ids = set(INDICATORS.keys())
    results = []

    for entry in data.get("release_dates", []):
        rid = entry.get("release_id")
        if rid in tracked_ids and entry["date"] == today:
            info = INDICATORS[rid]
            results.append({
                "release_id": rid,
                "date": entry["date"],
                "name": info["name"],
                "time": info["time"],
                "impact": info["impact"],
                "frequency": info.get("frequency", ""),
                "description": info.get("description", ""),
                "gold": info["gold"],
                "stock": info["stock"],
            })

    logger.info(f"找到 {len(results)} 个今日发布的指标")
    return results


def format_wechat_message(releases: list[dict]) -> str:
    """将指标列表格式化为微信推送内容（Markdown）"""
    now = get_beijing_now()
    date_str = now.strftime("%m月%d日")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]

    lines = [f"## 📅 今晚（{date_str} {weekday}）经济指标\n"]

    impact_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    for i, r in enumerate(releases):
        emoji = impact_emoji.get(r["impact"], "⚪")
        lines.append(f"### {emoji} {r['name']}  {r['time']}（北京时间）")
        lines.append(f"_{r['description']}_\n")

        lines.append("**📈 股市**")
        lines.append(r["stock"])
        lines.append("")
        lines.append("**🥇 金价**")
        lines.append(r["gold"])

        if i < len(releases) - 1:
            lines.append("\n---\n")

    lines.append(f"\n> 🤖 每天早 8:00 自动推送 | 数据来源：FRED")

    return "\n".join(lines)


def send_wechat(send_key: str, title: str, content: str) -> bool:
    """通过 Server酱 发送微信消息"""
    url = f"https://sctapi.ftqq.com/{send_key}.send"

    try:
        resp = requests.post(
            url,
            data={"title": title, "desp": content},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            logger.info("微信推送成功")
            return True
        else:
            logger.error(f"微信推送失败: {data}")
            return False
    except requests.RequestException as e:
        logger.error(f"Server酱 请求失败: {e}")
        return False


def main():
    config = load_config()

    # ── 参数处理 ──
    if "--test" in sys.argv:
        logger.info("发送测试消息...")
        ok = send_wechat(
            config["server_chan_key"],
            "🧪 经济指标提醒 - 测试消息",
            "如果你收到这条消息，说明 Server酱 配置正确！\n\n"
            "每天早上 8:00，你会收到今晚美国经济指标的发布提醒。",
        )
        print("✅ 测试消息发送成功" if ok else "❌ 测试消息发送失败")
        return

    # ── 周末跳过 ──
    if is_weekend():
        logger.info("周末，跳过推送")
        return

    # ── 查询今天发布的指标 ──
    releases = fetch_todays_releases(config["fred_api_key"])

    if not releases:
        logger.info("今天无关注的经济指标发布")
        return

    # ── dry-run 模式 ──
    if "--dry-run" in sys.argv:
        content = format_wechat_message(releases)
        print(content)
        return

    # ── 推送微信 ──
    now = get_beijing_now()
    title = f"📅 今晚经济指标提醒（{now.strftime('%m/%d')}）"

    # 高影响指标在前面
    releases.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["impact"]])

    content = format_wechat_message(releases)
    send_wechat(config["server_chan_key"], title, content)

    # 输出指标摘要
    impact_labels = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for r in releases:
        label = impact_labels.get(r["impact"], "⚪")
        logger.info(f"  {label} {r['name']} ({r['time']})")
    logger.info("完成")


if __name__ == "__main__":
    main()
