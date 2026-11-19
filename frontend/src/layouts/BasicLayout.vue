<template>
  <el-container class="layout-container">
    <a href="#main-content" class="skip-link visually-hidden">跳到主内容</a>

    <!-- Left Studio Rail -->
    <el-aside width="248px" class="aside-menu">
      <div class="logo-area" @click="router.push('/')">
        <span class="logo-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
            <rect x="2" y="4" width="20" height="16" rx="4" stroke="currentColor" stroke-width="1.8" />
            <path d="M10 9l5 3-5 3V9z" fill="currentColor" />
          </svg>
        </span>
        <div class="logo-copy">
          <span class="logo-title">VideoAgent</span>
          <span class="logo-sub">AI 短剧制片工作台</span>
        </div>
      </div>

      <nav class="aside-nav va-scroll" aria-label="主导航">
        <el-menu
          :default-active="activeMenu"
          class="el-menu-vertical"
          router
          background-color="transparent"
          text-color="#cbd5e1"
          active-text-color="#ffffff"
        >
          <li class="nav-group-label" role="presentation">创作生产</li>
          <el-menu-item index="/studio-craft" aria-label="短剧导演工坊">
            <el-icon><MagicStick /></el-icon>
            <span>短剧导演工坊</span>
          </el-menu-item>
          <el-menu-item index="/" aria-label="创建视频">
            <el-icon><Plus /></el-icon>
            <span>创建视频</span>
          </el-menu-item>
          <el-menu-item index="/series" aria-label="连载短剧">
            <el-icon><VideoPlay /></el-icon>
            <span>连载短剧</span>
          </el-menu-item>

          <li class="nav-group-label" role="presentation">资产与交付</li>
          <el-menu-item index="/characters" aria-label="角色资产">
            <el-icon><User /></el-icon>
            <span>角色资产</span>
          </el-menu-item>
          <el-menu-item index="/history" aria-label="历史任务">
            <el-icon><Collection /></el-icon>
            <span>历史任务</span>
          </el-menu-item>

          <li class="nav-group-label" role="presentation">系统配置</li>
          <el-menu-item index="/settings" aria-label="API 配置">
            <el-icon><Setting /></el-icon>
            <span>API 配置</span>
          </el-menu-item>
        </el-menu>
      </nav>

      <div class="aside-footer">
        <span class="pipeline-pill">
          <span class="pipeline-dot" aria-hidden="true"></span>
          拆剧 → 定角 → 拍片 → 交付
        </span>
      </div>
    </el-aside>

    <el-container class="main-container">
      <!-- Top Frosted Glass Header -->
      <el-header class="top-header">
        <div class="header-left">
          <span class="page-title">{{ currentTitle }}</span>
          <span v-if="currentSubtitle" class="page-subtitle-sep" aria-hidden="true">·</span>
          <span v-if="currentSubtitle" class="page-subtitle">{{ currentSubtitle }}</span>
        </div>
        <div class="header-actions">
          <RouterLink v-if="route.path !== '/'" to="/" class="header-quick-btn primary">
            + 新建单集
          </RouterLink>
          <RouterLink v-if="route.path !== '/settings'" to="/settings" class="header-quick-btn">
            API 配置
          </RouterLink>
        </div>
      </el-header>

      <!-- Main Workspace Area -->
      <el-main class="main-content va-scroll">
        <div id="main-content" tabindex="-1" class="content-wrapper">
          <slot></slot>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

const activeMenu = computed(() => {
  return route.path.startsWith("/series") ? "/series" : route.path;
});

const currentTitle = computed(() => {
  return route.meta.title || "视频生成控制台";
});

const currentSubtitle = computed(() => {
  return route.meta.subtitle || "";
});
</script>

<style scoped>
.layout-container {
  block-size: 100vh;
  block-size: 100dvh;
  inline-size: 100%;
  background-color: var(--va-bg);
}

.aside-menu {
  background: linear-gradient(180deg, #0d1526 0%, #111c33 100%);
  border-inline-end: 1px solid rgba(148, 163, 184, 0.14);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  color: #e2e8f0;
}

.logo-area {
  min-block-size: 68px;
  display: flex;
  align-items: center;
  padding: 12px 18px;
  gap: 12px;
  border-block-end: 1px solid rgba(148, 163, 184, 0.12);
  cursor: pointer;
}

.logo-mark {
  display: grid;
  place-items: center;
  inline-size: 36px;
  block-size: 36px;
  border-radius: 10px;
  flex-shrink: 0;
  color: #a3e635;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.35), rgba(163, 230, 53, 0.16));
  border: 1px solid rgba(163, 230, 53, 0.32);
}

.logo-copy {
  display: flex;
  flex-direction: column;
  min-inline-size: 0;
}

.logo-title {
  font-size: 1.08rem;
  font-weight: 800;
  color: #f8fafc;
  letter-spacing: -0.02em;
  line-height: 1.2;
}

.logo-sub {
  font-size: 0.7rem;
  color: #94a3b8;
  margin-top: 2px;
}

.aside-nav {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 12px 10px;
}

.el-menu-vertical {
  border-inline-end: none;
  background-color: transparent !important;
}

.nav-group-label {
  list-style: none;
  padding: 12px 12px 6px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #94a3b8;
}

.nav-group-label:first-child {
  padding-top: 4px;
}

.el-menu-item {
  position: relative;
  border-radius: 10px;
  margin-bottom: 3px;
  min-block-size: 44px;
  height: auto;
  line-height: 1.4;
  padding-inline: 14px !important;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.el-menu-item:hover {
  background-color: rgba(255, 255, 255, 0.07) !important;
  color: #ffffff !important;
}

.el-menu-item.is-active {
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.34), rgba(37, 99, 235, 0.14)) !important;
  color: #ffffff !important;
  font-weight: 700;
}

.el-menu-item.is-active::before {
  content: "";
  position: absolute;
  inset-inline-start: 0;
  inset-block: 9px;
  inline-size: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--va-studio-accent);
}

.aside-footer {
  padding: 12px 14px 16px;
  border-block-start: 1px solid rgba(148, 163, 184, 0.12);
}

.pipeline-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(148, 163, 184, 0.14);
  font-size: 0.72rem;
  color: #cbd5e1;
}

.pipeline-dot {
  inline-size: 7px;
  block-size: 7px;
  border-radius: 50%;
  background: var(--va-studio-accent);
  flex-shrink: 0;
}

.main-container {
  display: flex;
  flex-direction: column;
  min-inline-size: 0;
  overflow: hidden;
}

.top-header {
  background-color: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(12px);
  border-block-end: 1px solid var(--va-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 28px;
  min-block-size: 62px;
  height: auto;
  box-shadow: var(--va-shadow-xs);
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-inline-size: 0;
  flex-wrap: wrap;
}

.page-title {
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--va-ink);
}

.page-subtitle-sep {
  color: var(--va-border-strong);
}

.page-subtitle {
  font-size: 0.82rem;
  color: var(--va-ink-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.header-quick-btn {
  display: inline-flex;
  align-items: center;
  min-block-size: 32px;
  padding: 5px 12px;
  border-radius: 8px;
  border: 1px solid var(--va-border);
  background: #ffffff;
  color: var(--va-ink-secondary);
  font-size: 0.8rem;
  font-weight: 600;
  text-decoration: none;
  transition: border-color 0.15s ease, color 0.15s ease, background-color 0.15s ease;
}

.header-quick-btn:hover {
  border-color: var(--va-brand-border);
  color: var(--va-brand);
  background: var(--va-brand-soft);
}

.header-quick-btn.primary {
  border-color: var(--va-brand);
  background: var(--va-brand);
  color: #ffffff;
}

.header-quick-btn.primary:hover {
  background: var(--va-brand-hover);
  border-color: var(--va-brand-hover);
  color: #ffffff;
}

.main-content {
  flex: 1 1 auto;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 26px 28px;
  background-color: var(--va-bg);
}

.content-wrapper {
  max-inline-size: 100%;
  margin: 0;
}

.content-wrapper:focus {
  outline: none;
}

@media (max-width: 760px) {
  .aside-menu {
    inline-size: 64px !important;
  }

  .logo-area {
    padding: 12px 0;
    justify-content: center;
  }

  .logo-copy,
  .nav-group-label,
  .aside-footer,
  .el-menu-item span,
  .page-subtitle-sep,
  .page-subtitle {
    display: none;
  }

  .aside-nav {
    padding: 10px 6px;
  }

  .el-menu-item {
    padding: 0 !important;
    justify-content: center;
  }

  .el-menu-item :deep(.el-icon) {
    margin-inline-end: 0;
  }

  .top-header {
    padding: 8px 14px;
  }

  .main-content {
    padding: 14px;
  }
}
</style>
