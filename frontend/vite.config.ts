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
