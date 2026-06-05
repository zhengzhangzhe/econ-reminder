# 经济指标发布提醒系统

每天早上 8:00（北京时间）准时推送微信消息，告诉你**今晚美国要发布什么经济指标**，以及这些指标对股市、金价的影响。

## 架构

```
cron-job.org（准时，UTC 00:00 = 北京 08:00）
    ↓ POST /repos/{owner}/{repo}/dispatches
GitHub Actions（repository_dispatch 触发）
    ↓ 运行 econ_reminder.py
FRED API → Server酱 → 微信
```

## 部署步骤

### 1. 创建 GitHub 仓库

推送代码到你的 GitHub 仓库。在 `Settings → Secrets and variables → Actions` 添加：

| Secret | 值 |
|--------|-----|
| `FRED_API_KEY` | 你的 FRED API Key |
| `SERVER_CHAN_KEY` | 你的 Server酱 SendKey |
| `GH_PAT` | GitHub Personal Access Token（见下方） |

### 2. 创建 GitHub PAT

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token → 选 `repo` 权限
3. 复制生成的 token

### 3. 设置 cron-job.org

1. 注册 https://cron-job.org/
2. 创建 Cron Job：
   - **URL**: `https://api.github.com/repos/你的用户名/你的仓库名/dispatches`
   - **Method**: `POST`
   - **Headers**: 
     ```
     Authorization: token ghp_xxxxxxxxxx
     Content-Type: application/json
     ```
   - **Request Body**:
     ```json
     {"event_type": "remind"}
     ```
   - **Schedule**: `0 0 * * *`（每天 UTC 00:00 = 北京时间 08:00）

3. 保存后可以用 cron-job.org 的 "Run now" 测试

### 4. 验证

来一条测试推送验证一切正常：在 GitHub Actions 页面 → Daily Economic Reminder → Run workflow。

## 本地测试

```bash
pip install -r requirements.txt

# 复制配置文件并填入你的 key
cp config.example.json config.json

# 看今晚有什么（不推送）
python3 econ_reminder.py --dry-run

# 发测试消息到微信
python3 econ_reminder.py --test
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

## 项目结构

```
econ-reminder/
├── .github/workflows/remind.yml   # 工作流（repository_dispatch）
├── econ_reminder.py               # 主脚本
├── config.example.json            # 配置模板
├── requirements.txt               # 依赖
└── README.md
```
