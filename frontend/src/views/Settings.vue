<template>
  <BasicLayout>
    <div class="settings-page">
      <h2>模型配置</h2>
      <ModelSettings />
      <details class="advanced-settings">
        <summary>高级设置</summary>
        <p>本地生成可以连接 ComfyUI，配置图片和视频工作流。</p>
        <RouterLink to="/comfyui">配置 ComfyUI 本地工作流</RouterLink>
      </details>
    </div>
  </BasicLayout>
</template>

<script setup>
import BasicLayout from '../layouts/BasicLayout.vue'
import ModelSettings from '../components/ModelSettings.vue'
</script>

<style scoped>
.settings-page { max-width:1100px; margin:0 auto; }
h2 { margin:0 0 20px; }
.advanced-settings { margin-top:24px; padding:16px; border:1px solid #e2e8f0; border-radius:12px; }
summary { cursor:pointer; }
p { color:#475569; }
</style>
