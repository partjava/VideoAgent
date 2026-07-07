import { createRouter, createWebHistory } from 'vue-router'
import CreateVideo from '../views/CreateVideo.vue'
import TaskProgress from '../views/TaskProgress.vue'
import VideoPreview from '../views/VideoPreview.vue'
import History from '../views/History.vue'
import Settings from '../views/Settings.vue'

const routes = [
  {
    path: '/',
    name: 'CreateVideo',
    component: CreateVideo,
    meta: { title: '创建视频任务' }
  },
  {
    path: '/progress/:id',
    name: 'TaskProgress',
    component: TaskProgress,
    meta: { title: '生成进度' }
  },
  {
    path: '/preview/:id',
    name: 'VideoPreview',
    component: VideoPreview,
    meta: { title: '视频预览' }
  },
  {
    path: '/history',
    name: 'History',
    component: History,
    meta: { title: '历史任务列表' }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: Settings,
    meta: { title: 'API Key 配置' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
