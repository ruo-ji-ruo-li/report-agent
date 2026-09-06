import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'node',
    restoreMocks: true,
    server: {
      deps: {
        // element-plus 必须整体内联转译:外部化(native ESM)时其依赖 async-validator
        // 无 "exports" 字段,node 按 main 解析到 CJS 包,`import AsyncValidator from
        // 'async-validator'` 拿到 module.exports 对象而非 Schema 类,`new AsyncValidator()`
        // 同步抛 "not a constructor",el-form validate() 因此空过(探针定位;单内联
        // async-validator 无效——EP 外部化后其内部 import 不经 vite 解析,须内联 EP 本体)
        inline: ['element-plus'],
      },
    },
  },
})
