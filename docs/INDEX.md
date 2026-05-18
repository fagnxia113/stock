# 中文文档索引

这里汇总本项目的主要中文文档。第一次使用建议先看 README，再根据部署方式和使用场景进入对应专题。

## 快速入门

| 文档 | 适合场景 |
| --- | --- |
| [项目首页](../README.md) | 快速了解项目定位、核心能力和最小启动方式 |
| [完整配置与部署指南](full-guide.md) | 查完整环境变量、部署方式、数据源、通知渠道和 WebUI 说明 |
| [FAQ](FAQ.md) | 排查常见配置、数据源、通知、Docker 和模型问题 |

## 部署与运行

| 文档 | 适合场景 |
| --- | --- |
| [部署指南](DEPLOY.md) | 使用 Docker、systemd、Supervisor 等方式部署服务 |
| [云服务器 WebUI 部署说明](deploy-webui-cloud.md) | 在云服务器开放 Web 工作台访问 |
| [Zeabur 部署](docker/zeabur-deployment.md) | 使用 Zeabur 快速部署 |
| [桌面端打包说明](desktop-package.md) | 构建 Electron 桌面客户端 |

## 模型、搜索与数据源

| 文档 | 适合场景 |
| --- | --- |
| [LLM 配置指南](LLM_CONFIG_GUIDE.md) | 配置大模型、渠道模式、本地 Ollama、视觉模型和故障排查 |
| [LLM 服务商配置指南](llm-providers.md) | 查看常用服务商预设、Base URL、模型命名和兼容边界 |
| [Tushare 股票列表工具](TUSHARE_STOCK_LIST_GUIDE.md) | 使用 Tushare 生成股票列表和前端补全索引 |

## 通知与机器人

| 文档 | 适合场景 |
| --- | --- |
| [通知能力基线](notifications.md) | 查看通知渠道、最小/高级配置、Actions 映射和诊断方式 |
| [Bot 命令与集成](bot-command.md) | 了解 Bot 架构、命令、Webhook 路由和扩展方式 |
| [飞书通知配置](bot/feishu-bot-config.md) | 配置飞书群机器人、签名校验、关键词和 Stream Bot |
| [钉钉企业机器人配置](bot/dingding-bot-config.md) | 配置钉钉机器人 |
| [Discord 机器人配置](bot/discord-bot-config.md) | 配置 Discord Bot 或 Webhook |

## API、集成与开发

| 文档 | 适合场景 |
| --- | --- |
| [OpenClaw Skill 集成指南](openclaw-skill-integration.md) | 通过外部 Skill 调用 DSA REST API |
| [图片识别提示词](image-extract-prompt.md) | 调整图片股票识别相关提示词 |
| [贡献指南](CONTRIBUTING.md) | 提交 Issue、功能请求和 Pull Request |
| [更新日志](CHANGELOG.md) | 查看版本变化和未发布改动 |

## 其他语言

| 文档 | 说明 |
| --- | --- |
| [English Documentation Index](INDEX_EN.md) | 英文文档索引 |
| [README English](README_EN.md) | 英文项目首页 |
| [README 繁體中文](README_CHT.md) | 繁体中文项目首页 |
