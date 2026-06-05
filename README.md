# 经济指标发布提醒系统

每天早上 8:00（北京时间）推送微信消息，告诉你**今晚美国要发布什么经济指标**，以及这些指标对股市、金价的影响。

## 部署

### 1. GitHub Secrets

仓库 Settings → Secrets and variables → Actions → 添加：

| Secret | 值 |
|--------|-----|
| `FRED_API_KEY` | FRED API Key |
| `SERVER_CHAN_KEY` | Server酱 SendKey |

### 2. 启用

GitHub Actions 默认启用。每天 UTC 00:00（北京 08:00）自动运行。周末跳过。

手动触发：Actions → Daily Economic Reminder → Run workflow。

## 本地测试

```bash
pip install requests

# 复制配置文件
cp config.example.json config.json
# 编辑 config.json 填入 key

python econ_reminder.py --dry-run   # 看今晚有什么
python econ_reminder.py --test      # 发测试消息到微信
python econ_reminder.py             # 正式运行
```

## 关注的指标

| 指标 | 频率 | 影响力 | 时间(北京) |
|------|------|--------|-----------|
| 🔴 非农 (NFP) | 每月第一个周五 | 极高 | 20:30 |
| 🔴 CPI | 每月中旬 | 极高 | 20:30 |
| 🔴 FOMC 利率决议 | 每年 8 次 | 极高 | 次日 02:00 |
| 🟡 核心 PCE | 每月底 | 高 | 20:30 |
| 🟡 初请失业金 | 每周四 | 中 | 20:30 |
| 🟡 GDP | 每季度 | 中 | 20:30 |
| 🟢 零售销售 | 每月中旬 | 低 | 20:30 |
