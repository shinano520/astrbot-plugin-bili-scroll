# AstrBot 刷 B 站插件

> 给大肥鱼一个 B 站账号，让她自己刷、自己挑、自己分享。

[![Status](https://img.shields.io/badge/status-planning-yellow)]()
[![AstrBot](https://img.shields.io/badge/AstrBot-4.x-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## 这是什么

**把 [`c1overzz/dsh-bili-scroll`](https://github.com/c1overzz/dsh-bili-scroll) 移植到 AstrBot。**

原项目是一个 DSH 插件：每天替你刷一轮 B 站，读字幕挑出值得的，写一份日报，值得的就 @ 你。本项目把同一套产品逻辑搬到 AstrBot 插件体系里，让她在 QQ 上完成同样的事。

| 项 | 内容 |
| --- | --- |
| 开发者 | **大肥鱼** |
| **原项目** | [`c1overzz/dsh-bili-scroll`](https://github.com/c1overzz/dsh-bili-scroll) v0.1.6（MIT） |
| 关系 | **移植**，非原创。产品逻辑来自原项目，代码与运行时全部重写 |
| 账号 | 大肥鱼自己的 B 站账号 |
| 运行环境 | AstrBot 4.x（Python 插件） |
| 新增端口 | **无** |

---

## 能做什么

| 能力 | 来源 | 状态 |
| --- | --- | --- |
| 按关键词搜近 N 天视频 | 原项目 | 规划中 |
| 读字幕原文判读价值 | 原项目 | 规划中 |
| 判重（已看过的跳过） | 原项目 | 规划中 |
| 生成日报 + 归档 | 原项目 | 规划中 |
| 峰谷时段自动跑 | 原项目 | 规划中 |
| **随机时间抖动** | 本项目新增 | 规划中 |
| **分享到 QQ**（开关 + 频率） | 本项目新增 | 规划中 |
| **按好感度点赞 / 投币** | 本项目新增 | 规划中 |
| 评论并 @ / 回复楼中楼 | 原项目 | 规划中（默认关） |
| 本地 Whisper 转写 | 原项目 | **不移植** |

**不做**：刷播放量、批量账号、自动注册、任何绕过平台规则的行为。

---

## 和原项目的差异

| 维度 | dsh-bili-scroll | 本项目 |
| --- | --- | --- |
| 插件体系 | DSH（Node.js） | AstrBot（Python） |
| 存储 | Markdown 文件 | SQLite |
| 消息出口 | DSH 会话 | QQ 消息 |
| 调度 | 固定谷时 | 工作时段 + 随机抖动 |
| 账号行为 | 替用户刷 | **大肥鱼自己刷** |
| 投币决策 | 无 | **按好感度** |

移植边界与详细设计见 [方案书](docs/PLAN.md)。

---

## 架构

```text
AstrBot（astrbot 容器）
   │
   ├── bili-scroll 插件
   │     ├── 调度器：工作时段 + 随机抖动
   │     ├── 采集层：bilibili-api-python
   │     ├── 判读层：AstrBot 已配置的 LLM provider
   │     ├── 存储层：SQLite
   │     └── 出口层：QQ 消息
   │
   └── 好感度数据（astrbot_plugin_self_learning，只读）
```

**没有新增监听端口。** 插件是纯出站组件。

---

## 文档

- [方案书](docs/PLAN.md) —— 源插件分析、移植边界、功能清单、配置项、安全设计、好感度联动
- [开发书](docs/DEVELOPMENT.md) —— 工程结构、模块职责、数据模型、核心流程、测试策略、安全实现

---

## 安全

| 项 | 做法 |
| --- | --- |
| 网络 | 零监听，只做出站请求 |
| Cookie | 加密落盘，日志脱敏 |
| 提示注入 | 不可信内容包裹 + 输出白名单 + 动作二次校验 |
| 权限 | 读操作默认开，写操作需显式开启 |
| 速率 | API ≤ 2 次/秒，LLM ≤ 20 次/轮 |

详见方案书 §6。

---

## 状态

方案评审阶段。

---

## 许可与致谢

- **MIT**
- 本项目是 [`c1overzz/dsh-bili-scroll`](https://github.com/c1overzz/dsh-bili-scroll) 的移植版本，保留原项目版权声明
- 依赖 [`bilibili-api-python`](https://github.com/Nemo2011/bilibili-api) 与 [AstrBot](https://github.com/AstrBotDevs/AstrBot)
