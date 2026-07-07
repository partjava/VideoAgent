<template>
  <BasicLayout>
    <div class="history-page" v-loading="loading">
      <div class="header-intro">
        <h1 class="page-main-title">历史生成任务</h1>
        <p class="page-subtitle">管理所有已提交的视频生成任务，并按生成状态进行筛选。</p>
      </div>

      <!-- Filters & Actions Area -->
      <div class="filter-bar">
        <el-radio-group v-model="statusFilter" class="filter-radio-group">
          <el-radio-button label="all">全部</el-radio-button>
          <el-radio-button label="generating">生成中</el-radio-button>
          <el-radio-button label="success">成功</el-radio-button>
          <el-radio-button label="failed">失败</el-radio-button>
        </el-radio-group>
      </div>

      <!-- History Table Card -->
      <el-card class="table-card" shadow="never">
        <el-table :data="filteredTasksList" style="width: 100%" class="custom-table" empty-text="暂无匹配的任务">
          <el-table-column prop="task_id" label="任务 ID" width="160">
            <template #default="scope">
              <span class="mono-text">{{ scope.row.task_id }}</span>
            </template>
          </el-table-column>
          
          <el-table-column prop="topic" label="视频主题" width="180">
            <template #default="scope">
              <span class="topic-text">{{ scope.row.topic || scope.row.user_input.substring(0, 15) + '...' }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="generation_mode" label="生成模式" width="160">
            <template #default="scope">
              <span>{{ getModeText(scope.row.generation_mode) }}</span>
            </template>
          </el-table-column>
          
          <el-table-column prop="status" label="任务状态" width="140">
            <template #default="scope">
              <el-tag :type="getStatusTagType(scope.row.status)" effect="light">
                {{ getStatusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          
          <el-table-column prop="created_at" label="创建时间" width="180">
            <template #default="scope">
              <span>{{ formatDate(scope.row.created_at) }}</span>
            </template>
          </el-table-column>
          
          <el-table-column label="操作" min-width="220" fixed="right">
            <template #default="scope">
              <el-button 
                type="primary" 
                link 
                icon="View"
                @click="handleViewTask(scope.row)"
              >
                {{ scope.row.status === 'success' ? '预览' : '进度' }}
              </el-button>

              <el-button 
                type="warning" 
                link 
                icon="Refresh"
                @click="handleRegenerate(scope.row)"
              >
                重新生成
              </el-button>

              <el-button 
                type="danger" 
                link 
                icon="Delete"
                @click="handleDeleteTask(scope.row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </BasicLayout>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import BasicLayout from '../layouts/BasicLayout.vue'
import { videoApi } from '../api/video'

const router = useRouter()
const loading = ref(true)
const tasksList = ref([])
const statusFilter = ref('all')

const getModeText = (mode) => {
  const modes = {
    key_scenes: '关键镜头动态',
    full_dynamic: '全动态视频'
  }
  return modes[mode] || mode
}

const getStatusTagType = (status) => {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'created') return 'info'
  return 'primary'
}

const getStatusText = (status) => {
  const statusMap = {
    created: '已创建',
    planning: '规划中',
    script_done: '脚本完成',
    storyboard_done: '分镜完成',
    image_generating: '图片生成中',
    video_generating: '视频生成中',
    voice_generating: '语音合成中',
    subtitle_generating: '字幕生成中',
    rendering: '渲染中',
    editing: '剪辑中',
    checking: '质检中',
    success: '已完成',
    failed: '生成失败'
  }
  return statusMap[status] || status
}

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  try {
    const date = new Date(dateStr)
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch (e) {
    return dateStr
  }
}

// Filtered list
const filteredTasksList = computed(() => {
  if (statusFilter.value === 'all') {
    return tasksList.value
  }
  return tasksList.value.filter(task => {
    if (statusFilter.value === 'success') {
      return task.status === 'success'
    }
    if (statusFilter.value === 'failed') {
      return task.status === 'failed'
    }
    if (statusFilter.value === 'generating') {
      return task.status !== 'success' && task.status !== 'failed' && task.status !== 'created'
    }
    return true
  })
})

const handleViewTask = (task) => {
  if (task.status === 'success') {
    router.push(`/preview/${task.task_id}`)
  } else {
    router.push(`/progress/${task.task_id}`)
  }
}

const handleRegenerate = (task) => {
  ElMessageBox.confirm(
    '该任务支持断点续传。您可以选择【继续生成】保留已成功生成的素材以重新合成最终视频；或者选择【全新生成】彻底清空素材并从头开始。',
    '重新生成视频',
    {
      distinguishCancelAndClose: true,
      confirmButtonText: '继续生成 (推荐)',
      cancelButtonText: '全新生成',
      type: 'info'
    }
  ).then(async () => {
    await triggerPipeline(task.task_id, true)
  }).catch(async (action) => {
    if (action === 'cancel') {
      await triggerPipeline(task.task_id, false)
    }
  })
}

const triggerPipeline = async (taskId, resume) => {
  loading.value = true
  try {
    await videoApi.runPipeline(taskId, resume)
    ElMessage.success(resume ? '已从上次失败进度处继续生成' : '全新视频生成流水线已启动')
    router.push(`/progress/${taskId}`)
  } catch (error) {
    ElMessage.error(error.message || '重试失败')
  } finally {
    loading.value = false
  }
}

const handleDeleteTask = (task) => {
  ElMessageBox.confirm('确定要彻底删除该视频任务及其生成的所有资产文件吗？此操作不可逆。', '警告', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    confirmButtonClass: 'el-button--danger',
    type: 'error'
  }).then(async () => {
    loading.value = true
    try {
      await videoApi.deleteTask(task.task_id)
      ElMessage.success('任务删除成功')
      await fetchTasks()
    } catch (error) {
      ElMessage.error(error.message || '删除失败')
    } finally {
      loading.value = false
    }
  }).catch(() => {})
}

const fetchTasks = async () => {
  try {
    const list = await videoApi.listTasks()
    list.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    tasksList.value = list
  } catch (error) {
    console.error('Failed to load tasks list', error)
  }
}

onMounted(async () => {
  loading.value = true
  await fetchTasks()
  loading.value = false
})
</script>

<style scoped>
.history-page {
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
  margin: 0 0 8px 0;
}

.page-subtitle {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.filter-bar {
  margin-bottom: 20px;
  display: flex;
  justify-content: flex-start;
}

.filter-radio-group :deep(.el-radio-button__inner) {
  font-weight: 600;
}

.table-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}

.custom-table {
  font-size: 14px;
}

.mono-text {
  font-family: monospace;
  color: #475569;
}

.topic-text {
  font-weight: 600;
  color: #1e293b;
}
</style>
