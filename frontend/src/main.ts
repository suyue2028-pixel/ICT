import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElInput } from 'element-plus'
import 'element-plus/es/components/input/style/css'
import './assets/main.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.component('ElInput', ElInput)

app.mount('#app')
