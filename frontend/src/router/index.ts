import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: () => import('@/views/HomeView.vue'), meta: { title: '总览' } },
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
      component: () => import('@/views/GraphView.vue'),
      meta: { title: '知识图谱' },
    },
    { path: '/qa', component: () => import('@/views/QaView.vue'), meta: { title: '可追溯问答' } },
    {
      path: '/tasks',
      component: () => import('@/views/TasksView.vue'),
      meta: { title: '运行状态' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
