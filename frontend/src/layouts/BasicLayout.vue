<template>
  <el-container class="layout-container">
    <!-- Left Sidebar -->
    <el-aside width="240px" class="aside-menu">
      <div class="logo-area">
        <el-icon size="26" color="#4f46e5"><VideoPlay /></el-icon>
        <span class="logo-title">VideoAgent</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        class="el-menu-vertical"
        router
        background-color="#f8fafc"
        text-color="#475569"
        active-text-color="#4f46e5"
      >
        <el-menu-item index="/">
          <el-icon><Plus /></el-icon>
          <span>创建视频</span>
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><Collection /></el-icon>
          <span>历史任务</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>API 配置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container class="main-container">
      <!-- Top Header -->
      <el-header class="top-header">
        <div class="header-left">
          <span class="page-title">{{ currentTitle }}</span>
        </div>
        <div class="header-right">
          <el-tag type="success" effect="plain" class="tech-tag">Real AI Mode</el-tag>
        </div>
      </el-header>

      <!-- Main Content Area -->
      <el-main class="main-content">
        <div class="content-wrapper">
          <slot></slot>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const activeMenu = computed(() => {
  return route.path
})

const currentTitle = computed(() => {
  return route.meta.title || '视频生成控制台'
})
</script>

<style scoped>
.layout-container {
  height: 100vh;
  width: 100vw;
  background-color: #f1f5f9;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.aside-menu {
  background-color: #f8fafc;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
}

.logo-area {
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 12px;
  border-bottom: 1px solid #e2e8f0;
}

.logo-title {
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: 0.5px;
}

.el-menu-vertical {
  border-right: none;
  flex-grow: 1;
  padding: 16px 8px;
}

.el-menu-item {
  border-radius: 8px;
  margin-bottom: 4px;
  height: 48px;
  line-height: 48px;
}

.el-menu-item.is-active {
  background-color: #e0e7ff !important;
  font-weight: 600;
}

.main-container {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.top-header {
  background-color: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 32px;
  height: 64px;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}

.tech-tag {
  border-color: #cbd5e1;
  color: #64748b;
  font-weight: 500;
  border-radius: 4px;
}

.main-content {
  flex-grow: 1;
  overflow-y: auto;
  padding: 32px;
  background-color: #f8fafc;
}

.content-wrapper {
  max-width: 100%;
  margin: 0;
}
</style>
