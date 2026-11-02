import { createRouter, createWebHistory } from 'vue-router';
import WorkbenchPage from '@/features/workbench/WorkbenchPage.vue';

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workbench', component: WorkbenchPage },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});
