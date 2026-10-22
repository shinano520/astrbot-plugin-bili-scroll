# AstrBot 哔哩哔哩日常插件

> 在 QQ 里发一句指令，让机器人帮你做完 B 站日常。

[![Status](https://img.shields.io/badge/status-planning-yellow)]()
[![AstrBot](https://img.shields.io/badge/AstrBot-4.x-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## 这是什么

一个 AstrBot 插件，把 **DSH 内置 `browser-skill`** 的「驱动真实浏览器完成 B 站日常」能力，移植到 AstrBot 的插件体系里。

能力来源与开发方式：

| 项 | 说明 |
| --- | --- |
| 开发 | **由 AI 全程开发**（DeepSeek Harness / Little Code Sauce） |
| 能力范式 | **DSH `browser-skill`**：CDP 驱动 Chromium，真实点击 / 输入 / 滚动 / 读 DOM |
| 运行环境 | AstrBot + Shipyard Neo 沙盒（`browser-python` profile） |
| 模型依赖 | 无。复用 AstrBot 已配置的 provider，不新增任何 API Key |

> DSH 的 `browser-skill` 目前是 Harness 内置能力，不以独立仓库分发；若后续公开，本文档会补全仓库地址。

---

## 能做什么

| 功能 | 通道 | 状态 |
| --- | --- | --- |
| 每日签到 | B 站 API | 规划中 |
| 签到状态查询 | B 站 API | 规划中 |
| 观看视频领经验 | 浏览器（Playwright） | 规划中 |
| 分享任务 | 浏览器 | 规划中 |
| 投币 | B 站 API | 规划中 |
| 一键全套 | 混合 | 规划中 |

**不做**：刷播放量、批量账号、自动注册、任何绕过平台规则的行为。

---

## 架构

```text
QQ 群 / 私聊
   │  /bili 一键
   ▼
AstrBot（astrbot 容器）
   │
   ▼
bilibili-daily 插件
   ├── API 通道（requests）──────► B 站接口（签到 / 查询）
   └── 浏览器通道（Playwright）──► Shipyard Neo 沙盒
                                      └── Chromium
```

设计原则：**能用 API 就不开浏览器**，浏览器只处理 API 覆盖不到的真实交互。

---

## 文档

- [方案书](docs/PLAN.md) —— 背景、能力分析、架构、功能范围、风险与里程碑
- 使用说明 —— 随 M1 交付

---

## 配置（草案）

| 键 | 说明 |
| --- | --- |
| `bilibili.cookie` | B 站 Cookie（加密存储） |
| `tasks.checkin.enable` | 启用签到 |
| `tasks.watch.duration` | 每个视频最少观看秒数 |
| `schedule.time` | 每日自动执行时间 |
| `sandbox.profile` | 沙盒 profile，默认 `browser-python` |

完整 schema 见 [`_conf_schema.json`](_conf_schema.json)（M1 交付）。

---

## 状态

当前处于 **方案评审阶段**，代码尚未开始。

---

## 署名

- 本项目由 **AI（DeepSeek Harness / Little Code Sauce）** 开发。
- 能力模型参考 **DSH 内置 `browser-skill`** 的浏览器自动化实现。
- 感谢 AstrBot 社区提供的插件体系与 Shipyard Neo 沙盒。

## 许可

MIT
