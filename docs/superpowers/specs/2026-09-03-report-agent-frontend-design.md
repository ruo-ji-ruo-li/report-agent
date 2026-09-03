# report-agent 前端(Web)设计

配套设计:后端见 `2026-09-02-report-agent-design.md`(下称"后端 spec")。本文约束 `frontend/` 目录的实现,前端代码注释可引用"spec-f §n"。

## 1. 范围与背景

为体检报告问答助手提供浏览器界面:上传报告(PDF/照片)或手动录入 → 轮询解读任务(含性别/年龄补录)→ 阅读四段式解读与结构化复查计划 → 基于本人报告的多轮追问(SSE 流式)。产品定位为报告解读 + 健康建议,不构成医学诊断(免责声明文案由后端提供,原样渲染)。

**本文只约束前端**。后端 API 契约视为已冻结(以 `backend/src/report_agent/api/` 代码为准),前端不提出后端改动需求。

## 2. 设计过程中确认的决策

| 决策点 | 结论 |
|---|---|
| 框架 | Vue 3 + Vite + TypeScript |
| 组件库 | **Element Plus**(用户确认;CSS 变量主题化,视觉轻盈) |
| 手动录入 | 纳入本期(与文件上传同端点,二选一 tab) |
| 使用设备 | 仅桌面,不做移动端适配 |
| 视觉风格 | 简约;视觉方案由本文 §6 定义(用户已确认) |
| 聊天布局 | 报告详情页内 tab(解读/复查计划/追问),不用右侧栏 |
| 报告历史 | 后端无列表端点 → 前端 localStorage 记录最近报告,不要求后端新增接口 |
| SSE 客户端 | 自写 fetch 流式解析器(`token` 事件为原文非 JSON,见 §4.3) |
| Markdown | `markdown-it`(禁内联 HTML)+ `DOMPurify` 消毒;聊天流式增量渲染 |
| 不引入 | Tailwind、图表库、i18n(中文单语)、暗色模式、登录 |
| 开发联调 | Vite 代理 `/api → http://localhost:8000`(后端无 CORS,代理直接绕开) |
| 部署交付 | 本期只保证 `vite build` 产物 + 任意静态服务器/反代可部署(反代同源挂载 `/api`,无跨域问题) |

## 3. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 环境 | Node ≥ 20;包管理器 pnpm(或 npm,锁文件二选一) | — |
| 框架/构建 | Vue 3.x + Vite 6 + TypeScript 5 | SFC `<script setup lang="ts">` |
| UI | Element Plus(最新稳定版) | 全量引入即可(应用体积不敏感,桌面场景);主题 CSS 变量映射见 §8.4 |
| 状态 | Pinia | 仅一个 store:当前报告上下文(见 §8.2) |
| 路由 | vue-router 4(createWebHistory) | 见 §5 |
| HTTP | axios(REST) | baseURL `/api` |
| SSE | 原生 fetch + ReadableStream 自写解析 | 见 §4.3 / §8.3 |
| Markdown | markdown-it + DOMPurify | 渲染 LLM 文本;`html:false` + 输出消毒 |
| 测试 | Vitest + @vue/test-utils | 见 §9 |

## 4. 后端契约(前端视角)

以下形状取自后端代码(`api/*.py`、`pipeline/db_access.py`、`pipeline/interpret.py`、`pipeline/followup_plan.py`、`guardrails/rules.py`),实现时以此为准,不得凭 README 猜。

### 4.1 REST 端点

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/reports` | multipart `file`(PDF/照片)**或**纯 JSON 手动录入(见 §4.2),二者必居其一 | `{report_id, task_id}` |
| GET | `/api/tasks/{task_id}` | — | `{task_id, report_id, status, stage, timings, completed_stages: [阶段名], error}`;`status` ∈ `pending / awaiting_meta / running / completed / degraded / failed` |
| PATCH | `/api/reports/{id}/meta` | `{sex?, age?}` | `{report_id, meta_updated}`;任务处于 `awaiting_meta` 时自动恢复管线 |
| GET | `/api/reports/{id}` | — | 报告详情(§4.2),自带 `task` 摘要 `{id, status, stage, error}` |
| GET | `/api/reports/{id}/interpretation` | — | 四段式解读(§4.2);未生成 **404** |
| GET | `/api/reports/{id}/followup-plan` | — | 复查计划(§4.2);未生成 **404** |
| POST | `/api/reports/{id}/chat/sessions` | — | `{session_id}` |
| POST | `/api/chat/sessions/{sid}/messages` | `{content}` | SSE 流(§4.3) |
| GET | `/api/chat/sessions/{sid}/history` | — | `{messages: [{role, content, guardrail_flags, created_at}]}` |
| GET | `/api/health` | — | `{status, checks: {postgres, milvus, neo4j}}` |

404 语义:解读/复查计划 404 = "尚未生成"(不是错误),轮询流按此容错;会话/报告 404 = 资源不存在。

### 4.2 载荷形状(已钉死)

手动录入 JSON(`POST /api/reports` 纯 JSON body):

```json
{"meta": {"sex": "男", "age": 35, "institution": "…", "report_date": "…"},
 "items": [{"section": "肝功能", "name": "丙氨酸氨基转移酶", "value_text": "42.0",
            "value_num": 42.0, "unit": "U/L", "ref_range_text": "9~50", "abnormal_flag": null}]}
```

报告详情 `GET /api/reports/{id}`:

```json
{"meta": {"id": "…", "source": "pdf|photo|manual", "institution": null,
          "report_date": null, "sex": null, "age": null},
 "items":  [{"section", "name", "value_text", "value_num", "unit", "ref_range_text", "abnormal_flag"}],
 "normalized": [{"item_name", "indicator_code", "value_num", "unit", "status",
                 "ref_low", "ref_high", "critical", "section"}],
 "task": {"id", "status", "stage", "error"}}
```

解读 `GET …/interpretation`(护栏终稿,**原样渲染,不得改写/重组**):

```json
{"summary": "总体结论文本", 
 "items": [{"indicator_code", "name", "status", "value_text", "meaning",
            "risks": ["…"], "advice_level", "advice", "evidence_ids": ["…"]}],
 "advice_summary": "【定期复查】…\n【尽快就医】…", 
 "disclaimer": "【免责声明】…", "degraded": false}
```

- `status` ∈ `high / low / critical_high / critical_low / normal / unknown / unmapped`
- `advice_level` ∈ `lifestyle / recheck / specialist / urgent`(后端已排序 urgent 在前,前端不重排)
- `value_text` 已内嵌参考区间(如 `42.0 U/L(参考区间 9.0~50.0)`);区间带定位用 `normalized[]` 的数值(`value_num/ref_low/ref_high/critical`),按 `indicator_code`(缺则按 `item_name`)关联
- `evidence_ids` 仅为编号、接口不含证据文本 → 前端不渲染引用,仅可显示"引用知识库 N 条"
- `degraded=true` 或任务 `degraded` → 页面顶部降级提示条

复查计划 `GET …/followup-plan`:

```json
{"items": [{"item": "复查项目", "timeframe": "时间窗", "department": "科室", "basis": "依据"}], "degraded": false}
```

### 4.3 SSE 事件(解析器契约)

`POST /api/chat/sessions/{sid}/messages`,body `{content}`,响应 `text/event-stream`。**关键非标约定:只有 `token` 的 data 是原文,其余事件 data 是 JSON 字符串;`token` 若按 JSON.parse 会产生引号伪影(后端注释明示)。**

| 事件 | data 格式 | 前端动作 |
|---|---|---|
| `token` | **原文文本增量** | 直接拼接到当前 assistant 消息,增量渲染 markdown |
| `tool_call` | `{"name", "status": "start"|"end"}` | 状态行:开始→"正在查询报告/知识库…"(按 name 映射文案,未知 name 用通用文案),结束→隐藏 |
| `evidence` | 字符串(含 `[eN]` 编号的证据文本) | 收集,回答完成后渲染为底部引用条 |
| `safety` | 字符串(安全话术) | **整条替换**当前 assistant 消息内容,标记"经安全校验",丢弃已拼文本 |
| `error` | 字符串 | 停止流,显示错误态 + 重试(见 §5.3) |
| `done` | `{"session_id", "guardrail": "pass"|"suspect"|"block"}` | 结束流;`block` → 内容已是安全话术(safety 事件已处理) |

护栏枚举 `Verdict` = `pass / suspect / block`。断连/错误后的同步策略见 §5.3。

## 5. 信息架构与页面

3 个路由,无登录页(后端无鉴权,单用户本地部署)。

### 5.1 路由与页面

| 路由 | 页面 | 内容 |
|---|---|---|
| `/` | 首页 | ① 论点区(示例区间带 + 主标题"看懂你的体检报告")② 上传区 tab:[上传文件 / 手动录入]③ 最近报告列表 |
| `/report/:id` | 报告详情 | 封面带(报告信息 + 状态徽标)→ 任务未完成时进度区 → tab:[解读 / 复查计划 / 追问] |
| `*` | 404 | "没有这个页面"+ 回首页 |

**首页细节**
- 上传 tab:虚线拖拽区(点击选择 + 拖放,PDF/图片),文件类型与大小后端约束;选中即 POST
- 手动录入 tab:meta 表单(性别/年龄/机构/报告日期)+ 动态条目列表(`§4.2 ManualItem` 七字段,section/name 必填),增删行;提交 = POST JSON
- 最近报告:localStorage,见 §5.4

**报告详情页细节**
- 封面带:报告名称 + meta(性别/年龄缺失显示"—"与"待补充"入口)+ 来源/机构/日期 + 任务状态徽标;`degraded` 加降级徽标
- 进度区(任务未终态时):轮询 `GET /api/tasks/{task_id}`(2s 间隔,指数退避至 8s 上限);7 个真实阶段(parse→normalize→compare→retrieve→generate→guardrail→plan)渲染为细进度带,`completed_stages` 打勾,当前 `stage` 呼吸脉冲;`timings` 可显示"已用 N 秒"
- `awaiting_meta`:进度区显示"报告缺性别/年龄"补录表单 → 按钮 **保存并继续解读** → PATCH → 恢复轮询
- `failed`:显示 `error` 文案 + 引导("换一张更清晰的照片,或改用手动录入" + 回首页按钮)
- 解读 tab:四段纵向(§6.5);解读/followup 404 → 骨架占位(轮询任务终态后应有,防御性重试一次后显示"尚未生成")
- 复查计划 tab:`§4.2` items 渲染为简单列表(项目/时间/科室/依据),不画时间轴
- 追问 tab:进入时 `POST …/chat/sessions` 取 `session_id`(会话 id 仅存内存/会话级,不落 localStorage);`GET history` 恢复历史;输入发送走 SSE(§4.3);空态"就报告里的任何一项提问"

### 5.2 任务轮询流

```
上传/录入 → {report_id, task_id} → 跳 /report/:id
→ 轮询 GET /api/tasks/{task_id}
   pending/running → 进度带更新
   awaiting_meta → 停轮询,出补录表单 → PATCH → 恢复轮询
   completed/degraded → 停轮询 → 并行取 report + interpretation + followup-plan(404 容错)
   failed → 停轮询 → 错误态
从 localStorage 进入 → GET /api/reports/{id} → task.status 非终态则同轮询,终态直接渲染
```

### 5.3 追问 SSE 流与断连策略

- 发送:`POST messages`,fetch 读 ReadableStream,按 `event:`/`data:` 行解析(§8.3),AbortController 可中止
- `safety`:整条替换 + 标记;`done`:结束 + `guardrail` 落款(block 时内容已是安全话术)
- **断连/`error` 事件后的同步策略**:后端在流结束后才持久化完整回答(即使客户端断连,服务端仍会跑完并入库)→ 前端拉取 `GET history` 覆盖本地会话视图(丢弃已拼的半截文本);history 无本轮回答时显示"生成失败,请重试"
- 重试 = 重新发送同一条提问(原提问已入库,重复提问是用户显式动作)

### 5.4 最近报告(localStorage)

```ts
// key: "report-agent:recent";上限 20,按 id 去重,新在前
{id: string, created_at: string, source: "pdf"|"photo"|"manual",
 institution: string|null, report_date: string|null, status: TaskStatus|null}
```

每次上传成功/打开详情页时更新 status 字段;列表项可删除(仅清前端记录,不动后端)。

## 6. 视觉设计系统

### 6.1 设计 token(颜色)

| token | 值 | 用途 |
|---|---|---|
| `--c-paper` | `#F5F6F3` | 页面底色(冷调灰绿纸感) |
| `--c-ink` | `#28323A` | 正文墨青黑 |
| `--c-cover` | `#1F2A28` | 报告详情页封面带底色(全站唯一深色块) |
| `--c-clinical` | `#0E6F6D` | 主色:链接/按钮/区间内圆点(对纸白对比 6:1) |
| `--c-amber` | `#A16207` | 偏高/偏低·建议复查级 |
| `--c-brick` | `#B3412E` | 危急值·建议就诊级 |
| `--c-hairline` | `#DFE3DF` | 细线(仅化验单区域) |
| `--c-mist`(衍生) | `#E8EFEC` | 区间带正常段填充(临床青 8% 混白) |

深色封面带只出现在报告详情页;颜色信号 = 在意等级(观察/复查/就诊 → 临床青/信号橙/警示砖),其余界面保持纸白安静。Element Plus 主题映射:primary=临床青、warning=信号橙、danger=警示砖(§8.4)。

### 6.2 字体

| 角色 | 字体 | 用法 |
|---|---|---|
| 封面带标题 + 四段章节标题 | 思源宋体 Noto Serif SC 600(自托管单字重,回退 SimSun/serif) | 印刷报告质感;仅此两处 |
| 正文 | 系统无衬线栈(Segoe UI / Microsoft YaHei / PingFang SC) | 15px / 行高 1.8 |
| 化验数值 | 等宽(Consolas / ui-monospace),`font-variant-numeric: tabular-nums` | 15px 600,仪器读数感 + 列对齐 |
| 标签/元信息 | 正文栈 | 13px,墨 60% |

### 6.3 间距与动效

- 8px 栅格;内容列宽 920px 居中;段间距 48px;条带行内边距 16/12
- 动效仅两处:进度带当前阶段呼吸脉冲、聊天流式增量(内容本身就是动效);`prefers-reduced-motion` 时关闭脉冲
- 细线仅用于逐项解读条带行间(化验单隐喻);页面其余分隔靠留白

### 6.4 记忆点:参考区间带(signature)

逐项解读每一行渲染一条 160px × 4px 细横带:正常段填青雾、两端线灰刻度;你的结果 = 8px 圆点。

- 数据源:`normalized[]` 的 `value_num/ref_low/ref_high/status/critical`(解读 items 与之按 `indicator_code`→`item_name` 关联)
- 定位:`p = clamp((value − low) / (high − low), 0, 1)`,`x = padding + p × width`
- 单边区间(仅 low 或仅 high)→ 带一侧开口箭头;`ref_low/ref_high` 或 `value_num` 缺失 → 该行不画带,纯文本
- 圆点颜色:critical(或 status 为 critical_*)/危急 → 警示砖 + 「危急」徽标;high/low → 信号橙;normal → 临床青;unknown/unmapped → 墨 60% 灰点 + 「无法判定/未识别」标签
- 出区间:圆点吸附到带端 + ↑/↓ 箭头(不拉伸刻度)
- 图例仅出现一次:"— 参考区间 ● 你的结果"
- 纯 CSS/SVG,不引图表库

### 6.5 解读 tab 布局(四段)

1. **总体结论**:`summary` markdown 段落
2. **逐项解读**:两栏条带行——左栏数据列(指标名 / 等宽数值+单位 / 区间带 / 参考区间),右栏解读文字(`meaning` + `risks` 列表 + `advice`);行间线灰细线;`advice_level` 渲染枚举标签(urgent 尽快就医·警示砖 / specialist 专科就诊·信号橙 / recheck 定期复查·信号橙 / lifestyle 生活方式调整·临床青);行内不另加异常标记——区间带圆点颜色是唯一信号
3. **分级建议**:`advice_summary` **原样** markdown 渲染(后端已排序,前端不重组)
4. **免责声明**:`disclaimer` 小字,线灰上分隔,常驻底部

```
首页                                   报告详情(解读 tab)
┌──────────────────────────────┐    ┌──────────────────────────────┐
│ 看懂你的体检报告      (产品名) │    │ ██████████ 墨青封面带 ████████ │
│ 上传PDF或照片,逐项解读+复查建议 │    │ 体检报告 男·35岁·平安体检·08-28  │
│                              │    │ ████████████████████████████ │
│ 示例:总胆固醇 6.31 ↑ 参考2.8–5.2│    │ [解读] [复查计划] [追问]        │
│ ├───────────────●───────────┤│    │                              │
│                              │    │ 总体结论(衬线)                │
│ ┌──────────────────────────┐ │    │ 文字…                         │
│ │ [上传文件] [手动录入]       │ │    │ 逐项解读  (—参考区间 ●你的结果) │
│ │  ┌ 虚线区:拖入或点击选择 ─┐│ │    │ ──────────────────────────── │
│ └──────────────────────────┘ │    │ 丙氨酸氨基转移酶 42.0 U/L ↑    │
│ 最近报告                     │    │ 参考9–50    │ 轻度升高,常见于…  │
│ (空态:还没有报告,先上传吧)    │    │ ├─────────●─┤  (解读文字)      │
└──────────────────────────────┘    │ ──────────────────────────── │
                                    │ 总胆固醇 6.31 mmol/L ↑        │
                                    │ 参考2.8–5.2 │ 高于参考区间…     │
                                    │ ├───────●───┤ (点出界,橙)     │
                                    │ ──────────────────────────── │
                                    │ 分级建议 [需复查][定期观察]     │
                                    │ 免责声明(小字,常驻底部)         │
                                    └──────────────────────────────┘
```

### 6.6 其他页面视觉

- 首页:论点区(主标题 + 静态示例区间带)+ 上传区(虚线框,tabs 切换)+ 最近报告扁平列表(线灰分隔,状态点);页眉产品名小字
- 复查计划:扁平列表,列 = 项目 / 时间 / 科室 / 依据(等宽数值列对齐)
- 追问:用户消息右(青雾底)、assistant 左(纸白卡片线灰描边)、evidence 引用条在回答底部、tool_call 为轻状态行、safety 替换为安静警示块;输入框底栏 + 发送按钮
- 404/空态/失败态:文案见 §7

## 7. 文案规范

| 位置 | 文案 |
|---|---|
| 首页主标题/副题 | 看懂你的体检报告 / 上传 PDF 或照片,得到逐项解读与复查建议,还能继续追问 |
| 上传区 | 拖入 PDF 或报告照片,或点击选择文件 |
| 补录按钮 | 保存并继续解读(不是"提交") |
| 任务失败 | 这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。 |
| 聊天输入占位 | 就报告里的任何一项提问 |
| 最近报告空态 | 还没有报告,先上传第一份吧。 |
| 解读 404 重试后仍失败 | 解读尚未生成,稍后刷新页面再试。 |

原则:按钮说清行为;失败态给方向不给情绪;后端提供的文本(解读四段、安全话术、免责声明)原样渲染,前端不转述、不改写。

## 8. 工程组织

### 8.1 目录

```
frontend/
├── index.html / package.json / vite.config.ts / tsconfig.json / vitest.config.ts
├── src/
│   ├── main.ts / App.vue
│   ├── styles/            # tokens.css(§6.1 CSS 变量)、element-theme.css(§8.4)、base.css
│   ├── api/               # client.ts(axios)、types.ts(§4 全部响应/请求类型)、
│   │                      # reports.ts / tasks.ts / chat.ts / health.ts
│   ├── composables/       # useTaskPolling.ts、useChatSSE.ts、useRecentReports.ts
│   ├── stores/            # report.ts(当前报告上下文,唯一 store)
│   ├── router/            # index.ts
│   ├── views/             # HomeView.vue / ReportView.vue
│   └── components/        # UploadZone、ManualEntryForm、MetaPatchForm、TaskProgress、
│                          # SectionSummary、SectionItems(含 RangeStrip)、SectionAdvice、
│                          # SectionDisclaimer、FollowupList、ChatPanel、MarkdownBlock
└── tests/                 # sseParser、rangeStrip、severity、recentReports(§9)
```

### 8.2 状态

Pinia 单 store `report`:当前 `report_id`、报告详情、任务状态、解读/复查计划缓存;轮询与 SSE 均为 composable 内部状态(不上升全局)。

### 8.3 SSE 解析器(纯函数,独立可测)

输入 ReadableStream,输出事件序列:按行解析 `event:` / `data:`;`token` 的 data **直接拼接**(不 JSON.parse),其余事件 `JSON.parse`(失败防御性跳过并告警);网络分块可把一帧拆在多块中,解析器需按"双换行 = 帧边界"缓冲;`AbortController` 中止;`done`/`error`/流结束为终态。

### 8.4 Element Plus 主题映射与全局

```css
:root { --el-color-primary: #0E6F6D; --el-color-warning: #A16207; --el-color-danger: #B3412E; }
```

组件默认尺寸 default;el-upload 仅作样式容器,上传请求走 axios(便于统一错误处理与 JSON/表单双模式)。

### 8.5 markdown 渲染

`markdown-it` 配置 `{html: false, linkify: true, breaks: true}`;输出过 `DOMPurify.sanitize`;聊天 token 增量拼接后节流渲染(约 80ms)避免每 token 全量重渲染。

## 9. 测试策略

Vitest 单测(纯函数为主,无需真实后端):

| 目标 | 内容 |
|---|---|
| `sseParser` | 事件帧解析:token 原文/JSON 事件混排、帧跨块拆分、`done` 终态、异常 data 跳过 |
| `rangeStrip` | 定位公式:区间内/出界吸附/单边区间/缺 ref 或 value 不画带、critical 颜色 |
| `severity` | status × critical → 圆点颜色/中文标签/advice_level 标签映射 |
| `recentReports` | localStorage 读写、去重、上限截断、删除 |

组件测试:仅冒烟挂载(HomeView/ReportView 以 mock api 渲染不抛错)。**不做 e2e**;手工验收走 §10 清单。

## 10. 验收标准

- [ ] 上传 PDF → 跳详情 → 进度带展示真实阶段 → completed → 四段解读 + 复查计划渲染,数值/区间带/颜色与 `normalized` 一致
- [ ] 上传照片(图片)→ 同链路可用
- [ ] 手动录入 JSON → 同链路可用
- [ ] 性别/年龄缺失 → 任务停在 awaiting_meta → 补录表单 → "保存并继续解读" → 管线恢复
- [ ] 任务 failed → 错误态与引导文案
- [ ] degraded(任务或解读)→ 降级提示条
- [ ] 追问:建会话 → 流式打字 → tool_call 状态行 → evidence 引用条 → done;`safety` 事件整条替换;断连后 history 同步;刷新恢复历史
- [ ] 最近报告:上传后出现、状态更新、删除仅清本地
- [ ] 解读四段与安全话术**逐字**等于后端返回(渲染 markdown 后文本一致)
- [ ] `npm run build` 成功,产物可静态部署;dev 模式代理可用
- [ ] 键盘焦点可见;`prefers-reduced-motion` 下无脉冲动效

## 11. 范围外(本期不做)

移动端适配、暗色模式、i18n、登录/多用户、后端任何改动(含列表端点)、图表库、e2e 测试、Docker 打包/部署编排。