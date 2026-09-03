// src/api/client.ts
import axios from 'axios'

// baseURL 走 Vite 代理(spec-f §2)
export const apiClient = axios.create({ baseURL: '/api', timeout: 60_000 })
