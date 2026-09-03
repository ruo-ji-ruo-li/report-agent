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
