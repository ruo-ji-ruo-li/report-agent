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
