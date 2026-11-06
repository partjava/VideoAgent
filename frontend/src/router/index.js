import { createRouter, createWebHistory } from "vue-router";
import Characters from "../views/Characters.vue";
import Series from "../views/Series.vue";
import SeriesDetail from "../views/SeriesDetail.vue";
import CreateVideo from "../views/CreateVideo.vue";
import TaskProgress from "../views/TaskProgress.vue";
import VideoPreview from "../views/VideoPreview.vue";
import History from "../views/History.vue";
import Settings from "../views/Settings.vue";
import ComfyUISettings from "../views/ComfyUISettings.vue";

const routes = [
  { path: "/characters", component: Characters, meta: { title: "角色资产" } },
  { path: "/series", component: Series, meta: { title: "连载短剧" } },
  {
    path: "/series/:id",
    component: SeriesDetail,
    meta: { title: "连载工作台" },
  },
  {
    path: "/",
    name: "CreateVideo",
    component: CreateVideo,
    meta: { title: "创建视频任务" },
  },
  {
    path: "/progress/:id",
    name: "TaskProgress",
    component: TaskProgress,
    meta: { title: "生成进度" },
  },
  {
    path: "/preview/:id",
    name: "VideoPreview",
    component: VideoPreview,
    meta: { title: "视频预览" },
  },
  {
    path: "/history",
    name: "History",
    component: History,
    meta: { title: "历史任务列表" },
  },
  {
    path: "/settings",
    name: "Settings",
    component: Settings,
    meta: { title: "API Key 配置" },
  },
  {
    path: "/comfyui",
    name: "ComfyUISettings",
    component: ComfyUISettings,
    meta: { title: "ComfyUI Settings" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
