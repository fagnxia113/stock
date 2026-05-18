<div align="center">

# 股票智能分析系统

[![CI](https://github.com/fagnxia113/stock/actions/workflows/ci.yml/badge.svg)](https://github.com/fagnxia113/stock/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](docker/docker-compose.yml)

基于 AI 大模型的 A股 / 港股 / 美股自选股分析系统，支持自动生成决策报告、市场复盘、Web 工作台、机器人问股和多渠道推送。

[快速开始](#快速开始) · [核心能力](#核心能力) · [Web 工作台](#web-工作台) · [文档导航](docs/INDEX.md) · [常见问题](docs/FAQ.md) · [更新日志](docs/CHANGELOG.md)

简体中文 | [English](docs/README_EN.md) | [繁體中文](docs/README_CHT.md)

</div>

> 本仓库当前远程为 `fagnxia113/stock.git`。项目文档中仍保留部分上游 `ZhuLinsen/daily_stock_analysis` 相关链接，用于说明原项目来源、发布镜像或历史资料；如用于公开维护，建议后续继续统一仓库链接与署名口径。

## 项目定位

这个项目面向日常股票跟踪和复盘：你给它一组自选股，它会抓取行情、新闻、资金流、技术指标和基本面信息，再调用大模型生成可读的分析报告，并通过 Web、API、机器人或通知渠道交付结果。

典型流程：

```text
自选股列表 -> 多数据源抓取 -> 技术面/新闻/资金/基本面分析 -> LLM 生成报告 -> Web/API/Bot/通知推送
```

## 核心能力

| 模块 | 能力 |
| --- | --- |
| 股票分析 | 支持 A股、港股、美股、美股指数与常见 ETF |
| 决策报告 | 输出核心结论、评分、趋势判断、买卖点、风险警报和操作清单 |
| 市场复盘 | 支持 A股、港股、美股市场概览、指数表现和板块强弱 |
| Web 工作台 | 支持手动分析、配置管理、任务进度、历史报告、回测和持仓管理 |
| Agent 问股 | 支持策略对话、多轮追问、内置策略和后台执行 |
| 数据来源 | AkShare、Tushare、Pytdx、Baostock、YFinance、Longbridge、TickFlow 等 |
| 模型接入 | Anspire、AIHubMix、Gemini、OpenAI 兼容、DeepSeek、Claude、Ollama 等 |
| 通知渠道 | 企业微信、飞书、Telegram、Discord、Slack、邮件、自定义 Webhook 等 |
| 自动化 | GitHub Actions、Docker、本地定时任务、FastAPI 服务模式 |

## 快速开始

### 本地运行

```bash
git clone https://github.com/fagnxia113/stock.git
cd stock
pip install -r requirements.txt
cp .env.example .env
python main.py --stocks 600519,hk00700,AAPL
```

运行前至少需要在 `.env` 中配置一个可用的大模型 Key。常见配置方式见 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)。

### 启动 Web 工作台

```bash
python main.py --webui-only
```

启动后访问 `http://127.0.0.1:8000`。如果需要在服务器或 Docker 中部署，请看 [完整配置与部署指南](docs/full-guide.md) 和 [云服务器 WebUI 部署说明](docs/deploy-webui-cloud.md)。

### GitHub Actions 定时分析

适合不想长期运行服务器的场景。最小配置：

| 配置项 | 说明 |
| --- | --- |
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL` |
| 一个 AI Key | 例如 `ANSPIRE_API_KEYS`、`AIHUBMIX_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY` |
| 一个通知渠道 | 例如 `WECHAT_WEBHOOK_URL`、`FEISHU_WEBHOOK_URL`、`EMAIL_SENDER` + `EMAIL_PASSWORD` |

详细步骤见 [GitHub Actions 详细配置](docs/full-guide.md#github-actions-详细配置)。

## 常用命令

```bash
python main.py --debug
python main.py --dry-run
python main.py --stocks 600519,hk00700,AAPL
python main.py --market-review
python main.py --schedule
python main.py --serve-only
```

更多命令、环境变量、通知渠道、数据源优先级和交易日规则见 [完整配置与部署指南](docs/full-guide.md)。

## Web 工作台

![Web 工作台](sources/fastapi_server.png)

Web 工作台提供配置管理、任务监控、手动分析、历史报告、回测、持仓管理、智能导入和浅色 / 深色主题。认证、搜索补全、历史报告复制、云服务器访问等细节见 [本地 WebUI 管理界面](docs/full-guide.md#本地-webui-管理界面)。

## Agent 策略问股

配置任意可用 AI API Key 后，Web `/chat` 页面即可使用策略问股；如需显式关闭可设置 `AGENT_MODE=false`。

- 支持均线金叉、缠论、波浪理论、多头趋势等内置策略
- 支持实时行情、K 线、技术指标、新闻和风险信息调用
- 支持多轮追问、会话导出、发送到通知渠道和后台执行
- 支持自定义策略文件与多 Agent 编排

详细说明见 [完整指南](docs/full-guide.md#本地-webui-管理界面) 与 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)。

## 文档导航

| 场景 | 文档 |
| --- | --- |
| 不知道先看什么 | [中文文档索引](docs/INDEX.md) |
| 完整配置和部署 | [完整配置与部署指南](docs/full-guide.md) |
| 大模型配置 | [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md) |
| 服务器部署 | [部署指南](docs/DEPLOY.md) |
| WebUI 云服务器访问 | [云服务器 WebUI 部署说明](docs/deploy-webui-cloud.md) |
| 通知能力 | [通知能力基线](docs/notifications.md) |
| Bot 接入 | [Bot 命令与集成](docs/bot-command.md) |
| 常见问题 | [FAQ](docs/FAQ.md) |
| 参与开发 | [贡献指南](docs/CONTRIBUTING.md) |

## 相关项目

DSA 聚焦日常分析报告；下面两个同系列项目分别覆盖选股、策略验证与策略进化，适合按需延伸使用。

- [AlphaSift](https://github.com/ZhuLinsen/alphasift)：多因子选股与全市场扫描，用于从股票池中提取候选标的。
- [AlphaEvo](https://github.com/ZhuLinsen/alphaevo)：策略回测与自我进化，用于验证策略规则，并探索策略参数与组合。

## License

[MIT License](LICENSE) © 2026 ZhuLinsen

如果你在项目中使用或基于本项目进行二次开发，欢迎在 README 或文档中注明来源并附上原仓库或本仓库链接。

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。
