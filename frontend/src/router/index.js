import { createRouter, createWebHistory } from "vue-router";
import Characters from "../views/Characters.vue";
import Series from "../views/Series.vue";
import SeriesDetail from "../views/SeriesDetail.vue";
import StudioCraft from "../views/StudioCraft.vue";
import CreateVideo from "../views/CreateVideo.vue";
import TaskProgress from "../views/TaskProgress.vue";
import VideoPreview from "../views/VideoPreview.vue";
import History from "../views/History.vue";
import Settings from "../views/Settings.vue";
import ComfyUISettings from "../views/ComfyUISettings.vue";

const routes = [
  {
    path: "/studio-craft",
    component: StudioCraft,
    meta: { title: "短剧导演工坊", subtitle: "小说拆剧、审稿改稿与场景道具母版设计" },
  },
  {
    path: "/characters",
    component: Characters,
    meta: { title: "角色资产", subtitle: "统一管理三视图定妆照、四格板与配音音色" },
  },
  {
    path: "/series",
    component: Series,
    meta: { title: "连载短剧", subtitle: "多集大纲排期与跨集连续性管理" },
  },
  {
    path: "/series/:id",
    component: SeriesDetail,
    meta: { title: "连载工作台", subtitle: "分集编排、单集审片与整季连载状态追踪" },
  },
  {
    path: "/",
    name: "CreateVideo",
    component: CreateVideo,
    meta: { title: "创建视频任务", subtitle: "一句话或剧本直拍单集短视频" },
  },
  {
    path: "/progress/:id",
    name: "TaskProgress",
    component: TaskProgress,
    meta: { title: "生成进度", subtitle: "12 步全流程状态监控、开拍预审与单镜重试" },
  },
  {
    path: "/preview/:id",
    name: "VideoPreview",
    component: VideoPreview,
    meta: { title: "视频预览", subtitle: "成片回放、分镜定位精修与 MP4/SRT 导出" },
  },
  {
    path: "/history",
    name: "History",
    component: History,
    meta: { title: "历史任务列表", subtitle: "追踪生成进度、预审与历史成片" },
  },
  {
    path: "/settings",
    name: "Settings",
    component: Settings,
    meta: { title: "API Key 配置", subtitle: "实时配置大模型接口、生图/视频通道与额度上限" },
  },
  {
    path: "/comfyui",
    name: "ComfyUISettings",
    component: ComfyUISettings,
    meta: { title: "ComfyUI Settings", subtitle: "本地 ComfyUI 服务地址与工作流模板设置" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.afterEach((to) => {
  const pageTitle = to.meta?.title
    ? `${to.meta.title} · VideoAgent`
    : "VideoAgent · AI 短剧创作工作台";
  document.title = pageTitle;
});

export default router;
