# AstrBot 哔哩哔哩日常插件 · 方案书

> 版本：v0.1（草案）
> 日期：2026-10-22
> 状态：待评审

---

## 0. 项目信息

| 项 | 内容 |
| --- | --- |
| 项目名 | `astrbot-plugin-bilibili-daily` |
| 中文名 | AstrBot 哔哩哔哩日常插件 |
| 类型 | AstrBot 第三方插件 |
| 开发方式 | **由 AI 全程开发**（DeepSeek Harness / Little Code Sauce） |
| 能力来源 | **DSH 内置 `browser-skill`（浏览器自动化能力）** |
| 参考实现 | DSH `browser-skill`：CDP 驱动 Chromium，完成点击 / 输入 / 滚动 / 读 DOM / 截图 |
| 目标运行时 | AstrBot + Shipyard Neo 沙盒（`browser-python` profile） |
| 许可证 | MIT |

### 0.1 关于「原仓库」

DSH 的 `browser-skill` 是 **Harness 内置能力**，不以独立开源仓库分发（本机检查结果：安装目录与用户数据目录内均无对应仓库文件，能力由运行时动态注入）。

因此本项目在署名时采用如下表述：

> 本插件由 AI（DeepSeek Harness）开发，能力模型参考 DSH 内置 `browser-skill` 的浏览器自动化范式。
> 若后续该能力公开仓库，将在此处补全仓库地址与 commit 引用。

---

## 1. 背景与目标

### 1.1 背景

- 已在服务器 `160.202.47.128` 完成 AstrBot + NapCat 部署，QQ 侧全链路已通（`3692557270`）。
- 已在同一台机器部署 Shipyard Neo（`bay` + `bay-gull`），沙盒具备 Playwright / Chromium 能力。
- 用户在 DSH 侧已有成熟的「刷 B 站」使用习惯，希望把同样的能力搬到 QQ 机器人上，随时发指令即可完成日常任务。

### 1.2 目标

1. 在 QQ 里发指令，机器人完成 B 站日常任务（签到 / 观看 / 分享 / 投币）。
2. 任务结果以卡片形式回传，失败原因明确可读。
3. 登录态（Cookie）与执行环境（沙盒）分离，降低账号风险。
4. 复用 AstrBot 已配置的模型服务商，插件自身不引入任何新的 API Key。

### 1.3 非目标

- 不做播放量 / 数据刷量（违反平台规则，且与「日常任务」定位无关）。
- 不接管用户的 B 站账号密码，只接受 Cookie。
- 不实现 B 站内容解析（那是另一类插件，如 `astrbot_plugin_parser`）。

---

## 2. 源能力分析：DSH `browser-skill`

### 2.1 工作方式

```text
用户请求
   ↓
browser-skill（CDP）
   ↓
已登录的 Chromium 实例（--remote-debugging-port）
   ↓
页面操作：点击 / 输入 / 滚动 / 读 DOM / 截图
```

### 2.2 关键特性

| 特性 | 说明 | 对本项目的意义 |
| --- | --- | --- |
| 复用登录态 | 直接用用户已登录的浏览器 | 不需要处理登录、验证码 |
| 真实交互 | `Input.dispatchMouseEvent` 等可信事件 | 绕过大部分「非人类操作」检测 |
| 可读可写 | 既能读页面文本，也能点按钮 | 覆盖签到、观看、分享全流程 |
| 截图能力 | `Page.captureScreenshot` | 任务失败时留证据 |

### 2.3 局限（迁移时需要处理）

| 局限 | 迁移方案 |
| --- | --- |
| 依赖宿主机浏览器进程 | 改为沙盒内 headless Chromium（Playwright） |
| 无持久 cookie 管理 | Cookie 加密存储 + 手动导入 |
| 无定时能力 | AstrBot 调度器 / 插件内 APScheduler |

---

## 3. 目标形态：AstrBot 插件

### 3.1 运行时拓扑

```text
QQ 群/私聊
   │  指令：/bili 签到
   ▼
AstrBot（astrbot 容器）
   │  调用插件
   ▼
bilibili-daily 插件
   ├── API 通道（requests）──────► B 站开放接口（签到 / 用户信息）
   └── 浏览器通道（Playwright）──► Shipyard Neo 沙盒
                                      └── Chromium（browser-python profile）
```

### 3.2 两条通道的分工

| 通道 | 适用任务 | 理由 |
| --- | --- | --- |
| **API 通道** | 每日签到、查询硬币/经验、查询任务状态 | 快、稳、无需浏览器 |
| **浏览器通道** | 观看视频、分享、投币、领取观看奖励 | 必须真实页面交互，API 无法替代 |

> 设计原则：能用 API 就不开浏览器，浏览器只处理 API 覆盖不到的动作。

---

## 4. 功能范围（MVP）

| 编号 | 功能 | 优先级 | 通道 | 说明 |
| --- | --- | --- | --- | --- |
| F1 | 每日签到 | P0 | API | 调用 `api.bilibili.com/x/web-interface/coin/add` 等签到接口 |
| F2 | 签到状态查询 | P0 | API | 返回今日是否已签、经验、硬币余额 |
| F3 | 观看视频领经验 | P0 | 浏览器 | 打开视频页，播放 N 秒，记录进度 |
| F4 | 分享视频 | P1 | 浏览器 | 点击分享按钮并取消（完成动作即可） |
| F5 | 投币 | P1 | API | 需用户配置投币数量与目标视频 |
| F6 | 一键全套 | P0 | 混合 | 依次执行 F1 → F3 → F4，单次调用完成 |
| F7 | 任务结果卡片 | P0 | — | 回传每项的成功/失败/原因 |

**MVP 验收标准**：在 QQ 里发送 `/bili 一键`，10 分钟内返回 4 项任务的执行结果，且 B 站侧能看到经验/硬币变化。

---

## 5. 架构设计

### 5.1 目录结构

```text
astrbot-plugin-bilibili-daily/
├── main.py                  # 插件入口，注册指令与调度
├── metadata.yaml            # AstrBot 插件元信息
├── requirements.txt
├── _conf_schema.json        # 插件配置 schema（WebUI 渲染）
├── core/
│   ├── __init__.py
│   ├── api_client.py        # B 站 HTTP API 封装
│   ├── browser.py           # Playwright 沙盒驱动
│   ├── cookie_store.py      # Cookie 加密存取
│   ├── tasks.py             # 任务编排（签到/观看/分享/投币）
│   └── reporter.py          # 结果组装为消息卡片
├── docs/
│   ├── PLAN.md              # 本方案书
│   └── USAGE.md             # 使用说明
└── tests/
    ├── test_api_client.py
    └── test_tasks.py
```

### 5.2 模块边界

| 模块 | 职责 | 依赖 | 扩展点 |
| --- | --- | --- | --- |
| `api_client` | B 站接口调用、签名、错误码映射 | requests | 新增接口只需加方法 |
| `browser` | 沙盒会话创建、页面操作、超时控制 | Playwright / AstrBot 沙盒 API | 新增动作加 `Action` 类 |
| `cookie_store` | Cookie 加密落盘、过期检测 | 系统 keyring 或 AES | 支持多账号 |
| `tasks` | 任务定义与编排 | api_client + browser | 新增任务注册到 `TASK_REGISTRY` |
| `reporter` | 结果 → 文本/图片 | AstrBot 消息 API | 支持自定义模板 |

**约束**：`tasks` 不直接调用 Playwright，只通过 `browser` 的抽象接口；这样以后换 Driver（CUA / 本机）时不用改任务逻辑。

### 5.3 与 AstrBot 的集成点

| 集成点 | 用法 |
| --- | --- |
| 指令注册 | `@filter.command("bili")` |
| 定时任务 | AstrBot 内置调度器，每日固定时间触发 |
| 配置读取 | `_conf_schema.json` + `self.config` |
| 沙盒调用 | AstrBot Agent 沙盒 API（Shipyard Neo） |
| 消息发送 | `event.send()` / 主动推送 |

---

## 6. 模型与 API 配置

### 6.1 明确边界

**插件本身不调用任何 LLM。** 任务执行是确定性流程（API + 浏览器脚本），不需要模型参与。

### 6.2 为什么还要提「模型配置」

因为插件需要两处与模型相关的配置：

| 场景 | 用哪个模型 | 说明 |
| --- | --- | --- |
| 自然语言触发 | **复用 AstrBot 已配置的 provider** | 用户说「帮我把今天的 B 站任务做了」，由 AstrBot 主 Agent 理解意图后调用插件指令 |
| 失败原因解释（可选） | **复用 AstrBot 已配置的 provider** | 把技术错误码翻译成人话，走现有 provider，不新增 Key |

### 6.3 可选服务商（AstrBot 侧已有）

插件不绑定具体厂商，用户可在 AstrBot 配置里任选其一：

| 类型 | 示例 | 备注 |
| --- | --- | --- |
| OpenAI 兼容 | DeepSeek / 硅基流动 / 302.AI / 智谱 | 填 Base URL + Key 即可 |
| 官方 SDK | OpenAI / Anthropic / Gemini | AstrBot 原生支持 |
| 本地 | Ollama / LMStudio | 内网部署友好 |
| 自建中转 | NewAPI / one-api | 已有现成网关时直接用 |

> **对当前部署的建议**：沿用已配置的 `openai/cn:deepseek-v4.1-flash`，不新增任何 Key。

---

## 7. 配置项设计（`_conf_schema.json`）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `bilibili.cookie` | string | `""` | B 站 Cookie（必填，加密存储） |
| `bilibili.uid` | string | `""` | 账号 UID，用于校验 |
| `tasks.checkin.enable` | bool | `true` | 是否启用签到 |
| `tasks.watch.enable` | bool | `true` | 是否启用观看任务 |
| `tasks.watch.duration` | int | `30` | 每个视频最少观看秒数 |
| `tasks.watch.video_source` | string | `"dynamic"` | 视频来源：`dynamic` 动态流 / `fixed` 指定 BV 号 |
| `tasks.share.enable` | bool | `false` | 是否启用分享 |
| `tasks.coin.enable` | bool | `false` | 是否启用投币 |
| `tasks.coin.count` | int | `1` | 每个视频投币数 |
| `schedule.enable` | bool | `false` | 是否开启每日自动执行 |
| `schedule.time` | string | `"09:00"` | 执行时间（HH:mm） |
| `sandbox.profile` | string | `"browser-python"` | 沙盒 profile |
| `sandbox.timeout` | int | `600` | 单次任务超时（秒） |
| `notify.target` | string | `""` | 结果推送到哪个会话（默认回当前会话） |

---

## 8. 风险与边界

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| B 站风控 | 任务失败、账号受限 | 控制频率；复用真实浏览器指纹；单账号单日只跑一次 |
| Cookie 泄露 | 账号被盗 | 加密存储；日志脱敏；不落盘明文 |
| 沙盒资源占用 | 影响同机其他服务 | 任务结束后销毁沙盒；单次 CPU 1 核 / 内存 2G |
| 平台规则变化 | 接口失效 | 错误码集中映射；接口层可替换 |

**明确不做**：批量账号、自动注册、刷播放量、破解会员。

---

## 9. 里程碑

| 阶段 | 交付物 | 预计工作量 |
| --- | --- | --- |
| M1 | 插件骨架 + Cookie 存取 + 签到（API） | 0.5 天 |
| M2 | 浏览器通道 + 观看任务 | 1 天 |
| M3 | 分享 / 投币 + 一键全套 + 结果卡片 | 0.5 天 |
| M4 | 定时任务 + 配置 schema + 文档 | 0.5 天 |
| M5 | 打包发布到插件市场 | 0.5 天 |

---

## 10. 署名与致谢

- **开发**：由 AI（DeepSeek Harness / Little Code Sauce）全程开发。
- **能力来源**：DSH 内置 `browser-skill`，提供「CDP 驱动浏览器完成真实交互」的范式。
- **参考项目**：
  - AstrBot 官方文档 · Agent 沙盒环境（Shipyard Neo）
  - AstrBot 插件开发指南
- 若后续 DSH `browser-skill` 公开仓库，本项目将补充仓库地址与 commit 引用。

---

## 11. 待确认事项

1. B 站 Cookie 由用户在 WebUI 手动粘贴，还是提供「扫码登录」辅助流程？
2. 观看任务的视频来源：跟动态流，还是固定几个 BV 号轮换？
3. 是否需要多账号支持（当前设计为单账号）？
4. 结果回传是否需要生成图片卡片（需要文转图服务），还是纯文本即可？
