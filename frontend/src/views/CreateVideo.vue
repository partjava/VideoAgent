<template>
  <BasicLayout>
    <div class="create-video-page">
      <div class="header-intro">
        <h1 class="page-main-title">生成 AI 短视频</h1>
        <p class="page-subtitle">
          输入一个剧情创意，系统会自动完成编剧、分镜、绘图、视频生成、配音、字幕和合成。
        </p>
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

    videoApi.runPipeline(taskId).catch(err => {
      console.warn('Pipeline trigger failed:', err)
    })

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
  padding: 16px 0;
}

.header-intro {
  margin-bottom: 32px;
  text-align: left;
}

.page-main-title {
  font-size: 26px;
  font-weight: 800;
  color: #0f172a;
  margin: 0 0 8px 0;
}

.page-subtitle {
  font-size: 14px;
  color: #64748b;
  margin: 0;
  line-height: 1.6;
  max-width: 800px;
}

.form-wrapper {
  margin-bottom: 24px;
}

.error-alert {
  margin-top: 16px;
  border-radius: 8px;
}
</style>
