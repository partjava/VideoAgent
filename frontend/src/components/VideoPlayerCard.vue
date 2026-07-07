<template>
  <el-card class="player-card" shadow="never">
    <div class="player-container">
      <div v-if="videoUrl" class="video-wrapper">
        <video 
          ref="videoPlayer"
          :src="videoUrl" 
          controls 
          class="custom-video"
          preload="metadata"
        ></video>
      </div>
      <div v-else class="empty-video-state">
        <el-icon class="empty-icon"><VideoCamera /></el-icon>
        <p class="empty-text">视频文件准备中，或者正在生成封面...</p>
      </div>
    </div>
    
    <div class="video-info">
      <h3 class="video-title">{{ title || '未命名视频' }}</h3>
      <div class="metadata-grid">
        <div class="meta-item">
          <span class="meta-label">画幅比例</span>
          <span class="meta-value">{{ ratio || '9:16' }}</span>
        </div>
        <div class="meta-item" v-if="duration">
          <span class="meta-label">视频时长</span>
          <span class="meta-value">{{ duration }}秒</span>
        </div>
        <div class="meta-item" v-if="style">
          <span class="meta-label">视频风格</span>
          <span class="meta-value">{{ style }}</span>
        </div>
      </div>
      
      <div class="action-buttons" v-if="videoUrl">
        <el-button 
          type="primary" 
          icon="Download" 
          class="download-btn"
          @click="handleDownload"
        >
          下载最终视频 (MP4)
        </el-button>
        <el-button 
          v-if="subtitleUrl"
          type="default" 
          icon="Document" 
          class="subtitle-btn"
          @click="handleDownloadSubtitle"
        >
          下载字幕 (SRT)
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  videoPath: {
    type: String,
    default: ''
  },
  subtitlePath: {
    type: String,
    default: ''
  },
  title: {
    type: String,
    default: ''
  },
  ratio: {
    type: String,
    default: ''
  },
  duration: {
    type: Number,
    default: null
  },
  style: {
    type: String,
    default: ''
  }
})

// Convert backend path to Vite dev server local static URL path
// The backend saves path as: "backend/outputs/{task_id}/final.mp4"
// The vite server proxies requests to: "http://localhost:8000/api/..."
// Since backend is FastAPI, wait! Can we access files directly from static files?
// Let's check how files are served in FastAPI routes/main.py.
// Usually backend maps assets or static directory using StaticFiles.
// Let's check backend/main.py or routes/video_routes.py if it has mount for assets / outputs.
// Yes, we will check that!
// For now, let's parse the path to a relative url path.
const videoUrl = computed(() => {
  if (!props.videoPath) return ''
  // Strip "backend/" from path
  const relativePath = props.videoPath.replace('backend/', '')
  return `/${relativePath}`
})

const subtitleUrl = computed(() => {
  if (!props.subtitlePath) return ''
  const relativePath = props.subtitlePath.replace('backend/', '')
  return `/${relativePath}`
})

const handleDownload = () => {
  if (!videoUrl.value) return
  const link = document.createElement('a')
  link.href = videoUrl.value
  link.download = `video-${Date.now()}.mp4`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

const handleDownloadSubtitle = () => {
  if (!subtitleUrl.value) return
  const link = document.createElement('a')
  link.href = subtitleUrl.value
  link.download = `subtitle-${Date.now()}.srt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}
</script>

<style scoped>
.player-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

.player-container {
  background-color: #0f172a;
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  min-height: 380px;
}

.video-wrapper {
  width: 100%;
  max-width: 480px;
  max-height: 520px;
  display: flex;
  justify-content: center;
  align-items: center;
}

.custom-video {
  width: 100%;
  height: auto;
  max-height: 520px;
  border-radius: 4px;
}

.empty-video-state {
  text-align: center;
  color: #64748b;
  padding: 40px;
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
  color: #475569;
}

.empty-text {
  font-size: 14px;
}

.video-info {
  padding: 24px;
}

.video-title {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 16px 0;
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
  background-color: #f8fafc;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #f1f5f9;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-label {
  font-size: 12px;
  color: #64748b;
}

.meta-value {
  font-size: 14px;
  font-weight: 600;
  color: #334155;
}

.action-buttons {
  display: flex;
  gap: 12px;
}

.download-btn {
  flex: 1;
  height: 40px;
  background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
  border: none;
}

.download-btn:hover {
  background: linear-gradient(135deg, #4338ca 0%, #6d28d9 100%);
}

.subtitle-btn {
  height: 40px;
  border-color: #cbd5e1;
}
</style>
