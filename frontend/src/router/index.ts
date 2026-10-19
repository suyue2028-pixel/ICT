import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: () => import('@/views/HomeView.vue'), meta: { title: '首页' } },
    {
      path: '/intelligence',
      component: () => import('@/views/IntelligenceListView.vue'),
      meta: { title: '情报中心' },
    },
    {
      path: '/intelligence/:id',
      component: () => import('@/views/IntelligenceDetailView.vue'),
      meta: { title: '情报详情' },
    },
    {
      path: '/graph',
      component: () => import('@/views/ClassificationView.vue'),
      meta: { title: '情报分类' },
    },
    { path: '/qa', component: () => import('@/views/QaView.vue'), meta: { title: '可追溯问答' } },
    { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { title: '登录', layout: 'auth' } },
    { path: '/tasks', redirect: '/' },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
