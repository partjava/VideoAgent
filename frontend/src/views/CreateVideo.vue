<template>
  <BasicLayout>
    <div class="create-video-page">
      <div class="header-intro va-studio-hero">
        <span class="va-eyebrow">VIDEO STUDIO · NEW EPISODE</span>
        <h1 class="page-main-title">一句话，开始拍片</h1>
        <p class="page-subtitle">
          描述一个故事创意，或直接粘贴剧本与角色设定。系统将完成脚本审稿、分镜设计、角色定妆锚点绘制、配音生成与视频合成，并支持开拍前逐镜预审。
        </p>
        <div class="creation-flow" aria-label="制作流程">
          <span><b>01</b> 创意与脚本</span>
          <span><b>02</b> 分镜与首帧</span>
          <span><b>03</b> 配音、成片与质检</span>
        </div>
      </div>

      <div class="form-wrapper">
        <VideoCreateForm :loading="loading" @submit="handleCreateTask" />
      </div>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        closable
        @close="errorMessage = ''"
        class="error-alert"
      />
    </div>
  </BasicLayout>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import BasicLayout from '../layouts/BasicLayout.vue'
import VideoCreateForm from '../components/VideoCreateForm.vue'
import { videoApi } from '../api/video'

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')

const handleCreateTask = async (formData) => {
  loading.value = true
  errorMessage.value = ''

  try {
    const taskResult = await videoApi.createTask(formData)
    const taskId = taskResult.task_id

    await videoApi.runPipeline(taskId)

    router.push(`/progress/${taskId}`)
  } catch (error) {
    errorMessage.value = error.message || '创建任务失败，请检查后端服务连接。'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.create-video-page {
  max-inline-size: 1080px;
  margin-inline: auto;
  padding: 4px 0 24px;
}

.header-intro {
  margin-bottom: 22px;
}

.page-subtitle {
  max-inline-size: 68ch;
}

.creation-flow {
  display: flex;
  flex-wrap: wrap;
  flex-wrap: balance;
  gap: 10px;
  margin-top: 18px;
}

.creation-flow span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 13px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(148, 163, 184, 0.22);
  color: #e2e8f0;
  font-size: 0.78rem;
  font-weight: 600;
}

.creation-flow b {
  color: var(--va-studio-accent);
  font-variant-numeric: tabular-nums;
}

.form-wrapper {
  margin-bottom: 24px;
}

.error-alert {
  margin-top: 16px;
  border-radius: var(--va-radius-md);
}
</style>
