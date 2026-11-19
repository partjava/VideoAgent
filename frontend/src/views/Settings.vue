<template>
  <BasicLayout>
    <div class="settings-page">
      <div class="va-workspace-header">
        <div>
          <span class="va-eyebrow">MODEL & QUOTA · 模型与额度配置</span>
          <h1>模型与生成额度配置</h1>
          <p>按用途分别绑定提示词模型、图片模型与视频生成模型，修改后实时写入数据库与本地缓存。</p>
        </div>
      </div>
      <ModelSettings />
      <LimitSettings />
      <details class="advanced-settings">
        <summary>高级设置（本地 ComfyUI 工作流）</summary>
        <p>本地生成可以连接 ComfyUI，配置图片和视频工作流。</p>
        <RouterLink to="/comfyui" class="comfy-link">配置 ComfyUI 本地工作流 →</RouterLink>
      </details>
    </div>
  </BasicLayout>
</template>

<script setup>
import BasicLayout from '../layouts/BasicLayout.vue'
import LimitSettings from '../components/LimitSettings.vue'
import ModelSettings from '../components/ModelSettings.vue'
</script>

<style scoped>
.settings-page {
  max-width: 1180px;
  margin: 0 auto;
}
.advanced-settings {
  margin-top: 22px;
  padding: 16px 20px;
  border: 1px solid var(--va-border);
  border-radius: var(--va-radius-lg);
  background: var(--va-surface);
  box-shadow: var(--va-shadow-xs);
}
summary {
  cursor: pointer;
  font-weight: 700;
  color: var(--va-ink);
}
p {
  color: var(--va-ink-muted);
  margin: 10px 0 8px;
}
.comfy-link {
  color: var(--va-brand);
  font-weight: 600;
  text-decoration: none;
}
.comfy-link:hover {
  text-decoration: underline;
}
</style>
