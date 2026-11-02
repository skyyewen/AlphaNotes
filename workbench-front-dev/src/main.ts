import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import { router } from '@/shared/router';
import '@/shared/styles/theme.css';

createApp(App).use(createPinia()).use(router).mount('#app');
