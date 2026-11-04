<template>
  <BasicLayout>
    <div class="create-video-page">
      <div class="header-intro">
        <span class="studio-label">VIDEO STUDIO / NEW PROJECT</span>
        <h1 class="page-main-title">一句话，开始拍片</h1>
        <p class="page-subtitle">
          描述一个故事、科普主题或产品创意，接下来可以在工作台查看每一步。

        </p>
      </div>

      <div class="creation-flow" aria-label="制作流程">
        <span>01 创意与脚本</span><span>02 分镜与素材</span><span>03 剪辑与导出</span>
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
.create-video-page { max-width: 1050px; margin: auto; }
.header-intro { background: radial-gradient(ellipse at 94% 10%, #38506a 0%, transparent 55%), linear-gradient(125deg, #101b30, #20304b); padding: clamp(23px, 4vw, 46px); border-radius: 23px; }
.studio-label { color: #baf873; font-size: 11px; font-weight: 750; letter-spacing: .16em; }
.header-intro .page-main-title { color: #f6f8ff; font-size: clamp(26px, 4vw, 44px); margin: 9px 0 8px; }
.header-intro .page-subtitle { color: #c2d0e4; line-height: 1.65; }
.creation-flow { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.creation-flow span { padding: 7px 12px; background: #eaf1fc; color: #324c72; border-radius: 7px; font-size: 12px; font-weight: 650; }
</style>
