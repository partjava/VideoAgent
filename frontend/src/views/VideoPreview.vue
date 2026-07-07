<template>
  <BasicLayout>
    <div class="preview-page" v-loading="loading">
      <!-- Title header -->
      <div class="header-intro">
        <h1 class="page-main-title">生成结果控制台</h1>
        <p class="page-subtitle">您的短视频已合成完毕，可在线播放并下载。</p>
      </div>

      <!-- Task Failed Error State -->
      <div v-if="taskDetail && taskDetail.status === 'failed'" class="failed-state-box">
        <el-icon class="failed-icon"><WarningFilled /></el-icon>
        <div class="failed-content">
          <h3>任务生成失败</h3>
          <p>{{ taskDetail.metadata?.last_error || '未知错误，生成已被终止。' }}</p>
          <el-button type="primary" icon="Refresh" @click="handleRetry">返回重新创建</el-button>
        </div>
      </div>

      <!-- Video Not Generated Yet Empty State -->
      <div v-else-if="taskDetail && !taskDetail.metadata?.video_path" class="empty-state-box">
        <el-icon class="empty-icon"><VideoCameraFilled /></el-icon>
        <div class="empty-content">
          <h3>视频文件未就绪</h3>
          <p>该任务尚未完成渲染流程或尚未启动生成 pipeline。</p>
          <el-button type="primary" icon="CaretRight" @click="handleGoToProgress">查看生成进度</el-button>
        </div>
      </div>

      <!-- Main Preview Layout -->
      <div class="preview-grid" v-else-if="taskDetail">
        <!-- Left: Video Player region (aspect ratio 9:16 layout) -->
        <div class="player-wrapper">
          <VideoPlayerCard
            :video-path="taskDetail.metadata?.video_path"
            :subtitle-path="taskDetail.metadata?.subtitle_path"
            :title="taskDetail.topic || '未命名视频'"
            :ratio="taskDetail.ratio"
            :duration="taskDetail.duration"
            :style="taskDetail.style"
          />
        </div>

        <!-- Right: Detail Info & Script/Storyboard download actions -->
        <div class="detail-wrapper">
          <!-- Info Details Card -->
          <el-card class="detail-card info-card" shadow="never">
            <template #header>
              <div class="card-header">
                <span class="card-title">视频元数据</span>
              </div>
            </template>
            <div class="metadata-list">
              <div class="meta-row">
                <span class="meta-label">主题</span>
                <span class="meta-value">{{ taskDetail.topic || '未知' }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">生成模式</span>
                <span class="meta-value">{{ getModeText(taskDetail.generation_mode) }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">状态</span>
                <el-tag type="success" size="small" effect="dark">生成成功</el-tag>
              </div>
            </div>
          </el-card>

          <!-- Script & Storyboard text box -->
          <el-card class="detail-card content-card" shadow="never">
            <template #header>
              <div class="card-header">
                <span class="card-title">配音脚本预览</span>
              </div>
            </template>
            <div class="script-body">
              <p v-if="scriptData" class="script-text">{{ scriptData.content }}</p>
              <el-empty v-else description="无脚本内容" :image-size="60" />
            </div>
          </el-card>

          <!-- Download Action Card -->
          <el-card class="detail-card download-card" shadow="never">
            <template #header>
              <div class="card-header">
                <span class="card-title">导出与资产下载</span>
              </div>
            </template>
            <div class="downloads-grid">
              <el-button 
                type="primary" 
                icon="Download" 
                class="download-btn main-action-btn"
                @click="downloadVideo"
              >
                下载最终视频 (MP4)
              </el-button>
              
              <el-button 
                type="default" 
                icon="Document" 
                class="download-btn"
                :disabled="!taskDetail.metadata?.subtitle_path"
                @click="downloadSubtitle"
              >
                下载字幕文件 (SRT)
              </el-button>

              <el-button 
                type="default" 
                icon="Tickets" 
                class="download-btn"
                :disabled="!scriptData"
                @click="downloadScript"
              >
                下载文字脚本 (TXT)
              </el-button>

              <el-button 
                type="default" 
                icon="Film" 
                class="download-btn"
                :disabled="!storyboardData || storyboardData.length === 0"
                @click="downloadStoryboard"
              >
                下载分镜设计 (JSON)
              </el-button>
            </div>
          </el-card>
        </div>
      </div>
    </div>
  </BasicLayout>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BasicLayout from '../layouts/BasicLayout.vue'
import VideoPlayerCard from '../components/VideoPlayerCard.vue'
import { videoApi } from '../api/video'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

const loading = ref(true)
const taskDetail = ref(null)
const scriptData = ref(null)
const storyboardData = ref([])

const getModeText = (mode) => {
  const modes = {
    key_scenes: '关键镜头动态 (推荐)',
    full_dynamic: '全动态视频 (精美)'
  }
  return modes[mode] || mode
}

const handleRetry = () => {
  router.push('/')
}

const handleGoToProgress = () => {
  router.push(`/progress/${taskId}`)
}

// Download helpers
const downloadVideo = () => {
  if (!taskDetail.value?.metadata?.video_path) return
  const path = taskDetail.value.metadata.video_path.replace('backend/', '')
  const link = document.createElement('a')
  link.href = `/${path}`
  link.download = `${taskId}-final.mp4`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

const downloadSubtitle = () => {
  if (!taskDetail.value?.metadata?.subtitle_path) return
  const path = taskDetail.value.metadata.subtitle_path.replace('backend/', '')
  const link = document.createElement('a')
  link.href = `/${path}`
  link.download = `${taskId}-subtitle.srt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

const downloadScript = () => {
  if (!scriptData.value) return
  const content = `标题: ${scriptData.value.title || ''}\n\n口播脚本:\n${scriptData.value.content || ''}\n\n结尾总结:\n${scriptData.value.ending || ''}`
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${taskId}-script.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

const downloadStoryboard = () => {
  if (storyboardData.value.length === 0) return
  const content = JSON.stringify(storyboardData.value, null, 2)
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${taskId}-storyboard.json`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  try {
    // 1. Fetch task details
    const data = await videoApi.getTask(taskId)
    taskDetail.value = data
    
    if (data.status === 'success' && data.metadata?.video_path) {
      // 2. Fetch script content
      try {
        scriptData.value = await videoApi.getScript(taskId)
      } catch (e) {
        console.warn('Script details not found', e)
      }
      
      // 3. Fetch storyboard content
      try {
        storyboardData.value = await videoApi.getStoryboard(taskId)
      } catch (e) {
        console.warn('Storyboard details not found', e)
      }
    }
  } catch (error) {
    console.error('Failed to load video details', error)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.preview-page {
  padding: 16px 0;
}

.header-intro {
  margin-bottom: 24px;
  text-align: left;
}

.page-main-title {
  font-size: 26px;
  font-weight: 800;
  color: #0f172a;
  margin: 0 0 4px 0;
}

.page-subtitle {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

/* State boxes */
.failed-state-box, .empty-state-box {
  background-color: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 64px 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 600px;
  margin: 40px auto;
}

.failed-icon {
  font-size: 54px;
  color: #ef4444;
  margin-bottom: 16px;
}

.empty-icon {
  font-size: 54px;
  color: #64748b;
  margin-bottom: 16px;
}

.failed-content h3, .empty-content h3 {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 8px 0;
}

.failed-content p, .empty-content p {
  font-size: 14px;
  color: #64748b;
  margin: 0 0 24px 0;
}

/* Grid Layout */
.preview-grid {
  display: flex;
  gap: 32px;
  align-items: flex-start;
}

.player-wrapper {
  flex: 5;
  min-width: 320px;
}

.detail-wrapper {
  flex: 7;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.detail-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background-color: #ffffff;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #1e293b;
}

.metadata-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
}

.meta-label {
  color: #64748b;
}

.meta-value {
  font-weight: 600;
  color: #334155;
}

.script-body {
  max-height: 200px;
  overflow-y: auto;
}

.script-text {
  font-size: 14px;
  color: #475569;
  line-height: 1.6;
  margin: 0;
  white-space: pre-wrap;
  text-align: left;
}

.downloads-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

.download-btn {
  width: 100%;
  height: 40px;
  margin: 0 !important;
  font-weight: 500;
}

.main-action-btn {
  height: 46px;
  font-weight: 600;
  background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
  border: none;
  box-shadow: 0 4px 10px rgba(79, 70, 229, 0.2);
}

.main-action-btn:hover {
  background: linear-gradient(135deg, #4338ca 0%, #6d28d9 100%);
}
</style>
