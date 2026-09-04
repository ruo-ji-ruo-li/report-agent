# report-agent 前端(Web)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `frontend/` 目录实现体检报告问答助手的 Vue 3 浏览器界面(上传/录入 → 轮询解读 → 四段式解读 + 复查计划 → SSE 流式追问)。

**Architecture:** 纯函数核心(SSE 帧解析、区间带定位、严重度映射、聊天状态 reducer、localStorage 仓库)先行、TDD 覆盖;薄 API 层(axios + 完整类型)居中;Element Plus 组件逐件实现;最后组装两个视图(首页/报告详情)。全部业务逻辑写成可注入、可单测的纯模块,组件只做渲染与装配。

**Tech Stack:** Vue 3.5 + Vite 6 + TypeScript 5.7 + Element Plus 2.9 + Pinia 3 + vue-router 4 + axios + markdown-it 14 + DOMPurify + Vitest 3(@vue/test-utils + happy-dom)。Node ≥ 20,包管理器 pnpm。

**Spec:** `docs/superpowers/specs/2026-09-03-report-agent-frontend-design.md`(下称 spec-f,执行者必须同时阅读)

## Global Constraints

- 所有代码注释、用户界面文案为中文;技术栈与依赖只允许 spec-f §3 所列(禁 Tailwind/i18n/图表库/暗色模式/登录)
- 后端契约以 spec-f §4 为准,前端不改后端;后端文本(解读四段、安全话术、免责声明)**原样渲染,不改写不重组**
- 仅桌面;Node ≥ 20;pnpm 管理锁文件;TS 严格模式
- 设计 token 只从 `styles/tokens.css` 取用(§6.1 色值),组件内不得硬编码色值
- 按钮文案遵循 spec-f §7(如补录按钮为"保存并继续解读");失败态给方向不给情绪
- 每个 Task 以独立可验证的交付物结束:TDD(纯逻辑)或冒烟挂载测试 + typecheck(组件);提交信息 `feat: ...`/`fix: ...` 前缀
- **对 spec-f §8.2 的有意偏离**:不实现 Pinia 全局 store——报告上下文只有 ReportView 一个消费者(路由视图自持状态),轮询/SSE 状态按 spec 原文留在 composable 内部,建 store 即死代码(YAGNI)

---

### Task 1: 项目脚手架与设计 token

**Files:**
- Create: `frontend/package.json`、`frontend/vite.config.ts`、`frontend/tsconfig.json`、`frontend/vitest.config.ts`、`frontend/index.html`、`frontend/src/main.ts`、`frontend/src/App.vue`、`frontend/src/router/index.ts`、`frontend/src/styles/tokens.css`、`frontend/src/styles/element-theme.css`、`frontend/src/styles/base.css`、`frontend/tests/sanity.test.ts`
- Test: `frontend/tests/sanity.test.ts`

**Interfaces:**
- Consumes: 无
- Produces: CSS 变量(token)命名供后续所有组件使用:`--c-paper / --c-ink / --c-cover / --c-clinical / --c-amber / --c-brick / --c-hairline / --c-mist`;类 `.hairline-top`(线灰上边框);`App.vue` 页眉壳;路由实例(空路由表,Task 15 填充)

- [ ] **Step 1: 写 package.json**

```json
{
  "name": "report-agent-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "vue-tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@fontsource/noto-serif-sc": "^5.1.1",
    "axios": "^1.7.9",
    "dompurify": "^3.2.3",
    "element-plus": "^2.9.3",
    "markdown-it": "^14.1.0",
    "pinia": "^3.0.1",
    "vue": "^3.5.13",
    "vue-router": "^4.5.0"
  },
  "devDependencies": {
    "@types/markdown-it": "^14.1.2",
    "@vitejs/plugin-vue": "^5.2.1",
    "@vue/test-utils": "^2.4.6",
    "happy-dom": "^15.11.7",
    "typescript": "~5.7.2",
    "vite": "^6.0.7",
    "vitest": "^3.0.5",
    "vue-tsc": "^2.2.0"
  }
}
```

- [ ] **Step 2: 安装依赖并验证**

Run: `cd frontend && pnpm install`
Expected: 成功,生成 `pnpm-lock.yaml`

- [ ] **Step 3: 写 vite.config.ts 与 vitest.config.ts**

```ts
// vite.config.ts
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.')
  return {
    plugins: [vue()],
    server: {
      proxy: {
        // 后端无 CORS,开发期代理绕开跨域(spec-f §2)
        '/api': { target: env.VITE_API_TARGET || 'http://localhost:8000', changeOrigin: true },
      },
    },
  }
})
```

```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: { environment: 'node', restoreMocks: true },
})
```

- [ ] **Step 4: 写 tsconfig.json 与 index.html**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["vite/client"]
  },
  "include": ["src/**/*.ts", "src/**/*.vue", "tests/**/*.ts", "vite.config.ts", "vitest.config.ts"]
}
```

```html
<!-- index.html -->
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>体检报告解读助手</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 5: 写设计 token(spec-f §6.1/§6.2/§6.3)**

```css
/* src/styles/tokens.css —— 全站唯一色值来源,组件内不得硬编码 */
:root {
  --c-paper: #F5F6F3;
  --c-ink: #28323A;
  --c-cover: #1F2A28;
  --c-clinical: #0E6F6D;
  --c-amber: #A16207;
  --c-brick: #B3412E;
  --c-hairline: #DFE3DF;
  --c-mist: #E8EFEC;

  --font-display: 'Noto Serif SC', SimSun, serif;          /* 封面带 + 章节标题 */
  --font-body: 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif;
  --font-mono: Consolas, ui-monospace, 'Courier New', monospace; /* 化验数值 */

  --space-8: 8px; --space-16: 16px; --space-48: 48px;
  --col-width: 920px;
}

/* src/styles/element-theme.css —— Element Plus 主题映射(spec-f §8.4) */
:root {
  --el-color-primary: #0E6F6D;
  --el-color-warning: #A16207;
  --el-color-danger: #B3412E;
  --el-border-radius-base: 4px;
  --el-font-family: var(--font-body);
}

/* src/styles/base.css —— 基础版式 */
html, body { margin: 0; padding: 0; }
body {
  background: var(--c-paper);
  color: var(--c-ink);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.8;
}
#app { min-height: 100vh; }
*:focus-visible { outline: 2px solid var(--c-clinical); outline-offset: 2px; }
.hairline-top { border-top: 1px solid var(--c-hairline); }
.page-col { max-width: var(--col-width); margin: 0 auto; padding: 0 var(--space-16); }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-weight: 600; }

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none !important; }
}
```

- [ ] **Step 6: 写 main.ts / App.vue / 路由骨架**

```ts
// src/main.ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import '@fontsource/noto-serif-sc/600.css'
import './styles/tokens.css'
import './styles/element-theme.css'
import './styles/base.css'
import App from './App.vue'
import { router } from './router'

createApp(App).use(createPinia()).use(ElementPlus, { locale: zhCn }).use(router).mount('#app')
```

```vue
<!-- src/App.vue —— 页眉壳(产品名小字)+ 路由出口 -->
<script setup lang="ts">
</script>

<template>
  <header class="site-header hairline-top">
    <div class="page-col site-header-inner">
      <RouterLink to="/" class="brand">体检报告解读助手</RouterLink>
    </div>
  </header>
  <RouterView />
</template>

<style scoped>
.site-header { padding: var(--space-16) 0; border-top: none; border-bottom: 1px solid var(--c-hairline); }
.site-header-inner { display: flex; align-items: center; }
.brand { font-size: 13px; color: var(--c-ink); text-decoration: none; opacity: 0.6; }
.brand:hover { opacity: 1; }
</style>
```

```ts
// src/router/index.ts —— 路由表 Task 15 填充
import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [],
})
```

- [ ] **Step 7: 写 sanity 测试**

```ts
// tests/sanity.test.ts
import { describe, expect, it } from 'vitest'

describe('sanity', () => {
  it('测试框架可用', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 8: 验证**

Run: `cd frontend && pnpm test && pnpm run build`
Expected: 1 test PASS;build 成功(`dist/` 产出);`pnpm run dev` 可启动并显示页眉(手动确认)

- [ ] **Step 9: Commit**

```bash
git add frontend/
git commit -m "feat: frontend scaffold with design tokens (Task 1)"
```

---

### Task 2: API 类型与客户端

**Files:**
- Create: `frontend/src/api/types.ts`、`frontend/src/api/client.ts`、`frontend/src/api/reports.ts`、`frontend/src/api/tasks.ts`、`frontend/src/api/chat.ts`、`frontend/src/api/health.ts`
- Test: `frontend/tests/api.test.ts`

**Interfaces:**
- Consumes: Task 1 的 axios 依赖
- Produces(后续任务直接引用的签名,按 spec-f §4):

```ts
// types.ts 导出(节选,全部在下方实现)
type TaskStatus = 'pending' | 'awaiting_meta' | 'running' | 'completed' | 'degraded' | 'failed'
type ItemStatus = 'high' | 'low' | 'critical_high' | 'critical_low' | 'normal' | 'unknown' | 'unmapped'
type AdviceLevel = 'lifestyle' | 'recheck' | 'specialist' | 'urgent'
type Verdict = 'pass' | 'suspect' | 'block'
interface ManualItem / ManualEntry / ReportMeta / RawItem / NormalizedItem / TaskInfo / ReportDetail
interface InterpretationItem / InterpretationDoc / FollowupItem / FollowupPlanDoc / ChatMessageRow
type SSEChatEvent(§Task 3 使用)
// 模块函数(全部可注入 client,默认 apiClient):
createReportFromFile(file: File): Promise<{report_id: string; task_id: string}>
createReportManual(entry: ManualEntry): Promise<{report_id: string; task_id: string}>
getReport(id: string): Promise<ReportDetail>
patchReportMeta(id: string, patch: {sex?: string; age?: number}): Promise<void>
getTask(taskId: string): Promise<TaskInfo>
getInterpretation(id: string): Promise<InterpretationDoc>
getFollowupPlan(id: string): Promise<FollowupPlanDoc>
createChatSession(reportId: string): Promise<{session_id: string}>
getChatHistory(sessionId: string): Promise<ChatMessageRow[]>
getHealth(): Promise<{status: string; checks: Record<string, boolean>}>
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/api.test.ts
import { describe, expect, it, vi } from 'vitest'
import type { AxiosInstance } from 'axios'
import { createReportFromFile, createReportManual, getReport, patchReportMeta } from '../src/api/reports'
import { getTask } from '../src/api/tasks'
import { createChatSession, getChatHistory } from '../src/api/chat'

function mockClient(data: unknown) {
  const post = vi.fn().mockResolvedValue({ data })
  const get = vi.fn().mockResolvedValue({ data })
  const patch = vi.fn().mockResolvedValue({ data })
  return { post, get, patch } as unknown as AxiosInstance
}

describe('api 模块', () => {
  it('createReportFromFile 以 multipart 上传并返回 report_id/task_id', async () => {
    const c = mockClient({ report_id: 'r1', task_id: 't1' })
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' })
    const res = await createReportFromFile(file, c)
    expect(c.post).toHaveBeenCalledWith('/reports', expect.any(FormData))
    const fd = (c.post as ReturnType<typeof vi.fn>).mock.calls[0][1] as FormData
    expect(fd.get('file')).toBe(file)
    expect(res).toEqual({ report_id: 'r1', task_id: 't1' })
  })

  it('createReportManual 以 JSON 提交录入', async () => {
    const c = mockClient({ report_id: 'r2', task_id: 't2' })
    const entry = { meta: { sex: '男', age: 35 }, items: [{ name: 'ALT' }] }
    const res = await createReportManual(entry, c)
    expect(c.post).toHaveBeenCalledWith('/reports', entry)
    expect(res.task_id).toBe('t2')
  })

  it('getReport / getTask / patchReportMeta / 会话与历史 URL 正确', async () => {
    const c = mockClient({})
    await getReport('r1', c); expect(c.get).toHaveBeenCalledWith('/reports/r1')
    await getTask('t1', c); expect(c.get).toHaveBeenCalledWith('/tasks/t1')
    await patchReportMeta('r1', { sex: '女' }, c)
    expect(c.patch).toHaveBeenCalledWith('/reports/r1/meta', { sex: '女' })
    await createChatSession('r1', c); expect(c.post).toHaveBeenCalledWith('/reports/r1/chat/sessions')
    await getChatHistory('s1', c); expect(c.get).toHaveBeenCalledWith('/chat/sessions/s1/history')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test`
Expected: FAIL(找不到 `../src/api/reports`)

- [ ] **Step 3: 写 types.ts(spec-f §4.2 全部形状)**

```ts
// src/api/types.ts
export type TaskStatus = 'pending' | 'awaiting_meta' | 'running' | 'completed' | 'degraded' | 'failed'
export type ItemStatus = 'high' | 'low' | 'critical_high' | 'critical_low' | 'normal' | 'unknown' | 'unmapped'
export type AdviceLevel = 'lifestyle' | 'recheck' | 'specialist' | 'urgent'
export type Verdict = 'pass' | 'suspect' | 'block'

export interface ManualItem {
  section?: string | null
  name: string
  value_text?: string | null
  value_num?: number | null
  unit?: string | null
  ref_range_text?: string | null
  abnormal_flag?: string | null
}
export interface ManualEntry {
  meta?: Record<string, unknown>
  items: ManualItem[]
}

export interface ReportMeta {
  id: string
  source: 'pdf' | 'photo' | 'manual'
  institution: string | null
  report_date: string | null
  sex: string | null
  age: number | null
}
export interface RawItem {
  section: string | null
  name: string
  value_text: string | null
  value_num: number | null
  unit: string | null
  ref_range_text: string | null
  abnormal_flag: string | null
}
export interface NormalizedItem {
  item_name: string
  indicator_code: string | null
  value_num: number | null
  unit: string | null
  status: ItemStatus
  ref_low: number | null
  ref_high: number | null
  critical: boolean
  section: string | null
}
export interface TaskSummary { id: string; status: TaskStatus; stage: string | null; error: string | null }
export interface ReportDetail {
  meta: ReportMeta
  items: RawItem[]
  normalized: NormalizedItem[]
  task: TaskSummary | null
}
export interface TaskInfo {
  task_id: string
  report_id: string
  status: TaskStatus
  stage: string | null
  timings: Record<string, number> | null
  completed_stages: string[]
  error: string | null
}

export interface InterpretationItem {
  indicator_code: string | null
  name: string
  status: ItemStatus
  value_text: string
  meaning: string
  risks: string[]
  advice_level: AdviceLevel
  advice: string
  evidence_ids: string[]
}
export interface InterpretationDoc {
  summary: string
  items: InterpretationItem[]
  advice_summary: string
  disclaimer: string
  degraded: boolean
}
export interface FollowupItem { item: string; timeframe: string; department: string; basis: string }
export interface FollowupPlanDoc { items: FollowupItem[]; degraded: boolean }

export interface ChatMessageRow {
  role: 'user' | 'assistant'
  content: string
  guardrail_flags: string[] | null
  created_at: string | null
}

/** SSE 聊天事件(spec-f §4.3;token 为原文,其余为已解析 JSON) */
export type SSEChatEvent =
  | { event: 'token'; data: string }
  | { event: 'tool_call'; data: { name: string; status: 'start' | 'end' } }
  | { event: 'evidence'; data: string }
  | { event: 'safety'; data: string }
  | { event: 'error'; data: string }
  | { event: 'done'; data: { session_id: string; guardrail: Verdict } }
```

- [ ] **Step 4: 写 client.ts 与四个 API 模块**

```ts
// src/api/client.ts
import axios from 'axios'

// baseURL 走 Vite 代理(spec-f §2)
export const apiClient = axios.create({ baseURL: '/api', timeout: 60_000 })
```

```ts
// src/api/reports.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { FollowupPlanDoc, InterpretationDoc, ManualEntry, ReportDetail } from './types'

export async function createReportFromFile(file: File, client: AxiosInstance = apiClient) {
  const fd = new FormData()
  fd.append('file', file)
  const { data } = await client.post<{ report_id: string; task_id: string }>('/reports', fd)
  return data
}

export async function createReportManual(entry: ManualEntry, client: AxiosInstance = apiClient) {
  const { data } = await client.post<{ report_id: string; task_id: string }>('/reports', entry)
  return data
}

export async function getReport(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<ReportDetail>(`/reports/${id}`)
  return data
}

export async function patchReportMeta(id: string, patch: { sex?: string; age?: number }, client: AxiosInstance = apiClient) {
  await client.patch(`/reports/${id}/meta`, patch)
}

export async function getInterpretation(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<InterpretationDoc>(`/reports/${id}/interpretation`)
  return data
}

export async function getFollowupPlan(id: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<FollowupPlanDoc>(`/reports/${id}/followup-plan`)
  return data
}
```

```ts
// src/api/tasks.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { TaskInfo } from './types'

export async function getTask(taskId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<TaskInfo>(`/tasks/${taskId}`)
  return data
}
```

```ts
// src/api/chat.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { ChatMessageRow } from './types'

export async function createChatSession(reportId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.post<{ session_id: string }>(`/reports/${reportId}/chat/sessions`)
  return data
}

export async function getChatHistory(sessionId: string, client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ messages: ChatMessageRow[] }>(`/chat/sessions/${sessionId}/history`)
  return data.messages
}
```

```ts
// src/api/health.ts
import type { AxiosInstance } from 'axios'
import { apiClient } from './client'

export async function getHealth(client: AxiosInstance = apiClient) {
  const { data } = await client.get<{ status: string; checks: Record<string, boolean> }>('/health')
  return data
}
```

- [ ] **Step 5: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test && pnpm run typecheck`
Expected: 4 test PASS;typecheck 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api frontend/tests/api.test.ts
git commit -m "feat: typed API client layer (Task 2)"
```

---

### Task 3: SSE 帧解析器

**Files:**
- Create: `frontend/src/api/sseParser.ts`
- Test: `frontend/tests/sseParser.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `SSEChatEvent`
- Produces:

```ts
interface RawSSEEvent { event: string; data: string }   // data 已按行合并
class SSEFrameParser { push(chunk: string): RawSSEEvent[] }  // 缓冲到帧边界;忽略 ":" 注释行;同帧多 data 行以 \n 合并
function decodeChatEvent(raw: RawSSEEvent): SSEChatEvent | null  // token 原文透传;其余 JSON.parse,失败/未知事件返回 null
async function streamSSE(url: string, body: unknown, onEvent: (e: SSEChatEvent) => void, signal?: AbortSignal): Promise<void>
// 非 2xx 抛 Error(`HTTP ${status}`);解析失败的事件跳过;调用方以 done/error 事件为终态
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/sseParser.test.ts
import { describe, expect, it } from 'vitest'
import { SSEFrameParser, decodeChatEvent } from '../src/api/sseParser'

describe('SSEFrameParser', () => {
  it('一帧拆在两个 chunk 中也能解析', () => {
    const p = new SSEFrameParser()
    const a = p.push('event: token\nda')
    const b = p.push('ta: 你好\n\n')
    expect(a).toEqual([])
    expect(b).toEqual([{ event: 'token', data: '你好' }])
  })

  it('同帧多 data 行以换行合并(证据文本含换行)', () => {
    const p = new SSEFrameParser()
    expect(p.push('event: evidence\ndata: 第一行\ndata: 第二行\n\n'))
      .toEqual([{ event: 'evidence', data: '第一行\n第二行' }])
  })

  it('忽略 ":" 注释行(ping)', () => {
    const p = new SSEFrameParser()
    expect(p.push(': ping\n\nevent: token\ndata: x\n\n'))
      .toEqual([{ event: 'token', data: 'x' }])
  })

  it('一帧可含多个事件', () => {
    const p = new SSEFrameParser()
    expect(p.push('event: tool_call\ndata: {"name":"a","status":"start"}\n\nevent: token\ndata: 好\n\n').length).toBe(2)
  })
})

describe('decodeChatEvent', () => {
  it('token 原文透传,不 JSON.parse', () => {
    expect(decodeChatEvent({ event: 'token', data: '我来' }))
      .toEqual({ event: 'token', data: '我来' })
  })

  it('结构化事件 JSON.parse', () => {
    expect(decodeChatEvent({ event: 'tool_call', data: '{"name":"search_knowledge","status":"start"}' }))
      .toEqual({ event: 'tool_call', data: { name: 'search_knowledge', status: 'start' } })
  })

  it('done 事件解析 guardrail', () => {
    expect(decodeChatEvent({ event: 'done', data: '{"session_id":"s1","guardrail":"pass"}' }))
      .toEqual({ event: 'done', data: { session_id: 's1', guardrail: 'pass' } })
  })

  it('坏 JSON 与未知事件返回 null', () => {
    expect(decodeChatEvent({ event: 'tool_call', data: 'not json' })).toBeNull()
    expect(decodeChatEvent({ event: 'mystery', data: '{}' })).toBeNull()
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/sseParser.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/api/sseParser.ts —— spec-f §4.3/§8.3
import type { SSEChatEvent } from './types'

export interface RawSSEEvent { event: string; data: string }

const JSON_EVENTS = new Set(['tool_call', 'evidence', 'safety', 'error', 'done'])

/** 按 SSE 帧边界缓冲文本块,输出完整事件。token 的 data 保持原文。 */
export class SSEFrameParser {
  private buf = ''

  push(chunk: string): RawSSEEvent[] {
    this.buf += chunk
    const out: RawSSEEvent[] = []
    let idx: number
    while ((idx = this.buf.indexOf('\n\n')) !== -1) {
      const frame = this.buf.slice(0, idx)
      this.buf = this.buf.slice(idx + 2)
      const ev = this.parseFrame(frame)
      if (ev) out.push(ev)
    }
    return out
  }

  private parseFrame(frame: string): RawSSEEvent | null {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith(':')) continue // 注释(ping)
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (dataLines.length === 0) return null
    return { event, data: dataLines.join('\n') }
  }
}

/** 帧 → 聊天事件:token 原文透传(后端注释明示不可 JSON.parse),其余 JSON.parse。 */
export function decodeChatEvent(raw: RawSSEEvent): SSEChatEvent | null {
  if (raw.event === 'token') return { event: 'token', data: raw.data }
  if (!JSON_EVENTS.has(raw.event)) return null
  try {
    return { event: raw.event, data: JSON.parse(raw.data) } as SSEChatEvent
  } catch {
    return null
  }
}

/** fetch 流式 POST,逐事件回调。调用方以 done/error 事件或异常为终态。 */
export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (e: SSEChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SSEFrameParser()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
      const ev = decodeChatEvent(raw)
      if (ev) onEvent(ev)
    }
  }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/sseParser.test.ts`
Expected: 9 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/sseParser.ts frontend/tests/sseParser.test.ts
git commit -m "feat: SSE frame parser with raw-token passthrough (Task 3)"
```

---

### Task 4: 区间带定位与严重度映射

**Files:**
- Create: `frontend/src/utils/rangeStrip.ts`、`frontend/src/utils/severity.ts`
- Test: `frontend/tests/rangeStrip.test.ts`、`frontend/tests/severity.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `ItemStatus / AdviceLevel`
- Produces(Task 8/12 使用):

```ts
// rangeStrip.ts
interface StripPosition { pos: number; out: 'low' | 'high' | null }  // pos ∈ [0,1]
function stripPosition(value: number, low: number | null, high: number | null): StripPosition | null
// 双 null 或 low >= high → null(不画带);单边:仅 low → {pos:0, out: value<low?'low':null};仅 high → {pos:1, out: value>high?'high':null}
// 双边:pos = clamp((v-lo)/(hi-lo)),out 按越界侧
// severity.ts
interface SeverityMeta { dot: 'clinical' | 'amber' | 'brick' | 'dim'; tag: string | null; arrow: 'up' | 'down' | null }
function severityOf(status: ItemStatus, critical: boolean): SeverityMeta
// critical=true 或 status 含 critical_ → brick,tag '危急';high → amber ↑;low → amber ↓;
// normal → clinical;unknown → dim '无法判定';unmapped → dim '未识别'
interface AdviceMeta { label: string; tone: 'brick' | 'amber' | 'clinical' }
function adviceMeta(level: AdviceLevel): AdviceMeta
// urgent→尽快就医/brick;specialist→专科就诊/amber;recheck→定期复查/amber;lifestyle→生活方式调整/clinical
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/rangeStrip.test.ts
import { describe, expect, it } from 'vitest'
import { stripPosition } from '../src/utils/rangeStrip'

describe('stripPosition(spec-f §6.4)', () => {
  it('双边区间:按比例定位', () => {
    expect(stripPosition(4, 0, 10)).toEqual({ pos: 0.4, out: null })
    expect(stripPosition(0, 0, 10)).toEqual({ pos: 0, out: null })
  })

  it('越界吸附到端点并标记方向', () => {
    expect(stripPosition(12, 0, 10)).toEqual({ pos: 1, out: 'high' })
    expect(stripPosition(-3, 0, 10)).toEqual({ pos: 0, out: 'low' })
  })

  it('仅上界:正常贴右端,越界标 high', () => {
    expect(stripPosition(3, null, 5)).toEqual({ pos: 1, out: null })
    expect(stripPosition(6, null, 5)).toEqual({ pos: 1, out: 'high' })
  })

  it('仅下界:正常贴左端,越界标 low', () => {
    expect(stripPosition(3, 2, null)).toEqual({ pos: 0, out: null })
    expect(stripPosition(1, 2, null)).toEqual({ pos: 0, out: 'low' })
  })

  it('缺失或退化区间返回 null(不画带)', () => {
    expect(stripPosition(5, null, null)).toBeNull()
    expect(stripPosition(5, 10, 10)).toBeNull()
  })
})
```

```ts
// tests/severity.test.ts
import { describe, expect, it } from 'vitest'
import { severityOf, adviceMeta } from '../src/utils/severity'

describe('severityOf', () => {
  it('危急值(含 critical 标志)警示砖 + 危急标签', () => {
    expect(severityOf('high', true)).toEqual({ dot: 'brick', tag: '危急', arrow: 'up' })
    expect(severityOf('critical_high', false)).toEqual({ dot: 'brick', tag: '危急', arrow: 'up' })
    expect(severityOf('critical_low', false)).toEqual({ dot: 'brick', tag: '危急', arrow: 'down' })
  })

  it('高/低为信号橙带箭头,正常为临床青', () => {
    expect(severityOf('high', false)).toEqual({ dot: 'amber', tag: null, arrow: 'up' })
    expect(severityOf('low', false)).toEqual({ dot: 'amber', tag: null, arrow: 'down' })
    expect(severityOf('normal', false)).toEqual({ dot: 'clinical', tag: null, arrow: null })
  })

  it('无法判定/未识别为灰点', () => {
    expect(severityOf('unknown', false)).toEqual({ dot: 'dim', tag: '无法判定', arrow: null })
    expect(severityOf('unmapped', false)).toEqual({ dot: 'dim', tag: '未识别', arrow: null })
  })
})

describe('adviceMeta', () => {
  it('四级建议标签与色调', () => {
    expect(adviceMeta('urgent')).toEqual({ label: '尽快就医', tone: 'brick' })
    expect(adviceMeta('specialist')).toEqual({ label: '专科就诊', tone: 'amber' })
    expect(adviceMeta('recheck')).toEqual({ label: '定期复查', tone: 'amber' })
    expect(adviceMeta('lifestyle')).toEqual({ label: '生活方式调整', tone: 'clinical' })
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/rangeStrip.test.ts tests/severity.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/utils/rangeStrip.ts
export interface StripPosition { pos: number; out: 'low' | 'high' | null }

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x))
}

/** 值在参考区间带上的位置(spec-f §6.4)。返回 null 表示该行不画带。 */
export function stripPosition(value: number, low: number | null, high: number | null): StripPosition | null {
  if (low === null && high === null) return null
  if (low !== null && high !== null) {
    if (low >= high) return null
    if (value < low) return { pos: 0, out: 'low' }
    if (value > high) return { pos: 1, out: 'high' }
    return { pos: clamp01((value - low) / (high - low)), out: null }
  }
  if (high !== null) {
    return value > high ? { pos: 1, out: 'high' } : { pos: 1, out: null }
  }
  return value < (low as number) ? { pos: 0, out: 'low' } : { pos: 0, out: null }
}
```

```ts
// src/utils/severity.ts
import type { AdviceLevel, ItemStatus } from '../api/types'

export interface SeverityMeta { dot: 'clinical' | 'amber' | 'brick' | 'dim'; tag: string | null; arrow: 'up' | 'down' | null }
export interface AdviceMeta { label: string; tone: 'brick' | 'amber' | 'clinical' }

/** 圆点颜色/标签/箭头(颜色信号 = 在意等级,spec-f §6.4) */
export function severityOf(status: ItemStatus, critical: boolean): SeverityMeta {
  if (critical || status === 'critical_high' || status === 'critical_low') {
    return { dot: 'brick', tag: '危急', arrow: status.endsWith('low') ? 'down' : 'up' }
  }
  switch (status) {
    case 'high': return { dot: 'amber', tag: null, arrow: 'up' }
    case 'low': return { dot: 'amber', tag: null, arrow: 'down' }
    case 'normal': return { dot: 'clinical', tag: null, arrow: null }
    case 'unknown': return { dot: 'dim', tag: '无法判定', arrow: null }
    case 'unmapped': return { dot: 'dim', tag: '未识别', arrow: null }
  }
}

/** 建议级别 → 中文标签与色调(spec-f §6.5) */
export function adviceMeta(level: AdviceLevel): AdviceMeta {
  switch (level) {
    case 'urgent': return { label: '尽快就医', tone: 'brick' }
    case 'specialist': return { label: '专科就诊', tone: 'amber' }
    case 'recheck': return { label: '定期复查', tone: 'amber' }
    case 'lifestyle': return { label: '生活方式调整', tone: 'clinical' }
  }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/rangeStrip.test.ts tests/severity.test.ts`
Expected: 12 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils frontend/tests/rangeStrip.test.ts frontend/tests/severity.test.ts
git commit -m "feat: range-strip positioning and severity mapping (Task 4)"
```

---

### Task 5: 最近报告存储

**Files:**
- Create: `frontend/src/utils/recentReports.ts`
- Test: `frontend/tests/recentReports.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `TaskStatus`
- Produces(Task 15 使用):

```ts
interface RecentReport { id: string; created_at: string; source: 'pdf' | 'photo' | 'manual'; institution: string | null; report_date: string | null; status: TaskStatus | null }
function loadRecent(storage?: Pick<Storage, 'getItem' | 'setItem'>): RecentReport[]   // key 'report-agent:recent',坏数据返回 []
function upsertRecent(storage: Pick<Storage, 'getItem' | 'setItem'>, entry: RecentReport): RecentReport[]  // 按 id 去重前插,截断 20
function removeRecent(storage: Pick<Storage, 'getItem' | 'setItem'>, id: string): RecentReport[]
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/recentReports.test.ts
import { describe, expect, it } from 'vitest'
import { loadRecent, upsertRecent, removeRecent } from '../src/utils/recentReports'
import type { RecentReport } from '../src/utils/recentReports'

function fakeStorage(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  }
}

const r1: RecentReport = { id: 'a', created_at: '2026-09-01T00:00:00Z', source: 'pdf', institution: null, report_date: null, status: 'completed' }

describe('recentReports(spec-f §5.4)', () => {
  it('空存储返回空列表', () => {
    expect(loadRecent(fakeStorage())).toEqual([])
  })

  it('写入后去重前插', () => {
    const s = fakeStorage()
    upsertRecent(s, r1)
    upsertRecent(s, { ...r1, status: 'failed' })
    upsertRecent(s, { ...r1, id: 'b' })
    const list = loadRecent(s)
    expect(list.map(x => x.id)).toEqual(['b', 'a'])
    expect(list[1].status).toBe('failed')
  })

  it('超出 20 条截断', () => {
    const s = fakeStorage()
    for (let i = 0; i < 25; i++) upsertRecent(s, { ...r1, id: `r${i}` })
    expect(loadRecent(s)).toHaveLength(20)
    expect(loadRecent(s)[0].id).toBe('r24')
  })

  it('删除仅移除一条', () => {
    const s = fakeStorage()
    upsertRecent(s, r1)
    upsertRecent(s, { ...r1, id: 'b' })
    const list = removeRecent(s, 'a')
    expect(list.map(x => x.id)).toEqual(['b'])
  })

  it('坏 JSON 返回空列表且不抛', () => {
    expect(loadRecent(fakeStorage({ 'report-agent:recent': '{{{oops' }))).toEqual([])
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/recentReports.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/utils/recentReports.ts —— 后端无列表端点,本地记录(spec-f §5.4)
import type { TaskStatus } from '../api/types'

export interface RecentReport {
  id: string
  created_at: string
  source: 'pdf' | 'photo' | 'manual'
  institution: string | null
  report_date: string | null
  status: TaskStatus | null
}

const KEY = 'report-agent:recent'
const MAX = 20

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

export function loadRecent(storage: StorageLike = localStorage): RecentReport[] {
  try {
    const parsed = JSON.parse(storage.getItem(KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function upsertRecent(storage: StorageLike, entry: RecentReport): RecentReport[] {
  const list = [entry, ...loadRecent(storage).filter(x => x.id !== entry.id)].slice(0, MAX)
  storage.setItem(KEY, JSON.stringify(list))
  return list
}

export function removeRecent(storage: StorageLike, id: string): RecentReport[] {
  const list = loadRecent(storage).filter(x => x.id !== id)
  storage.setItem(KEY, JSON.stringify(list))
  return list
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/recentReports.test.ts`
Expected: 5 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/recentReports.ts frontend/tests/recentReports.test.ts
git commit -m "feat: localStorage recent-reports store (Task 5)"
```

---

### Task 6: 聊天状态 reducer

**Files:**
- Create: `frontend/src/utils/chatState.ts`
- Test: `frontend/tests/chatState.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `SSEChatEvent / ChatMessageRow / Verdict`
- Produces(Task 14 使用):

```ts
interface ChatTurn { role: 'user' | 'assistant'; text: string; state: 'streaming' | 'done' | 'error' | 'safety'; tool: { name: string; label: string } | null; evidence: string[]; guardrail: Verdict | null }
function toolLabel(name: string): string
// get_my_report→正在查看你的报告 / query_indicator_knowledge→正在查询指标知识 / compute_reference_range→正在核对参考区间 / search_knowledge→正在检索医学知识库 / 其余→正在查询…
function startUserTurn(text: string): ChatTurn[]   // 追加 user turn(done)+ 空 assistant turn(streaming)
function applyChatEvent(turns: ChatTurn[], ev: SSEChatEvent): ChatTurn[]  // 作用到最后一条 assistant turn;不可变更新
function turnsFromHistory(rows: ChatMessageRow[]): ChatTurn[]  // 历史恢复;guardrail_flags 非空 → state 'safety'
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/chatState.test.ts
import { describe, expect, it } from 'vitest'
import { applyChatEvent, startUserTurn, toolLabel, turnsFromHistory } from '../src/utils/chatState'

describe('startUserTurn / applyChatEvent', () => {
  it('提问追加 user + 空 assistant', () => {
    const turns = startUserTurn('什么是 ALT?')
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ role: 'user', text: '什么是 ALT?', state: 'done' })
    expect(turns[1]).toMatchObject({ role: 'assistant', text: '', state: 'streaming' })
  })

  it('token 拼接;tool_call 起止设置/清除状态行;evidence 收集', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'token', data: '你' })
    t = applyChatEvent(t, { event: 'token', data: '好' })
    t = applyChatEvent(t, { event: 'tool_call', data: { name: 'search_knowledge', status: 'start' } })
    expect(t[1].text).toBe('你好')
    expect(t[1].tool).toEqual({ name: 'search_knowledge', label: '正在检索医学知识库' })
    t = applyChatEvent(t, { event: 'evidence', data: '[e0] 证据文本' })
    t = applyChatEvent(t, { event: 'tool_call', data: { name: 'search_knowledge', status: 'end' } })
    expect(t[1].evidence).toEqual(['[e0] 证据文本'])
    expect(t[1].tool).toBeNull()
  })

  it('safety 事件整条替换并标记', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'token', data: '被丢弃的内容' })
    t = applyChatEvent(t, { event: 'safety', data: '本条回答未通过内容安全校验。' })
    expect(t[1]).toMatchObject({ text: '本条回答未通过内容安全校验。', state: 'safety' })
  })

  it('error 事件标记错误态;done 记录 guardrail', () => {
    let t = startUserTurn('x')
    t = applyChatEvent(t, { event: 'error', data: '生成失败' })
    expect(t[1].state).toBe('error')
    t = startUserTurn('y')
    t = applyChatEvent(t, { event: 'done', data: { session_id: 's1', guardrail: 'block' } })
    expect(t[1].state).toBe('done')
    expect(t[1].guardrail).toBe('block')
  })

  it('不可变更新:原数组不被修改', () => {
    const t = startUserTurn('x')
    applyChatEvent(t, { event: 'token', data: 'a' })
    expect(t[1].text).toBe('')
  })
})

describe('toolLabel', () => {
  it('四个工具名映射文案,未知名用通用文案', () => {
    expect(toolLabel('get_my_report')).toBe('正在查看你的报告')
    expect(toolLabel('query_indicator_knowledge')).toBe('正在查询指标知识')
    expect(toolLabel('compute_reference_range')).toBe('正在核对参考区间')
    expect(toolLabel('search_knowledge')).toBe('正在检索医学知识库')
    expect(toolLabel('unknown_tool')).toBe('正在查询…')
  })
})

describe('turnsFromHistory', () => {
  it('历史恢复:guardrail_flags 非空标 safety', () => {
    const turns = turnsFromHistory([
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '回答', guardrail_flags: ['diagnosis_term'], created_at: null },
    ])
    expect(turns[0]).toMatchObject({ role: 'user', state: 'done' })
    expect(turns[1]).toMatchObject({ role: 'assistant', text: '回答', state: 'safety' })
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/chatState.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/utils/chatState.ts —— 聊天视图状态机(纯函数,spec-f §5.3/§4.3)
import type { ChatMessageRow, SSEChatEvent, Verdict } from '../api/types'

export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  state: 'streaming' | 'done' | 'error' | 'safety'
  tool: { name: string; label: string } | null
  evidence: string[]
  guardrail: Verdict | null
}

const TOOL_LABELS: Record<string, string> = {
  get_my_report: '正在查看你的报告',
  query_indicator_knowledge: '正在查询指标知识',
  compute_reference_range: '正在核对参考区间',
  search_knowledge: '正在检索医学知识库',
}

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? '正在查询…'
}

function newTurn(role: 'user' | 'assistant', text: string, state: ChatTurn['state']): ChatTurn {
  return { role, text, state, tool: null, evidence: [], guardrail: null }
}

export function startUserTurn(text: string): ChatTurn[] {
  return [newTurn('user', text, 'done'), newTurn('assistant', '', 'streaming')]
}

/** 事件应用到最后一条 assistant turn;不可变更新。 */
export function applyChatEvent(turns: ChatTurn[], ev: SSEChatEvent): ChatTurn[] {
  const idx = turns.length - 1
  const last = turns[idx]
  if (!last || last.role !== 'assistant') return turns
  let next: ChatTurn
  switch (ev.event) {
    case 'token':
      next = { ...last, text: last.text + ev.data }
      break
    case 'tool_call':
      next = ev.data.status === 'start'
        ? { ...last, tool: { name: ev.data.name, label: toolLabel(ev.data.name) } }
        : { ...last, tool: null }
      break
    case 'evidence':
      next = { ...last, evidence: [...last.evidence, ev.data] }
      break
    case 'safety':
      next = { ...last, text: ev.data, state: 'safety', evidence: [] }
      break
    case 'error':
      next = { ...last, state: 'error' }
      break
    case 'done':
      next = { ...last, state: 'done', guardrail: ev.data.guardrail }
      break
  }
  return [...turns.slice(0, idx), next]
}

export function turnsFromHistory(rows: ChatMessageRow[]): ChatTurn[] {
  return rows.map(r => {
    const t = newTurn(r.role, r.content, 'done')
    if (r.role === 'assistant' && r.guardrail_flags && r.guardrail_flags.length > 0) {
      t.state = 'safety'
    }
    return t
  })
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/chatState.test.ts`
Expected: 8 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/chatState.ts frontend/tests/chatState.test.ts
git commit -m "feat: chat turn reducer with SSE event semantics (Task 6)"
```

---

### Task 7: MarkdownBlock 组件

**Files:**
- Create: `frontend/src/components/MarkdownBlock.vue`
- Test: `frontend/tests/MarkdownBlock.test.ts`

**Interfaces:**
- Consumes: Task 1 的 token 与基础样式
- Produces(所有渲染后端文本处使用):

```vue
<MarkdownBlock :text="string" />  // markdown-it(html:false, linkify, breaks)→ DOMPurify 消毒 → v-html
// 流式场景:prop 高频变化时 80ms 节流重渲染;静态文本仅一次渲染
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/MarkdownBlock.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MarkdownBlock from '../src/components/MarkdownBlock.vue'

describe('MarkdownBlock(spec-f §8.5)', () => {
  it('渲染加粗与列表', () => {
    const w = mount(MarkdownBlock, { props: { text: '**加粗**\n- 条目' } })
    expect(w.html()).toContain('<strong>加粗</strong>')
    expect(w.html()).toContain('<li>条目</li>')
  })

  it('消毒 script 与事件属性(LLM 输出不可信)', () => {
    const w = mount(MarkdownBlock, { props: { text: '<script>alert(1)</script><a onclick="x()">链接</a>' } })
    expect(w.html()).not.toContain('<script>')
    expect(w.html()).not.toContain('onclick')
  })

  it('空文本渲染空容器', () => {
    const w = mount(MarkdownBlock, { props: { text: '' } })
    expect(w.find('.md').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/MarkdownBlock.test.ts`
Expected: FAIL(找不到组件)

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/MarkdownBlock.vue -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ text: string }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const html = ref('')
let timer: ReturnType<typeof setTimeout> | null = null

function render() {
  html.value = DOMPurify.sanitize(md.render(props.text || ''))
}

// 流式场景 80ms 节流;静态文本仅首帧一次(spec-f §8.5)
render()
watch(() => props.text, () => {
  if (timer) clearTimeout(timer)
  timer = setTimeout(render, 80)
})

defineExpose({ render })
</script>

<template>
  <div class="md" v-html="html" />
</template>

<style scoped>
.md { word-break: break-word; }
.md :deep(p) { margin: 0 0 var(--space-16); }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(ul) { margin: 0 0 var(--space-16); padding-left: 1.5em; }
.md :deep(code) { font-family: var(--font-mono); font-size: 0.9em; }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/MarkdownBlock.test.ts`
Expected: 3 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MarkdownBlock.vue frontend/tests/MarkdownBlock.test.ts
git commit -m "feat: sanitized markdown renderer component (Task 7)"
```

---

### Task 8: RangeStrip 组件

**Files:**
- Create: `frontend/src/components/RangeStrip.vue`
- Test: `frontend/tests/RangeStrip.test.ts`

**Interfaces:**
- Consumes: Task 4 的 `stripPosition / severityOf`,Task 1 token
- Produces(Task 12/15 使用):

```vue
<RangeStrip :value="number|null" :low="number|null" :high="number|null" :status="ItemStatus" :critical="boolean" />
// 160×20 SVG:基线 y=10 青雾矩形(全宽 160 × 高 4);圆点 cx = 8 + pos*(160-16),r=4;
// 颜色按 severityOf(组件内经 stripPosition/severityOf 计算);出界时带端画 ↑/↓;数据不足不渲染(slot 回退由父组件处理)
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/RangeStrip.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RangeStrip from '../src/components/RangeStrip.vue'

describe('RangeStrip(spec-f §6.4)', () => {
  it('数据齐全渲染 SVG 与圆点,偏高为信号橙类', () => {
    const w = mount(RangeStrip, { props: { value: 6.31, low: 2.8, high: 5.2, status: 'high', critical: false } })
    expect(w.find('svg').exists()).toBe(true)
    expect(w.find('circle').exists()).toBe(true)
    expect(w.find('circle').classes()).toContain('dot-amber')
  })

  it('危急值警示砖类 + 箭头', () => {
    const w = mount(RangeStrip, { props: { value: 999, low: 0, high: 40, status: 'critical_high', critical: true } })
    expect(w.find('circle').classes()).toContain('dot-brick')
    expect(w.find('text').text()).toBe('↑')
  })

  it('参考区间缺失时不渲染', () => {
    const w = mount(RangeStrip, { props: { value: 5, low: null, high: null, status: 'normal', critical: false } })
    expect(w.find('svg').exists()).toBe(false)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/RangeStrip.test.ts`
Expected: FAIL(找不到组件)

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/RangeStrip.vue —— 参考区间带(spec-f §6.4 记忆点) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { ItemStatus } from '../api/types'
import { stripPosition } from '../utils/rangeStrip'
import { severityOf } from '../utils/severity'

const props = defineProps<{
  value: number | null
  low: number | null
  high: number | null
  status: ItemStatus
  critical?: boolean
}>()

const W = 160
const PAD = 8

const pos = computed(() =>
  props.value === null ? null : stripPosition(props.value, props.low, props.high),
)
const sev = computed(() => severityOf(props.status, props.critical ?? false))
const cx = computed(() => (pos.value ? PAD + pos.value.pos * (W - PAD * 2) : PAD))
const arrowX = computed(() => (pos.value?.out === 'high' ? W - PAD : PAD))
</script>

<template>
  <svg v-if="pos" class="range-strip" :width="W" height="20" role="img" aria-label="参考区间带">
    <rect class="strip-base" x="0" y="8" :width="W" height="4" rx="2" />
    <circle :class="`dot-${sev.dot}`" :cx="cx" cy="10" r="4" />
    <text v-if="pos.out" :class="`dot-${sev.dot}`" :x="arrowX" y="18"
          text-anchor="middle" font-size="10">{{ pos.out === 'high' ? '↑' : '↓' }}</text>
  </svg>
</template>

<style scoped>
/* 色值全部消费 token(全局约束:组件内不硬编码色值) */
.strip-base { fill: var(--c-mist); }
.dot-clinical { fill: var(--c-clinical); }
.dot-amber { fill: var(--c-amber); }
.dot-brick { fill: var(--c-brick); }
.dot-dim { fill: var(--c-ink); opacity: 0.6; }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/RangeStrip.test.ts`
Expected: 3 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RangeStrip.vue frontend/tests/RangeStrip.test.ts
git commit -m "feat: reference-range strip component (Task 8)"
```

---

### Task 9: UploadZone 组件(文件上传)

**Files:**
- Create: `frontend/src/components/UploadZone.vue`
- Test: `frontend/tests/UploadZone.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `createReportFromFile`
- Produces(Task 15 使用):

```vue
<UploadZone @uploaded="(e: { reportId: string; taskId: string; source: 'pdf' | 'photo' }) => void" />
// 虚线拖拽区(拖放 + 点击选择);选中即 POST;.pdf → source 'pdf',图片 → 'photo';
// 失败显示 spec-f §7 文案("这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。")
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/UploadZone.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import UploadZone from '../src/components/UploadZone.vue'
import * as api from '../src/api/reports'

describe('UploadZone', () => {
  it('选择 PDF 文件后调用上传并派发 uploaded 事件', async () => {
    const spy = vi.spyOn(api, 'createReportFromFile').mockResolvedValue({ report_id: 'r1', task_id: 't1' })
    const w = mount(UploadZone, { global: { plugins: [ElementPlus] } })
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' })
    // el-upload 内部 input change 不易模拟:直接调用组件暴露的 handleFile
    await w.vm.$nextTick()
    ;(w.vm as unknown as { handleFile: (f: File) => void }).handleFile(file)
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(file, undefined)
    expect(w.emitted('uploaded')![0]).toEqual([{ reportId: 'r1', taskId: 't1', source: 'pdf' }])
  })

  it('上传失败显示引导文案', async () => {
    vi.spyOn(api, 'createReportFromFile').mockRejectedValue(new Error('boom'))
    const w = mount(UploadZone, { global: { plugins: [ElementPlus] } })
    ;(w.vm as unknown as { handleFile: (f: File) => void }).handleFile(new File(['x'], 'a.jpg', { type: 'image/jpeg' }))
    await flushPromises()
    expect(w.text()).toContain('这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/UploadZone.test.ts`
Expected: FAIL(找不到组件)

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/UploadZone.vue —— 文件上传(spec-f §5.1/§7) -->
<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createReportFromFile } from '../api/reports'

const emit = defineEmits<{ uploaded: [{ reportId: string; taskId: string; source: 'pdf' | 'photo' }] }>()
const uploading = ref(false)

function sourceOf(file: File): 'pdf' | 'photo' {
  return file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'photo'
}

async function handleFile(file: File) {
  uploading.value = true
  try {
    const { report_id, task_id } = await createReportFromFile(file)
    emit('uploaded', { reportId: report_id, taskId: task_id, source: sourceOf(file) })
  } catch {
    ElMessage.error('这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。')
  } finally {
    uploading.value = false
  }
}

/** el-upload on-change 回调(uploadFile 为原始 File,含 .raw 字段) */
function onFileChange(uploadFile: { raw: File }) {
  handleFile(uploadFile.raw)
}

defineExpose({ handleFile })
</script>

<template>
  <div class="upload-zone">
    <el-upload drag :show-file-list="false" :auto-upload="false"
               accept=".pdf,.jpg,.jpeg,.png" :on-change="onFileChange">
      <div class="drop-hint">{{ uploading ? '正在上传…' : '拖入 PDF 或报告照片,或点击选择文件' }}</div>
    </el-upload>
  </div>
</template>

<style scoped>
.upload-zone :deep(.el-upload-dragger) {
  background: transparent;
  border: 1px dashed var(--c-hairline);
  border-radius: 4px;
  padding: var(--space-48) var(--space-16);
}
.drop-hint { color: var(--c-ink); opacity: 0.6; }
</style>
```

- [ ] **Step 4: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test tests/UploadZone.test.ts && pnpm run typecheck`
Expected: 2 test PASS;typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/UploadZone.vue frontend/tests/UploadZone.test.ts
git commit -m "feat: file upload zone component (Task 9)"
```

---

### Task 10: ManualEntryForm 组件(手动录入)

**Files:**
- Create: `frontend/src/components/ManualEntryForm.vue`
- Test: `frontend/tests/ManualEntryForm.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `createReportManual / ManualItem`
- Produces(Task 15 使用):

```vue
<ManualEntryForm @uploaded="(e: { reportId: string; taskId: string; source: 'manual' }) => void" />
// meta(性别/年龄/机构/报告日期)+ 动态条目行(项目名必填/分组/数值/单位/参考范围/异常标记),可增删;
// 提交按钮"提交并解读";校验:至少 1 条、name 非空;失败同 §7 上传失败文案
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/ManualEntryForm.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import ManualEntryForm from '../src/components/ManualEntryForm.vue'
import * as api from '../src/api/reports'

const globalOpts = { global: { plugins: [ElementPlus] } }

describe('ManualEntryForm', () => {
  it('空提交显示校验提示且不请求', async () => {
    const spy = vi.spyOn(api, 'createReportManual')
    const w = mount(ManualEntryForm, globalOpts)
    await w.find('button[type="submit"]').trigger('click')
    await flushPromises()
    expect(spy).not.toHaveBeenCalled()
    expect(w.text()).toContain('至少填写一条检验项目')
  })

  it('填一条后提交成功并派发 uploaded', async () => {
    vi.spyOn(api, 'createReportManual').mockResolvedValue({ report_id: 'r9', task_id: 't9' })
    const w = mount(ManualEntryForm, globalOpts)
    const vm = w.vm as unknown as {
      rows: { name: string }[]; sex: string; age: number | null
      submit: () => Promise<void>
    }
    vm.rows[0].name = '丙氨酸氨基转移酶'
    vm.sex = '男'
    vm.age = 35
    await vm.submit()
    await flushPromises()
    expect(w.emitted('uploaded')![0]).toEqual([{ reportId: 'r9', taskId: 't9', source: 'manual' }])
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/ManualEntryForm.test.ts`
Expected: FAIL(找不到组件)

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/ManualEntryForm.vue —— JSON 手动录入(spec-f §5.1,字段对齐 ManualItem) -->
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createReportManual } from '../api/reports'
import type { ManualEntry, ManualItem } from '../api/types'

const emit = defineEmits<{ uploaded: [{ reportId: string; taskId: string; source: 'manual' }] }>()

const sex = ref<string>('')
const age = ref<number | null>(null)
const institution = ref('')
const reportDate = ref('')
const rows = reactive<ManualItem[]>([{ name: '' }])
const submitting = ref(false)

function addRow() {
  rows.push({ name: '' })
}
function removeRow(i: number) {
  rows.splice(i, 1)
}

async function submit() {
  const items = rows.filter(r => r.name.trim())
  if (items.length === 0) {
    ElMessage.warning('至少填写一条检验项目')
    return
  }
  submitting.value = true
  const entry: ManualEntry = {
    meta: { sex: sex.value || null, age: age.value ?? null,
            institution: institution.value || null, report_date: reportDate.value || null },
    items,
  }
  try {
    const { report_id, task_id } = await createReportManual(entry)
    emit('uploaded', { reportId: report_id, taskId: task_id, source: 'manual' })
  } catch {
    ElMessage.error('这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。')
  } finally {
    submitting.value = false
  }
}

defineExpose({ rows, sex, age, submit })
</script>

<template>
  <el-form label-position="top" @submit.prevent="submit">
    <div class="meta-row">
      <el-form-item label="性别" class="meta-field">
        <el-select v-model="sex" clearable placeholder="未知">
          <el-option label="男" value="男" /><el-option label="女" value="女" />
        </el-select>
      </el-form-item>
      <el-form-item label="年龄" class="meta-field">
        <el-input-number v-model="age" :min="0" :max="120" placeholder="未知" />
      </el-form-item>
      <el-form-item label="体检机构" class="meta-field">
        <el-input v-model="institution" placeholder="选填" />
      </el-form-item>
      <el-form-item label="报告日期" class="meta-field">
        <el-date-picker v-model="reportDate" type="date" value-format="YYYY-MM-DD" placeholder="选填" />
      </el-form-item>
    </div>

    <div class="item-head">检验项目(至少一条)</div>
    <div v-for="(row, i) in rows" :key="i" class="item-row">
      <el-input v-model="row.name" placeholder="项目名,如:总胆固醇" class="field-name" />
      <el-input v-model="row.value_text" placeholder="数值,如:6.31" class="field" />
      <el-input v-model="row.unit" placeholder="单位" class="field" />
      <el-input v-model="row.ref_range_text" placeholder="参考范围" class="field" />
      <el-button class="btn-del" text type="danger" :disabled="rows.length === 1" @click="removeRow(i)">删</el-button>
    </div>
    <el-button class="btn-add" text type="primary" @click="addRow">+ 添加一行</el-button>

    <div class="submit-row">
      <el-button type="primary" native-type="submit" :loading="submitting">提交并解读</el-button>
    </div>
  </el-form>
</template>

<style scoped>
.meta-row { display: flex; gap: var(--space-16); flex-wrap: wrap; }
.meta-field { flex: 1; min-width: 140px; }
.item-head { font-size: 13px; opacity: 0.6; margin: var(--space-16) 0 var(--space-8); }
.item-row { display: flex; gap: var(--space-8); margin-bottom: var(--space-8); align-items: center; }
.field-name { flex: 2; }
.field { flex: 1; }
.submit-row { margin-top: var(--space-16); }
</style>
```

- [ ] **Step 4: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test tests/ManualEntryForm.test.ts && pnpm run typecheck`
Expected: 2 test PASS;typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ManualEntryForm.vue frontend/tests/ManualEntryForm.test.ts
git commit -m "feat: manual entry form component (Task 10)"
```

---

### Task 11: 任务轮询与进度组件

**Files:**
- Create: `frontend/src/composables/useTaskPolling.ts`、`frontend/src/components/TaskProgress.vue`、`frontend/src/components/MetaPatchForm.vue`
- Test: `frontend/tests/useTaskPolling.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `getTask / patchReportMeta / TaskInfo / TaskStatus`,Task 1 token
- Produces(Task 15 使用):

```ts
// useTaskPolling.ts
interface PollCallbacks {
  onUpdate: (info: TaskInfo) => void
  onAwaitingMeta: (info: TaskInfo) => void   // 停轮询,等 PATCH
  onDone: (info: TaskInfo) => void           // completed / degraded
  onFailed: (info: TaskInfo) => void
}
function createTaskPoller(fetchTask: (id: string) => Promise<TaskInfo>, opts?: { baseMs?: number; maxMs?: number }):
  { start: (taskId: string, cb: PollCallbacks) => void; stop: () => void }
// 立即首查,然后 baseMs(默认 2000)指数退避至 maxMs(默认 8000);终态/awaiting_meta 自动停
// TaskProgress.vue: props { info: TaskInfo };7 阶段细进度带(§6.3 脉冲当前段;阶段中文名见下)
// MetaPatchForm.vue: props { reportId: string }; emit('saved');按钮"保存并继续解读"(spec-f §7)
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/useTaskPolling.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createTaskPoller } from '../src/composables/useTaskPolling'
import type { TaskInfo } from '../src/api/types'

function info(status: TaskInfo['status']): TaskInfo {
  return { task_id: 't1', report_id: 'r1', status, stage: 'parse',
           timings: {}, completed_stages: [], error: null }
}

describe('createTaskPoller(spec-f §5.2)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('立即首查,运行态按指数退避继续', async () => {
    const fetchTask = vi.fn()
      .mockResolvedValueOnce(info('running'))
      .mockResolvedValueOnce(info('running'))
      .mockResolvedValueOnce(info('completed'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask, { baseMs: 2000, maxMs: 8000 })
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchTask).toHaveBeenCalledTimes(1)
    expect(cb.onUpdate).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000) // 第二次(2s)
    await vi.advanceTimersByTimeAsync(4000) // 第三次(4s 退避)
    expect(fetchTask).toHaveBeenCalledTimes(3)
    expect(cb.onDone).toHaveBeenCalledTimes(1)
    // 终态后停止
    await vi.advanceTimersByTimeAsync(8000)
    expect(fetchTask).toHaveBeenCalledTimes(3)
  })

  it('awaiting_meta 触发回调并停止', async () => {
    const fetchTask = vi.fn().mockResolvedValue(info('awaiting_meta'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask)
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onAwaitingMeta).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(8000)
    expect(fetchTask).toHaveBeenCalledTimes(1)
  })

  it('failed 触发 onFailed;stop 立即停止;可重新 start', async () => {
    const fetchTask = vi.fn().mockResolvedValue(info('failed'))
    const cb = { onUpdate: vi.fn(), onAwaitingMeta: vi.fn(), onDone: vi.fn(), onFailed: vi.fn() }
    const p = createTaskPoller(fetchTask)
    p.start('t1', cb)
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onFailed).toHaveBeenCalledTimes(1)

    fetchTask.mockResolvedValue(info('completed'))
    p.start('t1', cb) // PATCH 后恢复
    await vi.advanceTimersByTimeAsync(0)
    expect(cb.onDone).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/useTaskPolling.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/composables/useTaskPolling.ts —— 轮询状态机(spec-f §5.2)
import type { TaskInfo } from '../api/types'

export interface PollCallbacks {
  onUpdate: (info: TaskInfo) => void
  onAwaitingMeta: (info: TaskInfo) => void
  onDone: (info: TaskInfo) => void
  onFailed: (info: TaskInfo) => void
}

export function createTaskPoller(
  fetchTask: (id: string) => Promise<TaskInfo>,
  opts: { baseMs?: number; maxMs?: number } = {},
) {
  const baseMs = opts.baseMs ?? 2000
  const maxMs = opts.maxMs ?? 8000
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  function stop() {
    stopped = true
    if (timer) clearTimeout(timer)
    timer = null
  }

  function start(taskId: string, cb: PollCallbacks) {
    stop()
    stopped = false
    let attempt = 0
    const tick = async () => {
      if (stopped) return
      let info: TaskInfo
      try {
        info = await fetchTask(taskId)
      } catch {
        // 网络抖动:重试同延迟,不冒泡(spec-f §11 降级不崩溃)
        schedule()
        return
      }
      switch (info.status) {
        case 'pending':
        case 'running':
          cb.onUpdate(info)
          schedule()
          break
        case 'awaiting_meta':
          cb.onAwaitingMeta(info)
          stopped = true
          break
        case 'completed':
        case 'degraded':
          cb.onDone(info)
          stopped = true
          break
        case 'failed':
          cb.onFailed(info)
          stopped = true
          break
      }
    }
    const schedule = () => {
      if (stopped) return
      const delay = Math.min(baseMs * 2 ** attempt, maxMs)
      attempt += 1
      timer = setTimeout(tick, delay)
    }
    void tick()
  }

  return { start, stop }
}
```

```vue
<!-- src/components/TaskProgress.vue —— 7 阶段细进度带(spec-f §5.1/§6.3) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { TaskInfo } from '../api/types'

const props = defineProps<{ info: TaskInfo }>()

const STAGES = [
  { key: 'parse', label: '解析报告' },
  { key: 'normalize', label: '标准化' },
  { key: 'compare', label: '规则判定' },
  { key: 'retrieve', label: '检索知识' },
  { key: 'generate', label: '生成解读' },
  { key: 'guardrail', label: '安全校验' },
  { key: 'plan', label: '复查计划' },
] // 阶段 key 对齐后端 STAGE_ORDER(spec-f §5.1)

const currentIndex = computed(() =>
  props.info.stage ? STAGES.findIndex(s => s.key === props.info.stage) : -1,
)
const elapsed = computed(() =>
  props.info.timings ? Math.round(Object.values(props.info.timings).reduce((a, b) => a + b, 0)) : null,
)
</script>

<template>
  <div class="task-progress">
    <div class="stage-line">
      <template v-for="(s, i) in STAGES" :key="s.key">
        <span class="stage-seg" :class="{
          done: info.completed_stages.includes(s.key),
          current: i === currentIndex,
        }" />
        <span v-if="i < STAGES.length - 1" class="stage-gap" />
      </template>
    </div>
    <div class="stage-meta">
      <span>{{ info.stage ? STAGES[currentIndex]?.label ?? '处理中' : '排队中' }}</span>
      <span v-if="elapsed !== null" class="mono">已用 {{ elapsed }}s</span>
    </div>
  </div>
</template>

<style scoped>
.stage-line { display: flex; align-items: center; }
.stage-seg { flex: 1; height: 4px; border-radius: 2px; background: var(--c-hairline); }
.stage-gap { width: 4px; }
.stage-seg.done { background: var(--c-clinical); }
.stage-seg.current { background: var(--c-clinical); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.stage-meta { display: flex; justify-content: space-between; font-size: 13px; opacity: 0.6; margin-top: var(--space-8); }
</style>
```

```vue
<!-- src/components/MetaPatchForm.vue —— 性别/年龄补录(spec-f §5.1/§7) -->
<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { patchReportMeta } from '../api/reports'

const props = defineProps<{ reportId: string }>()
const emit = defineEmits<{ saved: [] }>()

const sex = ref<string>('')
const age = ref<number | null>(null)
const saving = ref(false)

async function save() {
  if (!sex.value && age.value === null) {
    ElMessage.warning('请至少填写性别或年龄')
    return
  }
  saving.value = true
  try {
    await patchReportMeta(props.reportId, { sex: sex.value || undefined, age: age.value ?? undefined })
    emit('saved')
  } catch {
    ElMessage.error('保存失败,请稍后重试')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="meta-patch">
    <div class="patch-title">报告缺少性别或年龄,补充后继续解读</div>
    <div class="patch-row">
      <el-select v-model="sex" placeholder="性别">
        <el-option label="男" value="男" /><el-option label="女" value="女" />
      </el-select>
      <el-input-number v-model="age" :min="0" :max="120" placeholder="年龄" />
      <el-button type="primary" :loading="saving" @click="save">保存并继续解读</el-button>
    </div>
  </div>
</template>

<style scoped>
.patch-title { margin-bottom: var(--space-8); }
.patch-row { display: flex; gap: var(--space-8); align-items: center; }
</style>
```

- [ ] **Step 4: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test tests/useTaskPolling.test.ts && pnpm run typecheck`
Expected: 3 test PASS;typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/useTaskPolling.ts frontend/src/components/TaskProgress.vue frontend/src/components/MetaPatchForm.vue frontend/tests/useTaskPolling.test.ts
git commit -m "feat: task polling composable with progress and meta patch UI (Task 11)"
```

---

### Task 12: 逐项解读(数据关联 + 四段中的三文本段)

**Files:**
- Create: `frontend/src/utils/joinInterpretation.ts`、`frontend/src/components/SectionSummary.vue`、`frontend/src/components/SectionAdvice.vue`、`frontend/src/components/SectionDisclaimer.vue`、`frontend/src/components/SectionItems.vue`
- Test: `frontend/tests/joinInterpretation.test.ts`、`frontend/tests/SectionItems.test.ts`

**Interfaces:**
- Consumes: Task 2 类型、Task 4、Task 7、Task 8
- Produces(Task 15 使用):

```ts
// joinInterpretation.ts
interface JoinedItem { interp: InterpretationItem; norm: NormalizedItem | null }
function joinInterpretation(interp: InterpretationItem[], norm: NormalizedItem[]): JoinedItem[]
// 先按 indicator_code(双方非空)匹配,未匹配按 name(item_name)匹配;一个 norm 只被用一次(spec-f §6.4)
// SectionSummary/Advice/Disclaimer: <X :text="string" />(衬线章节标题 + MarkdownBlock)
// SectionItems: props { doc: InterpretationDoc; normalized: NormalizedItem[] }
// 两栏条带行:左数据列(名/等宽数值/区间带/参考区间),右解读(meaning/risks/advice);adviceMeta 标签;evidence 数量脚注
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/joinInterpretation.test.ts
import { describe, expect, it } from 'vitest'
import { joinInterpretation } from '../src/utils/joinInterpretation'
import type { InterpretationItem, NormalizedItem } from '../src/api/types'

const interp = (name: string, code: string | null): InterpretationItem => ({
  indicator_code: code, name, status: 'high', value_text: `${name} 6.31`,
  meaning: 'm', risks: [], advice_level: 'recheck', advice: 'a', evidence_ids: [],
})
const norm = (item_name: string, code: string | null): NormalizedItem => ({
  item_name, indicator_code: code, value_num: 6.31, unit: 'mmol/L',
  status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null,
})

describe('joinInterpretation(spec-f §6.4)', () => {
  it('优先按 indicator_code 关联', () => {
    const joined = joinInterpretation([interp('总胆固醇', 'TC')], [norm('胆固醇', 'TC')])
    expect(joined[0].norm?.item_name).toBe('胆固醇')
  })

  it('code 缺失时按名称关联', () => {
    const joined = joinInterpretation([interp('总胆固醇', null)], [norm('总胆固醇', null)])
    expect(joined[0].norm).not.toBeNull()
  })

  it('无匹配 norm 为 null(该行不画带)', () => {
    const joined = joinInterpretation([interp('神秘指标', null)], [norm('ALT', 'ALT')])
    expect(joined[0].norm).toBeNull()
  })

  it('一个 norm 只被用一次', () => {
    const joined = joinInterpretation([interp('甲', 'X'), interp('乙', 'X')], [norm('甲', 'X')])
    expect(joined[0].norm).not.toBeNull()
    expect(joined[1].norm).toBeNull()
  })
})
```

```ts
// tests/SectionItems.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SectionItems from '../src/components/SectionItems.vue'
import type { InterpretationDoc, NormalizedItem } from '../src/api/types'

const doc: InterpretationDoc = {
  summary: 's',
  items: [{
    indicator_code: 'TC', name: '总胆固醇', status: 'high',
    value_text: '6.31 mmol/L(参考区间 2.8~5.2)', meaning: '高于参考区间',
    risks: [], advice_level: 'recheck', advice: '建议低脂饮食', evidence_ids: ['e0'],
  }],
  advice_summary: '', disclaimer: '', degraded: false,
}
const norm: NormalizedItem = {
  item_name: '总胆固醇', indicator_code: 'TC', value_num: 6.31, unit: 'mmol/L',
  status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null,
}

describe('SectionItems(spec-f §6.5)', () => {
  it('两栏条带行:数值/区间带/解读/建议标签/证据脚注', () => {
    const w = mount(SectionItems, { props: { doc, normalized: [norm] } })
    expect(w.text()).toContain('总胆固醇')
    expect(w.text()).toContain('高于参考区间')
    expect(w.text()).toContain('建议低脂饮食')
    expect(w.text()).toContain('定期复查')
    expect(w.text()).toContain('引用知识库 1 条')
    expect(w.find('svg').exists()).toBe(true)
  })

  it('图例只出现一次', () => {
    const w = mount(SectionItems, { props: { doc, normalized: [norm] } })
    expect(w.text().split('参考区间').length - 1).toBe(2) // 图例 1 + 数据 1
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/joinInterpretation.test.ts tests/SectionItems.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/utils/joinInterpretation.ts
import type { InterpretationItem, NormalizedItem } from '../api/types'

export interface JoinedItem { interp: InterpretationItem; norm: NormalizedItem | null }

/** 解读条目与归一化数值按 code → 名称关联;norm 只被用一次(spec-f §6.4)。 */
export function joinInterpretation(interp: InterpretationItem[], norm: NormalizedItem[]): JoinedItem[] {
  const unused = [...norm]
  const take = (pred: (n: NormalizedItem) => boolean): NormalizedItem | null => {
    const i = unused.findIndex(pred)
    if (i === -1) return null
    return unused.splice(i, 1)[0]
  }
  return interp.map(it => {
    const n = (it.indicator_code
      ? take(x => x.indicator_code === it.indicator_code)
      : null) ?? take(x => x.item_name === it.name)
    return { interp: it, norm: n }
  })
}
```

```vue
<!-- src/components/SectionSummary.vue -->
<script setup lang="ts">
import MarkdownBlock from './MarkdownBlock.vue'
defineProps<{ text: string }>()
</script>

<template>
  <section class="section">
    <h2 class="section-title">总体结论</h2>
    <MarkdownBlock :text="text" />
  </section>
</template>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 var(--space-16);
}
</style>
```

```vue
<!-- src/components/SectionAdvice.vue —— advice_summary 原样渲染,不重组(spec-f §6.5) -->
<script setup lang="ts">
import MarkdownBlock from './MarkdownBlock.vue'
defineProps<{ text: string }>()
</script>

<template>
  <section class="section">
    <h2 class="section-title">分级建议</h2>
    <MarkdownBlock :text="text" />
  </section>
</template>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 var(--space-16);
}
</style>
```

```vue
<!-- src/components/SectionDisclaimer.vue -->
<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <section class="section disclaimer hairline-top">
    <p>{{ text }}</p>
  </section>
</template>

<style scoped>
.disclaimer { margin-top: var(--space-48); padding-top: var(--space-16); font-size: 13px; opacity: 0.7; }
</style>
```

```vue
<!-- src/components/SectionItems.vue —— 两栏条带行(spec-f §6.5) -->
<script setup lang="ts">
import { computed } from 'vue'
import type { InterpretationDoc, NormalizedItem } from '../api/types'
import { joinInterpretation } from '../utils/joinInterpretation'
import { adviceMeta } from '../utils/severity'
import MarkdownBlock from './MarkdownBlock.vue'
import RangeStrip from './RangeStrip.vue'

const props = defineProps<{ doc: InterpretationDoc; normalized: NormalizedItem[] }>()

const joined = computed(() => joinInterpretation(props.doc.items, props.normalized))

function toneClass(tone: string): string {
  return `tag-${tone}`
}
</script>

<template>
  <section class="section">
    <div class="head-row">
      <h2 class="section-title">逐项解读</h2>
      <span class="legend">— 参考区间 ● 你的结果</span>
    </div>
    <div v-for="(j, i) in joined" :key="i" class="item-band" :class="{ 'hairline-top': i > 0 }">
      <div class="data-col">
        <div class="item-name">{{ j.interp.name }}</div>
        <div class="mono item-value">{{ j.interp.value_text }}</div>
        <RangeStrip v-if="j.norm" :value="j.norm.value_num" :low="j.norm.ref_low"
                    :high="j.norm.ref_high" :status="j.norm.status" :critical="j.norm.critical" />
      </div>
      <div class="text-col">
        <MarkdownBlock :text="j.interp.meaning" />
        <ul v-if="j.interp.risks.length" class="risks">
          <li v-for="(r, k) in j.interp.risks" :key="k">{{ r }}</li>
        </ul>
        <div class="advice-line">
          <span class="tag" :class="toneClass(adviceMeta(j.interp.advice_level).tone)">
            {{ adviceMeta(j.interp.advice_level).label }}
          </span>
          <span>{{ j.interp.advice }}</span>
        </div>
        <div v-if="j.interp.evidence_ids.length" class="evidence-note">
          引用知识库 {{ j.interp.evidence_ids.length }} 条
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: baseline; }
.section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 var(--space-16);
}
.legend { font-size: 13px; opacity: 0.6; }
.item-band { display: flex; gap: var(--space-16); padding: var(--space-16) 0; }
.data-col { width: 320px; flex-shrink: 0; }
.item-name { font-weight: 600; }
.item-value { font-size: 15px; margin: var(--space-8) 0; }
.risks { margin: 0 0 var(--space-8); padding-left: 1.5em; }
.advice-line { display: flex; gap: var(--space-8); align-items: baseline; }
.tag { font-size: 12px; padding: 0 6px; border-radius: 4px; color: #fff; flex-shrink: 0; }
.tag-brick { background: var(--c-brick); }
.tag-amber { background: var(--c-amber); }
.tag-clinical { background: var(--c-clinical); }
.evidence-note { font-size: 12px; opacity: 0.5; margin-top: var(--space-8); }
</style>
```

- [ ] **Step 4: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test tests/joinInterpretation.test.ts tests/SectionItems.test.ts && pnpm run typecheck`
Expected: 6 test PASS;typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/joinInterpretation.ts frontend/src/components/SectionSummary.vue frontend/src/components/SectionAdvice.vue frontend/src/components/SectionDisclaimer.vue frontend/src/components/SectionItems.vue frontend/tests/joinInterpretation.test.ts frontend/tests/SectionItems.test.ts
git commit -m "feat: per-item interpretation sections with joined range strips (Task 12)"
```

---

### Task 13: FollowupList 组件

**Files:**
- Create: `frontend/src/components/FollowupList.vue`
- Test: `frontend/tests/FollowupList.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `FollowupPlanDoc`
- Produces(Task 15 使用):

```vue
<FollowupList :doc="FollowupPlanDoc" />  // 扁平列表:项目 / 时间 / 科室 / 依据(不画时间轴,spec-f §6.6)
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/FollowupList.test.ts
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FollowupList from '../src/components/FollowupList.vue'

describe('FollowupList(spec-f §6.6)', () => {
  it('渲染项目/时间/科室/依据四列', () => {
    const w = mount(FollowupList, { props: { doc: {
      items: [{ item: '总胆固醇(危急值)复查', timeframe: '立即', department: '心内科', basis: '达危急值水平' }],
      degraded: false,
    } } })
    expect(w.text()).toContain('总胆固醇(危急值)复查')
    expect(w.text()).toContain('立即')
    expect(w.text()).toContain('心内科')
    expect(w.text()).toContain('达危急值水平')
  })

  it('空计划显示空态文案', () => {
    const w = mount(FollowupList, { props: { doc: { items: [], degraded: false } } })
    expect(w.text()).toContain('本次体检无需特别复查项目')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/FollowupList.test.ts`
Expected: FAIL(找不到组件)

- [ ] **Step 3: 写实现**

```vue
<!-- src/components/FollowupList.vue -->
<script setup lang="ts">
import type { FollowupPlanDoc } from '../api/types'
defineProps<{ doc: FollowupPlanDoc }>()
</script>

<template>
  <div v-if="doc.items.length === 0" class="empty">本次体检无需特别复查项目</div>
  <div v-else class="plan-list">
    <div v-for="(f, i) in doc.items" :key="i" class="plan-row" :class="{ 'hairline-top': i > 0 }">
      <div class="col col-item">{{ f.item }}</div>
      <div class="col col-time mono">{{ f.timeframe }}</div>
      <div class="col col-dept">{{ f.department }}</div>
      <div class="col col-basis">{{ f.basis }}</div>
    </div>
  </div>
</template>

<style scoped>
.plan-row { display: flex; gap: var(--space-16); padding: var(--space-16) 0; }
.col-item { width: 260px; flex-shrink: 0; font-weight: 600; }
.col-time { width: 90px; flex-shrink: 0; }
.col-dept { width: 140px; flex-shrink: 0; }
.col-basis { flex: 1; opacity: 0.8; }
.empty { padding: var(--space-16) 0; opacity: 0.6; }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && pnpm test tests/FollowupList.test.ts`
Expected: 2 test PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FollowupList.vue frontend/tests/FollowupList.test.ts
git commit -m "feat: followup plan list component (Task 13)"
```

---

### Task 14: ChatPanel 组件与 useChatSSE

**Files:**
- Create: `frontend/src/composables/useChatSSE.ts`、`frontend/src/components/ChatPanel.vue`
- Test: `frontend/tests/useChatSSE.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `createChatSession / getChatHistory / ChatMessageRow`、Task 3 的 `streamSSE`、Task 6 的 reducer、Task 7
- Produces(Task 15 使用):

```vue
<ChatPanel :report-id="string" />
// 进入:createChatSession → getChatHistory → useChatSSE.init(rows);发送 → useChatSSE.send(sessionId, content)
// 流异常后由 send 内部以 fetchHistory 覆盖本地视图(§5.3 同步策略);发送中禁用输入
```

- [ ] **Step 1: 写失败测试**

```ts
// tests/useChatSSE.test.ts
import { describe, expect, it, vi } from 'vitest'
import { useChatSSE } from '../src/composables/useChatSSE'
import type { ChatMessageRow } from '../src/api/types'
import { streamSSE } from '../src/api/sseParser'

vi.mock('../src/api/sseParser', () => ({
  streamSSE: vi.fn(),
}))

describe('useChatSSE(spec-f §5.3)', () => {
  it('send 时通过 streamSSE 流式发出,事件驱动 turns', async () => {
    const mockStream = vi.mocked(streamSSE).mockImplementation(async (_url, _body, onEvent) => {
      onEvent({ event: 'token', data: '你' })
      onEvent({ event: 'token', data: '好' })
      onEvent({ event: 'done', data: { session_id: 's1', guardrail: 'pass' } })
    })
    const history: ChatMessageRow[] = [
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '完整回答', guardrail_flags: null, created_at: null },
    ]
    const { turns, init, send } = useChatSSE(async () => history)
    init(history)
    await send('s1', '新提问')
    expect(mockStream).toHaveBeenCalledWith('/api/chat/sessions/s1/messages', { content: '新提问' }, expect.any(Function), expect.anything())
    expect(turns.value).toHaveLength(4) // 历史 2 + 新提问 user/assistant
    expect(turns.value[3].text).toBe('你好')
    expect(turns.value[3].state).toBe('done')
  })

  it('流异常后以 history 覆盖本地视图', async () => {
    vi.mocked(streamSSE).mockRejectedValue(new Error('network'))
    const history: ChatMessageRow[] = [
      { role: 'user', content: '问题', guardrail_flags: null, created_at: null },
      { role: 'assistant', content: '完整回答', guardrail_flags: null, created_at: null },
    ]
    const { turns, init, send } = useChatSSE(async () => history)
    init(history)
    await send('s1', '问题')
    // 覆盖同步:原 streaming 半截丢弃,历史完整回答呈现
    expect(turns.value.map(t => t.text)).toContain('完整回答')
    expect(turns.value.every(t => t.state === 'done')).toBe(true)
  })

  it('history 不可用时将 streaming 末条标记为 error', async () => {
    vi.mocked(streamSSE).mockRejectedValue(new Error('network'))
    const { turns, send } = useChatSSE(async () => { throw new Error('db down') })
    await send('s1', '问题')
    expect(turns.value[turns.value.length - 1].state).toBe('error')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/useChatSSE.test.ts`
Expected: FAIL(找不到模块)

- [ ] **Step 3: 写实现**

```ts
// src/composables/useChatSSE.ts —— SSE 追问(spec-f §5.3)
import { ref } from 'vue'
import type { ChatMessageRow } from '../api/types'
import { streamSSE } from '../api/sseParser'
import { applyChatEvent, startUserTurn, turnsFromHistory } from '../utils/chatState'
import type { ChatTurn } from '../utils/chatState'

export function useChatSSE(
  fetchHistory: (sessionId: string) => Promise<ChatMessageRow[]>,
) {
  const turns = ref<ChatTurn[]>([])
  const sending = ref(false)
  const errorMsg = ref<string | null>(null)

  function init(rows: ChatMessageRow[]) {
    turns.value = turnsFromHistory(rows)
  }

  async function send(sessionId: string, content: string) {
    sending.value = true
    errorMsg.value = null
    turns.value = [...turns.value, ...startUserTurn(content)]
    const ac = new AbortController()
    try {
      await streamSSE(
        `/api/chat/sessions/${sessionId}/messages`,
        { content },
        ev => { turns.value = applyChatEvent(turns.value, ev) },
        ac.signal,
      )
      // error 事件(服务端生成失败,未持久化回答)保留本地 error 态,用户重发;不覆盖
    } catch {
      // 传输层断连:后端流会跑完并持久化完整回答 → history 覆盖本地(spec-f §5.3)
      await syncHistory(sessionId)
    } finally {
      sending.value = false
    }
  }

  async function syncHistory(sessionId: string) {
    try {
      const rows = await fetchHistory(sessionId)
      const last = turns.value[turns.value.length - 1]
      // 仅当本地末条仍是 streaming(未收到 done)时覆盖;否则保留已完成的流
      if (last && last.state === 'streaming') {
        turns.value = turnsFromHistory(rows)
      }
    } catch {
      turns.value = turns.value.map(t =>
        t.state === 'streaming' ? { ...t, state: 'error' as const } : t,
      )
      errorMsg.value = '生成失败,请重试'
    }
  }

  return { turns, sending, errorMsg, init, send }
}
```

```vue
<!-- src/components/ChatPanel.vue —— 追问面板(spec-f §5.1/§6.6) -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createChatSession, getChatHistory } from '../api/chat'
import { useChatSSE } from '../composables/useChatSSE'
import MarkdownBlock from './MarkdownBlock.vue'

const props = defineProps<{ reportId: string }>()

const sessionId = ref<string | null>(null)
const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const { turns, sending, errorMsg, init, send } = useChatSSE(getChatHistory)

onMounted(async () => {
  try {
    const { session_id } = await createChatSession(props.reportId)
    sessionId.value = session_id
    init(await getChatHistory(session_id))
  } catch {
    ElMessage.error('追问服务暂不可用,请稍后刷新重试')
  }
})

async function doSend() {
  const content = input.value.trim()
  if (!content || sending.value || !sessionId.value) return
  input.value = ''
  await send(sessionId.value, content)
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
}
</script>

<template>
  <div class="chat-panel">
    <div ref="listRef" class="chat-list">
      <div v-for="(t, i) in turns" :key="i" class="turn" :class="t.role">
        <div class="bubble">
          <div v-if="t.role === 'assistant' && t.tool" class="tool-line">{{ t.tool.label }}</div>
          <MarkdownBlock v-if="t.role === 'assistant'" :text="t.text" />
          <template v-else>{{ t.text }}</template>
          <div v-if="t.state === 'safety'" class="safety-note">本条回答经安全校验调整</div>
          <div v-if="t.state === 'error'" class="error-note">生成失败,请重试</div>
        </div>
        <div v-if="t.role === 'assistant' && t.evidence.length" class="evidence">
          <div v-for="(e, k) in t.evidence" :key="k" class="evidence-item">{{ e }}</div>
        </div>
      </div>
      <div v-if="errorMsg" class="error-line">{{ errorMsg }}</div>
    </div>
    <div class="chat-input hairline-top">
      <el-input v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                placeholder="就报告里的任何一项提问"
                :disabled="sending || !sessionId"
                @keydown.enter.exact.prevent="doSend" />
      <el-button type="primary" :disabled="!input.trim() || sending || !sessionId" @click="doSend">发送</el-button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel { display: flex; flex-direction: column; height: 60vh; }
.chat-list { flex: 1; overflow-y: auto; }
.turn { margin: var(--space-16) 0; display: flex; flex-direction: column; }
.turn.user { align-items: flex-end; }
.bubble {
  max-width: 75%;
  padding: var(--space-8) var(--space-16);
  border-radius: 4px;
  background: var(--c-paper);
  border: 1px solid var(--c-hairline);
}
.turn.user .bubble { background: var(--c-mist); border: none; }
.tool-line { font-size: 12px; opacity: 0.5; margin-bottom: var(--space-8); }
.safety-note, .error-note, .error-line { font-size: 12px; opacity: 0.6; margin-top: var(--space-8); }
.evidence { max-width: 75%; margin-top: var(--space-8); font-size: 12px; opacity: 0.6; }
.chat-input { display: flex; gap: var(--space-8); padding-top: var(--space-16); align-items: flex-end; }
</style>
```

- [ ] **Step 4: 运行测试确认通过 + 类型检查**

Run: `cd frontend && pnpm test tests/useChatSSE.test.ts && pnpm run typecheck`
Expected: 3 test PASS;typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/useChatSSE.ts frontend/src/components/ChatPanel.vue frontend/tests/useChatSSE.test.ts
git commit -m "feat: chat panel with SSE streaming and history sync (Task 14)"
```

---

### Task 15: 视图组装(HomeView / ReportView / 404 / 路由)

**Files:**
- Create: `frontend/src/views/HomeView.vue`、`frontend/src/views/ReportView.vue`、`frontend/src/views/NotFoundView.vue`
- Modify: `frontend/src/router/index.ts`(填充路由表)
- Test: `frontend/tests/views.test.ts`

**Interfaces:**
- Consumes: Task 1/2/4/5/8/9/10/11/12/13/14 全部产出
- Produces: 完整可用的应用(§10 验收清单的载体)

- [ ] **Step 1: 写失败测试(冒烟挂载,spec-f §9)**

```ts
// tests/views.test.ts
// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import HomeView from '../src/views/HomeView.vue'
import ReportView from '../src/views/ReportView.vue'
import * as reportsApi from '../src/api/reports'
import * as tasksApi from '../src/api/tasks'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { id: 'r1' } }),
}))

const globalOpts = { global: { plugins: [ElementPlus] } }

describe('视图冒烟(spec-f §9)', () => {
  it('HomeView 渲染论点区/上传 tabs/最近报告空态', () => {
    const w = mount(HomeView, globalOpts)
    expect(w.text()).toContain('看懂你的体检报告')
    expect(w.text()).toContain('上传文件')
    expect(w.text()).toContain('手动录入')
    expect(w.text()).toContain('还没有报告,先上传第一份吧。')
  })

  it('ReportView 终态渲染封面带与解读四段', async () => {
    vi.spyOn(reportsApi, 'getReport').mockResolvedValue({
      meta: { id: 'r1', source: 'pdf', institution: '平安健康体检中心', report_date: '2026-08-28', sex: '男', age: 35 },
      items: [],
      normalized: [{ item_name: '总胆固醇', indicator_code: 'TC', value_num: 6.31, unit: 'mmol/L', status: 'high', ref_low: 2.8, ref_high: 5.2, critical: false, section: null }],
      task: { id: 't1', status: 'completed', stage: null, error: null },
    })
    vi.spyOn(reportsApi, 'getInterpretation').mockResolvedValue({
      summary: '总体结论文本',
      items: [{ indicator_code: 'TC', name: '总胆固醇', status: 'high', value_text: '6.31 mmol/L(参考区间 2.8~5.2)', meaning: '高于参考区间', risks: [], advice_level: 'recheck', advice: '建议低脂饮食', evidence_ids: [] }],
      advice_summary: '【定期复查】总胆固醇: 建议低脂饮食',
      disclaimer: '【免责声明】本解读仅供健康参考。',
      degraded: false,
    })
    vi.spyOn(reportsApi, 'getFollowupPlan').mockResolvedValue({
      items: [{ item: '复查总胆固醇', timeframe: '3 个月后', department: '心内科', basis: '偏高' }],
      degraded: false,
    })
    const w = mount(ReportView, globalOpts)
    await flushPromises()
    expect(w.text()).toContain('平安健康体检中心')
    expect(w.text()).toContain('总体结论')
    expect(w.text()).toContain('高于参考区间')
  })

  it('ReportView awaiting_meta 显示补录表单', async () => {
    vi.spyOn(reportsApi, 'getReport').mockResolvedValue({
      meta: { id: 'r1', source: 'pdf', institution: null, report_date: null, sex: null, age: null },
      items: [], normalized: [],
      task: { id: 't1', status: 'awaiting_meta', stage: 'normalize', error: null },
    })
    vi.spyOn(tasksApi, 'getTask').mockResolvedValue({
      task_id: 't1', report_id: 'r1', status: 'awaiting_meta', stage: 'normalize', timings: {}, completed_stages: [], error: null,
    })
    const w = mount(ReportView, globalOpts)
    await flushPromises()
    expect(w.text()).toContain('保存并继续解读')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && pnpm test tests/views.test.ts`
Expected: FAIL(找不到视图)

- [ ] **Step 3: 写 HomeView**

```vue
<!-- src/views/HomeView.vue —— 首页(spec-f §5.1/§6.6) -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import UploadZone from '../components/UploadZone.vue'
import ManualEntryForm from '../components/ManualEntryForm.vue'
import RangeStrip from '../components/RangeStrip.vue'
import { loadRecent, removeRecent, upsertRecent } from '../utils/recentReports'
import type { RecentReport } from '../utils/recentReports'

const router = useRouter()
const recent = ref<RecentReport[]>([])
const activeTab = ref('file')

onMounted(() => { recent.value = loadRecent() })

function onUploaded(e: { reportId: string; taskId: string; source: 'pdf' | 'photo' | 'manual' }) {
  upsertRecent(localStorage, {
    id: e.reportId, created_at: new Date().toISOString(),
    source: e.source, institution: null, report_date: null, status: null,
  })
  router.push(`/report/${e.reportId}`)
}

function onRemove(id: string) {
  recent.value = removeRecent(localStorage, id)
}

const STATUS_TEXT: Record<string, string> = {
  completed: '已解读', degraded: '已解读(降级)', failed: '解读失败',
  awaiting_meta: '待补充信息', running: '解读中', pending: '排队中',
}
</script>

<template>
  <main class="page-col home">
    <section class="thesis">
      <h1 class="thesis-title">看懂你的体检报告</h1>
      <p class="thesis-sub">上传 PDF 或照片,得到逐项解读与复查建议,还能继续追问</p>
      <div class="example">
        <span class="example-label">示例:总胆固醇 6.31 ↑ 参考 2.8–5.2</span>
        <RangeStrip :value="6.31" :low="2.8" :high="5.2" status="high" :critical="false" />
      </div>
    </section>

    <section class="upload-box">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="上传文件" name="file"><UploadZone @uploaded="onUploaded" /></el-tab-pane>
        <el-tab-pane label="手动录入" name="manual"><ManualEntryForm @uploaded="onUploaded" /></el-tab-pane>
      </el-tabs>
    </section>

    <section class="recent">
      <h2 class="recent-title">最近报告</h2>
      <p v-if="recent.length === 0" class="recent-empty">还没有报告,先上传第一份吧。</p>
      <div v-for="r in recent" :key="r.id" class="recent-row hairline-top">
        <span class="recent-main" @click="router.push(`/report/${r.id}`)">
          <span class="recent-name">{{ r.institution || '手动录入' }}</span>
          <span class="recent-date">{{ r.created_at.slice(0, 10) }}</span>
          <span class="recent-status" :class="`st-${r.status ?? 'pending'}`">
            {{ STATUS_TEXT[r.status ?? 'pending'] }}
          </span>
        </span>
        <el-button text size="small" @click="onRemove(r.id)">删除</el-button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.thesis { margin: var(--space-48) 0; }
.thesis-title { font-family: var(--font-display); font-size: 28px; font-weight: 600; margin: 0 0 var(--space-8); }
.thesis-sub { opacity: 0.7; margin: 0 0 var(--space-16); }
.example { display: flex; align-items: center; gap: var(--space-16); }
.example-label { font-size: 13px; opacity: 0.6; }
.recent { margin-top: var(--space-48); }
.recent-title { font-size: 15px; font-weight: 600; }
.recent-empty { opacity: 0.6; }
.recent-row { display: flex; justify-content: space-between; align-items: center; padding: var(--space-8) 0; }
.recent-main { display: flex; gap: var(--space-16); align-items: baseline; cursor: pointer; }
.recent-status { font-size: 12px; }
.st-completed { color: var(--c-clinical); }
.st-degraded, .st-awaiting_meta, .st-running, .st-pending { color: var(--c-amber); }
.st-failed { color: var(--c-brick); }
</style>
```

- [ ] **Step 4: 写 ReportView**

```vue
<!-- src/views/ReportView.vue —— 报告详情(spec-f §5.1/§6.5) -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getFollowupPlan, getInterpretation, getReport } from '../api/reports'
import { getTask } from '../api/tasks'
import type { FollowupPlanDoc, InterpretationDoc, ReportDetail } from '../api/types'
import { createTaskPoller } from '../composables/useTaskPolling'
import { upsertRecent } from '../utils/recentReports'
import TaskProgress from '../components/TaskProgress.vue'
import MetaPatchForm from '../components/MetaPatchForm.vue'
import SectionSummary from '../components/SectionSummary.vue'
import SectionItems from '../components/SectionItems.vue'
import SectionAdvice from '../components/SectionAdvice.vue'
import SectionDisclaimer from '../components/SectionDisclaimer.vue'
import FollowupList from '../components/FollowupList.vue'
import ChatPanel from '../components/ChatPanel.vue'

const route = useRoute()
const reportId = route.params.id as string

const detail = ref<ReportDetail | null>(null)
const taskInfo = ref<Awaited<ReturnType<typeof getTask>> | null>(null)
const interp = ref<InterpretationDoc | null>(null)
const followup = ref<FollowupPlanDoc | null>(null)
const docError = ref<string | null>(null)
const activeTab = ref('interp')

const terminal = computed(() =>
  detail.value?.task && ['completed', 'degraded', 'failed'].includes(detail.value.task.status),
)

const poller = createTaskPoller(getTask)

function updateRecent() {
  if (!detail.value) return
  upsertRecent(localStorage, {
    id: reportId, created_at: new Date().toISOString(),
    source: detail.value.meta.source, institution: detail.value.meta.institution,
    report_date: detail.value.meta.report_date,
    status: detail.value.task?.status ?? null,
  })
}

/** 404 = 尚未生成:重试一次后给 §7 文案(spec-f §5.1) */
async function fetchDoc<T>(fn: () => Promise<T>): Promise<T | null> {
  for (let i = 0; i < 2; i++) {
    try {
      return await fn()
    } catch (e) {
      if ((e as { response?: { status?: number } }).response?.status === 404) {
        await new Promise(r => setTimeout(r, 1000))
        continue
      }
      throw e
    }
  }
  docError.value = '解读尚未生成,稍后刷新页面再试。'
  return null
}

async function loadDocs() {
  interp.value = await fetchDoc(() => getInterpretation(reportId))
  followup.value = await fetchDoc(() => getFollowupPlan(reportId))
}

async function startPolling() {
  if (!detail.value?.task) return
  poller.start(detail.value.task.id, {
    onUpdate: async info => { taskInfo.value = info; detail.value = await getReport(reportId) },
    onAwaitingMeta: async info => { taskInfo.value = info; detail.value = await getReport(reportId) },
    onDone: async () => { detail.value = await getReport(reportId); updateRecent(); await loadDocs() },
    onFailed: async info => { taskInfo.value = info; detail.value = await getReport(reportId) },
  })
}

onMounted(async () => {
  detail.value = await getReport(reportId)
  updateRecent()
  if (detail.value.task && !terminal.value) {
    await startPolling()
  } else {
    await loadDocs()
  }
})

onBeforeUnmount(() => poller.stop())
</script>

<template>
  <main v-if="detail" class="page-col report">
    <header class="cover">
      <h1 class="cover-title">体检报告</h1>
      <div class="cover-meta">
        {{ detail.meta.sex ?? '—' }} · {{ detail.meta.age ?? '—' }} 岁
        · {{ detail.meta.institution || '未知机构' }} · {{ detail.meta.report_date || '未知日期' }}
      </div>
      <div class="cover-badges">
        <span v-if="detail.task?.status === 'degraded'" class="badge b-amber">部分能力降级</span>
        <span v-if="detail.task?.status === 'failed'" class="badge b-brick">解读失败</span>
      </div>
    </header>

    <section v-if="detail.task?.status === 'failed'" class="fail-block">
      <p>这份文件没能解读出来。换一张更清晰的照片,或改用手动录入。</p>
      <p v-if="detail.task.error" class="fail-detail">{{ detail.task.error }}</p>
      <el-button type="primary" @click="$router.push('/')">回首页重新上传</el-button>
    </section>

    <section v-else-if="!terminal && taskInfo" class="progress-block">
      <TaskProgress :info="taskInfo" />
      <MetaPatchForm v-if="taskInfo.status === 'awaiting_meta'" :report-id="reportId" @saved="startPolling" />
    </section>

    <section v-else class="content-block">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="解读" name="interp">
          <div v-if="docError" class="doc-error">{{ docError }}</div>
          <template v-if="interp">
            <div v-if="interp.degraded" class="degraded-note">本次解读由备用模板生成,内容为降级结果</div>
            <SectionSummary :text="interp.summary" />
            <SectionItems :doc="interp" :normalized="detail.normalized" />
            <SectionAdvice :text="interp.advice_summary" />
            <SectionDisclaimer :text="interp.disclaimer" />
          </template>
        </el-tab-pane>
        <el-tab-pane label="复查计划" name="followup">
          <FollowupList v-if="followup" :doc="followup" />
        </el-tab-pane>
        <el-tab-pane label="追问" name="chat">
          <ChatPanel :report-id="reportId" />
        </el-tab-pane>
      </el-tabs>
    </section>
  </main>
</template>

<style scoped>
.cover {
  background: var(--c-cover);
  color: var(--c-paper);
  padding: var(--space-48) var(--space-16);
  margin: 0 calc(-1 * var(--space-16));
}
.cover-title { font-family: var(--font-display); font-size: 24px; font-weight: 600; margin: 0 0 var(--space-8); }
.cover-meta { opacity: 0.8; }
.cover-badges { margin-top: var(--space-8); display: flex; gap: var(--space-8); }
.badge { font-size: 12px; padding: 0 6px; border-radius: 4px; color: #fff; }
.b-amber { background: var(--c-amber); }
.b-brick { background: var(--c-brick); }
.progress-block { margin-top: var(--space-48); }
.fail-block { margin-top: var(--space-48); }
.fail-detail { font-size: 13px; opacity: 0.6; }
.doc-error, .degraded-note { padding: var(--space-8) var(--space-16); border-radius: 4px; background: var(--c-mist); margin-bottom: var(--space-16); }
.content-block { margin-top: var(--space-48); }
</style>
```

- [ ] **Step 5: 写 NotFoundView 并接线路由**

```vue
<!-- src/views/NotFoundView.vue -->
<script setup lang="ts">
</script>

<template>
  <main class="page-col notfound">
    <h1>没有这个页面</h1>
    <el-button type="primary" @click="$router.push('/')">回首页</el-button>
  </main>
</template>

<style scoped>
.notfound { padding-top: var(--space-48); }
</style>
```

```ts
// src/router/index.ts(替换原文件内容)
import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('../views/HomeView.vue') },
    { path: '/report/:id', name: 'report', component: () => import('../views/ReportView.vue') },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFoundView.vue') },
  ],
})
```

- [ ] **Step 6: 运行测试确认通过 + 类型检查 + 构建**

Run: `cd frontend && pnpm test && pnpm run typecheck && pnpm run build`
Expected: 全部 PASS(累计 50+ test);typecheck 无错误;build 成功

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views frontend/src/router frontend/tests/views.test.ts
git commit -m "feat: assemble home and report views with routing (Task 15)"
```

---

### Task 16: 验收检查

**Files:**
- Create: `frontend/README.md`(启动说明)
- Modify: `frontend/package.json`(无;如需说明放 README)

**Interfaces:**
- Consumes: Task 15 完整应用
- Produces: 验收通过的记录(spec-f §10)

- [ ] **Step 1: 写 frontend/README.md**

```markdown
# frontend 体检报告解读助手界面

依赖后端 `http://localhost:8000` 已启动(见 ../backend 与根 README)。

```bash
pnpm install
pnpm run dev      # http://localhost:5173,/api 代理到后端
pnpm test         # Vitest
pnpm run build    # typecheck + 产物 dist/
```

API 代理目标可用环境变量覆盖:`VITE_API_TARGET=http://other:8000 pnpm run dev`。
设计约束见 `../docs/superpowers/specs/2026-09-03-report-agent-frontend-design.md`。
```

- [ ] **Step 2: 全量验证命令**

Run: `cd frontend && pnpm test && pnpm run build`
Expected: 全部 PASS;build 成功

- [ ] **Step 3: 手工验收清单(spec-f §10,需后端 + 基础设施)**

启动后端(`docker compose up -d` + `uv run uvicorn ...`)后逐项核对:

- [ ] 上传 PDF → 跳详情 → 进度带展示真实阶段 → completed → 四段解读 + 复查计划渲染,数值/区间带/颜色与 normalized 一致
- [ ] 上传照片(图片)→ 同链路可用
- [ ] 手动录入 JSON → 同链路可用
- [ ] 性别/年龄缺失 → awaiting_meta → 补录表单 → "保存并继续解读" → 管线恢复
- [ ] 任务 failed → 错误态与引导文案
- [ ] degraded(任务或解读)→ 降级提示条
- [ ] 追问:建会话 → 流式打字 → tool_call 状态行 → evidence 引用条 → done;`safety` 事件整条替换;断连后 history 同步;刷新恢复历史
- [ ] 最近报告:上传后出现、状态更新、删除仅清本地
- [ ] 解读四段与安全话术逐字等于后端返回(渲染 markdown 后文本一致)
- [ ] 键盘焦点可见;`prefers-reduced-motion` 下无脉冲动效

- [ ] **Step 4: Commit**

```bash
git add frontend/README.md
git commit -m "docs: frontend readme with acceptance checklist (Task 16)"
```

---

## 执行顺序与依赖

```
1 脚手架 → 2 API → 3 SSE 解析 → 4 区间带/严重度 → 5 最近报告 → 6 聊天 reducer
→ 7 MarkdownBlock → 8 RangeStrip → 9 UploadZone → 10 ManualEntryForm
→ 11 轮询/进度/补录 → 12 逐项解读 → 13 FollowupList → 14 ChatPanel → 15 组装 → 16 验收
```

纯逻辑任务(3/4/5/6)相互独立,可并行;组件任务(7–14)依赖对应纯逻辑;15 依赖全部;16 收尾。
