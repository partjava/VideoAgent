<template>
  <BasicLayout>
    <div class="progress-page">
      <div class="header-intro">
        <h1 class="page-main-title">视频生成任务控制台</h1>
        <div class="header-actions">
          <p class="page-subtitle">
            <span v-if="taskTopic" class="task-topic-badge">{{ taskTopic }}</span>
            任务ID: {{ taskId }}
          </p>
          <div class="buttons-row">
            <el-button
              v-if="progressInfo.status === 'success'"
              type="success"
              icon="VideoPlay"
              class="view-video-btn"
              @click="goToPreview"
            >
              查看视频
            </el-button>
            <el-button
              v-if="progressInfo.status === 'failed'"
              type="danger"
              icon="Refresh"
              class="retry-btn"
              @click="triggerRetry"
            >
              重新生成
            </el-button>
          </div>
        </div>
      </div>

      <!-- Overall progress bar -->
      <TaskProgressBar :progress="progressInfo.progress" :status="progressInfo.status" />

      <!-- Status message -->
      <div class="status-message-box" :class="progressInfo.status">
        <el-icon class="status-message-icon" :class="progressInfo.status">
          <Loading v-if="progressInfo.status === 'running'" />
          <CircleCheck v-else-if="progressInfo.status === 'success'" />
          <Warning v-else />
        </el-icon>
        <span class="status-message-text">{{ progressInfo.message }}</span>
      </div>

      <div class="agents-container">
        <div class="intro-tip-box">
          <el-icon><InfoFilled /></el-icon>
          <span>提示：您可以随时点击已完成的智能体卡片，弹出窗口查看大模型输出的文本、提示词、配音或生成原画！</span>
        </div>

        <div class="agents-flex-grid">
          <AgentStatusCard
            v-for="agent in agentsList"
            :key="agent.name"
            :agent-name="agent.name"
            :status="getAgentStatus(agent)"
            :description="agent.description"
            :output-summary="getAgentOutputSummary(agent)"
            @click="handleAgentClick(agent)"
          />
        </div>
      </div>

      <!-- Detail Modal Dialog -->
      <el-dialog
        v-model="dialogVisible"
        :title="currentDialogTitle"
        width="60%"
        destroy-on-close
        custom-class="agent-detail-dialog"
      >
        <div v-if="dialogLoading" class="dialog-loading-box">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>正在读取智能体输出，请稍候...</span>
        </div>

        <div v-else class="dialog-content-box">
          <!-- 1. TaskPlannerAgent details -->
          <div v-if="activeAgentName === 'TaskPlannerAgent' && taskDetail" class="detail-section">
            <h4 class="detail-subtitle">规划参数</h4>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="视频主题">{{ taskDetail.user_input }}</el-descriptions-item>
              <el-descriptions-item label="解析风格">{{ taskDetail.style || '默认风格' }}</el-descriptions-item>
              <el-descriptions-item label="视频时长">{{ taskDetail.duration }} 秒</el-descriptions-item>
              <el-descriptions-item label="画面比例">{{ taskDetail.ratio || '9:16' }}</el-descriptions-item>
              <el-descriptions-item label="目标平台">{{ taskDetail.platform || '抖音' }}</el-descriptions-item>
              <el-descriptions-item label="生成模式">{{ taskDetail.generation_mode === 'full_dynamic' ? '真实动态视频' : taskDetail.generation_mode }}</el-descriptions-item>
            </el-descriptions>
          </div>

          <!-- 2. ScriptAgent details -->
          <div v-else-if="activeAgentName === 'ScriptAgent' && scriptData" class="detail-section">
            <h4 class="detail-subtitle">大模型生成脚本</h4>
            <div class="script-box">
              <p><strong>视频标题：</strong>{{ scriptData.title }}</p>
              <p><strong>发布文案：</strong>{{ scriptData.publish_copy || '无' }}</p>
              <el-divider />
              <div class="script-body">{{ scriptData.content }}</div>
            </div>
          </div>

          <!-- 3a. StoryboardAgent details — 分镜设计 -->
          <div v-else-if="activeAgentName === 'StoryboardAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">分镜划分清单</h4>
            <div class="storyboard-list">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="scene-item-card">
                <div class="scene-card-header">
                  <span class="scene-idx">分镜 {{ idx + 1 }}</span>
                  <span class="scene-dur">{{ scene.duration }}秒</span>
                  <span v-if="scene.need_dynamic_video" class="scene-video-flag">动态</span>
                  <span :class="'scene-camera-' + (scene.camera_motion ? 'yes' : 'no')">{{ scene.camera_motion || '固定镜头' }}</span>
                </div>
                <div class="scene-card-body">
                  <div class="scene-text-details">
                    <p><strong>口播旁白：</strong>{{ scene.voiceover }}</p>
                    <p><strong>字幕文案：</strong>{{ scene.subtitle }}</p>
                    <p><strong>画面描述：</strong>{{ scene.visual_description }}</p>
                    <p v-if="scene.need_dynamic_video" class="scene-video-box"><strong>动态镜头：</strong>是</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 3b. PromptAgent details — 提示词 -->
          <div v-else-if="activeAgentName === 'PromptAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">图片与视频提示词</h4>
            <div class="storyboard-list">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="scene-item-card">
                <div class="scene-card-header">
                  <span class="scene-idx">分镜 {{ idx + 1 }}</span>
                  <span class="scene-dur">{{ scene.duration }}秒</span>
                </div>
                <div class="scene-card-body">
                  <div class="scene-text-details">
                    <p v-if="scene.image_prompt" class="scene-prompt-box">
                      <strong>图片提示词 (image_prompt)：</strong><br>{{ scene.image_prompt }}
                    </p>
                    <p v-if="scene.negative_prompt" class="scene-prompt-box scene-prompt-negative">
                      <strong>负面提示词 (negative_prompt)：</strong><br>{{ scene.negative_prompt }}
                    </p>
                    <p v-if="scene.video_prompt" class="scene-prompt-box">
                      <strong>视频运动提示词 (video_prompt)：</strong><br>{{ scene.video_prompt }}
                    </p>
                    <p v-if="!scene.image_prompt && !scene.video_prompt">暂无提示词数据</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 3c. DialoguePolishAgent details — 台词润色 -->
          <div v-else-if="activeAgentName === 'DialoguePolishAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">配音台词校准结果</h4>
            <div class="storyboard-list">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="scene-item-card">
                <div class="scene-card-header">
                  <span class="scene-idx">分镜 {{ idx + 1 }}</span>
                  <span class="scene-dur">{{ scene.duration }}秒</span>
                  <span class="scene-status-done">{{ scene.speaker || '旁白' }}</span>
                </div>
                <div class="scene-card-body">
                  <div class="scene-text-details">
                    <p><strong>配音文本：</strong>{{ scene.voiceover }}</p>
                    <p><strong>字幕文本：</strong>{{ scene.subtitle || scene.voiceover }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 3c. ImageAgent details — 生成的图片 -->
          <div v-else-if="activeAgentName === 'ImageAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">Qwen-Image 生成底图素材</h4>
            <div class="storyboard-list">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="scene-item-card">
                <div class="scene-card-header">
                  <span class="scene-idx">分镜 {{ idx + 1 }}</span>
                  <span class="scene-dur">{{ scene.duration }}秒</span>
                  <span v-if="scene.image_path" class="scene-status-done">已生成</span>
                  <span v-else class="scene-status-wait">未生成</span>
                </div>
                <div class="scene-card-body">
                  <div class="scene-img-box">
                    <el-image
                      v-if="scene.image_path"
                      :src="getResolvedMediaUrl(scene.image_path)"
                      fit="cover"
                      class="scene-img"
                      :preview-src-list="[getResolvedMediaUrl(scene.image_path)]"
                    />
                    <div v-else class="scene-no-img">
                      <el-icon><Picture /></el-icon>
                      <span>等待画图</span>
                    </div>
                  </div>
                  <div class="scene-text-details">
                    <p><strong>画面描述：</strong>{{ scene.visual_description }}</p>
                    <p v-if="scene.image_path" class="scene-path-info">{{ scene.image_path }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 3d. VideoAgent details — 生成的视频片段 -->
          <div v-else-if="activeAgentName === 'VideoAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">图生视频片段成果</h4>
            <div class="storyboard-list">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="scene-item-card">
                <div class="scene-card-header">
                  <span class="scene-idx">分镜 {{ idx + 1 }}</span>
                  <span class="scene-dur">{{ scene.duration }}秒</span>
                  <span v-if="scene.need_dynamic_video" class="scene-video-flag">动态</span>
                  <span v-else class="scene-status-static">静态</span>
                </div>
                <div class="scene-card-body">
                  <div v-if="scene.video_path" class="scene-video-preview">
                    <video :src="getResolvedMediaUrl(scene.video_path)" controls class="scene-video-player"></video>
                  </div>
                  <div v-else class="scene-img-box">
                    <el-image
                      v-if="scene.image_path"
                      :src="getResolvedMediaUrl(scene.image_path)"
                      fit="cover"
                      class="scene-img"
                    />
                    <div v-else class="scene-no-img">
                      <el-icon><VideoCamera /></el-icon>
                      <span>无素材</span>
                    </div>
                  </div>
                  <div class="scene-text-details">
                    <p v-if="scene.video_path" class="scene-video-box">
                      <strong>视频文件：</strong><a :href="getResolvedMediaUrl(scene.video_path)" target="_blank">点击下载片段</a>
                    </p>
                    <p v-else><strong>状态：</strong>静态图片镜头（未启用图生视频）</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 4. VoiceAgent details -->
          <div v-else-if="activeAgentName === 'VoiceAgent'" class="detail-section">
            <h4 class="detail-subtitle">Narration 音频旁白</h4>
            <div class="audio-box">
              <p>旁白配音文件已由 edge-tts 渲染成功：</p>
              <audio controls :src="getVoiceAudioUrl()" class="dialog-audio-player"></audio>
            </div>
          </div>

          <!-- 5. SubtitleAgent details -->
          <div v-else-if="activeAgentName === 'SubtitleAgent' && storyboardData.length > 0" class="detail-section">
            <h4 class="detail-subtitle">SRT 字幕与时间轴预览</h4>
            <div class="subtitle-timeline">
              <div v-for="(scene, idx) in storyboardData" :key="scene.scene_id" class="subtitle-line-item">
                <div class="subtitle-time-badge">
                  分镜 {{ idx + 1 }} ({{ scene.duration }}秒)
                </div>
                <div class="subtitle-text-content">
                  {{ scene.subtitle || scene.voiceover || '（无字幕）' }}
                </div>
              </div>
            </div>
            <div class="subtitle-action-bar">
              <el-button type="primary" size="default" @click="downloadSubtitle">下载标准 SRT 文件</el-button>
            </div>
          </div>

          <!-- 6. EditorAgent details -->
          <div v-else-if="activeAgentName === 'EditorAgent' && taskDetail" class="detail-section">
            <h4 class="detail-subtitle">MoviePy 视频合成报告</h4>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="合成状态">{{ taskDetail.metadata?.editor_status || 'success' }}</el-descriptions-item>
              <el-descriptions-item label="输出路径">{{ taskDetail.metadata?.video_path || '无' }}</el-descriptions-item>
              <el-descriptions-item label="合成方式">
                <el-tag type="success" size="small">MoviePy + FFmpeg</el-tag>
              </el-descriptions-item>
            </el-descriptions>
            <div v-if="taskDetail.metadata?.video_path" style="margin-top:12px;">
              <video controls :src="getResolvedMediaUrl(taskDetail.metadata.video_path)" style="width:100%;max-height:300px;border-radius:8px;"></video>
            </div>
          </div>

          <!-- 7. QualityAgent details -->
          <div v-else-if="activeAgentName === 'QualityAgent' && taskDetail" class="detail-section">
            <h4 class="detail-subtitle">自动视频质检报告</h4>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="质检状态">
                <el-tag :type="taskDetail.metadata?.quality_status === 'success' ? 'success' : 'danger'" size="small">
                  {{ taskDetail.metadata?.quality_status === 'success' ? '通过' : '未通过' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="视频文件">{{ taskDetail.metadata?.video_path || '无' }}</el-descriptions-item>
              <el-descriptions-item label="任务状态">{{ taskDetail.status }}</el-descriptions-item>
              <el-descriptions-item label="总进度">{{ taskDetail.progress }}%</el-descriptions-item>
            </el-descriptions>
          </div>

          <!-- 8. ExportAgent details -->
          <div v-else-if="activeAgentName === 'ExportAgent' && taskDetail" class="detail-section">
            <h4 class="detail-subtitle">最终导出资产</h4>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="导出状态">
                <el-tag type="success" size="small">{{ taskDetail.metadata?.export_status || 'success' }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="视频文件">{{ taskDetail.metadata?.video_path || '无' }}</el-descriptions-item>
              <el-descriptions-item label="字幕文件">{{ taskDetail.metadata?.subtitle_path || '无' }}</el-descriptions-item>
            </el-descriptions>
            <div style="margin-top:16px;display:flex;gap:12px;">
              <el-button type="primary" icon="Download" @click="downloadVideo" v-if="taskDetail.metadata?.video_path">
                下载最终视频
              </el-button>
              <el-button icon="Document" @click="downloadSubtitle" v-if="taskDetail.metadata?.subtitle_path">
                下载字幕
              </el-button>
            </div>
          </div>

          <!-- 9. Generic or No Data fallback -->
          <div v-else class="no-data-dialog">
            <el-empty description="该智能体尚未生成数据，或尚未运行到此阶段。" />
          </div>
        </div>

        <template #footer>
          <span class="dialog-footer">
            <el-button @click="dialogVisible = false">关闭</el-button>
          </span>
        </template>
      </el-dialog>
    </div>
  </BasicLayout>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import BasicLayout from '../layouts/BasicLayout.vue'
import TaskProgressBar from '../components/TaskProgressBar.vue'
import AgentStatusCard from '../components/AgentStatusCard.vue'
import { videoApi } from '../api/video'
import { 
  Loading, 
  CircleCheck, 
  Warning, 
  InfoFilled, 
  Picture 
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

const taskTopic = ref('')
const progressInfo = reactive({
  progress: 0,
  status: 'running', // running, success, failed
  current_agent: '',
  message: '正在初始化 Pipeline...'
})

// Dialog Modals variables
const dialogVisible = ref(false)
const dialogLoading = ref(false)
const activeAgentName = ref('')
const currentDialogTitle = ref('')
const taskDetail = ref(null)
const scriptData = ref(null)
const storyboardData = ref([])

// 预缓存：智能体完成时提前拉数据，点击时直接显示
const agentCache = reactive({
  taskDetail: null,
  scriptData: null,
  storyboardData: [],
  ready: {}  // { agentName: true }
})

let pollInterval = null
const errorMessage = ref('')

const agentsList = [
  { name: 'TaskPlannerAgent', threshold: 20, description: '解析用户输入，规划视频的主题、时长、画风风格、输出比例和平台配置。' },
  { name: 'ScriptAgent', threshold: 30, description: '根据规划主题，调用 DeepSeek 生成口播文本和发布文案。' },
  { name: 'StoryboardAgent', threshold: 50, description: '将口播脚本文本拆分为连续分镜，每个分镜包含配音文本和字幕文本。' },
  { name: 'DialoguePolishAgent', threshold: 60, description: 'DeepSeek 精修每个分镜的配音文本和字幕文本。' },
  { name: 'PromptAgent', threshold: 70, description: '根据每个镜头描述，生成图片提示词和视频运动提示词。' },
  { name: 'ImageAgent', threshold: 85, description: '调用图片模型（Doubao / Qwen / ComfyUI）生成每个镜头的底图素材。' },
  { name: 'VideoAgent', threshold: 90, description: '根据底图和运动提示词，调用视频模型生成动态片段（3路并发，审核自动重试）。' },
  { name: 'VoiceAgent', threshold: 93, description: '提取每个分镜的配音文本，调用 edge-tts 生成独立配音文件。' },
  { name: 'SubtitleAgent', threshold: 95, description: '按分镜时间轴组装 SRT 字幕文件（使用配音原文）。' },
  { name: 'EditorAgent', threshold: 96, description: '拼接视频/图片素材，挂载独立配音音轨，烧录字幕，导出 final.mp4。' },
  { name: 'QualityAgent', threshold: 98, description: '校验 final.mp4 文件是否存在。' },
  { name: 'ExportAgent', threshold: 100, description: '保存结果到数据库。' }
]

const getAgentStatus = (agent) => {
  if (progressInfo.status === 'failed') {
    if (progressInfo.current_agent === agent.name) return 'failed'
    const failedAgent = agentsList.find(a => a.name === progressInfo.current_agent)
    if (failedAgent && agent.threshold < failedAgent.threshold) return 'success'
    return 'waiting'
  }

  if (progressInfo.status === 'success') {
    return 'success'
  }

  // 优先级1：进度超过阈值 +2，说明已经过了该智能体阶段
  if (progressInfo.progress > agent.threshold + 2) {
    return 'success'
  }

  // 优先级2：正好是当前正在执行的智能体
  if (progressInfo.current_agent === agent.name) {
    return 'running'
  }

  // 优先级3：进度刚好达到阈值（边界情况）
  if (progressInfo.progress >= agent.threshold) {
    return 'success'
  }

  return 'waiting'
}

const getAgentOutputSummary = (agent) => {
  const status = getAgentStatus(agent)
  if (status === 'failed') {
    return errorMessage.value || '执行错误'
  }
  if (status === 'success') {
    return '完成生成，点击查看详细'
  }
  if (status === 'running') {
    return '处理中...'
  }
  return '等待中'
}

const getResolvedMediaUrl = (path) => {
  if (!path) return ''
  const clean = path.replace('backend/', '')
  return `/${clean}`
}

const getVoiceAudioUrl = () => {
  return `/assets/${taskId}/audio/voice.mp3`
}

const downloadSubtitle = () => {
  window.open(`/outputs/${taskId}/subtitle.srt`, '_blank')
}

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

// 预拉取某个智能体的数据并缓存
async function prefetchAgentData(agentName) {
  if (agentCache.ready[agentName]) return // 已缓存
  try {
    if (agentName === 'TaskPlannerAgent' || agentName === 'EditorAgent' || agentName === 'QualityAgent' || agentName === 'ExportAgent') {
      agentCache.taskDetail = await videoApi.getTask(taskId)
    } else if (agentName === 'ScriptAgent') {
      agentCache.scriptData = await videoApi.getScript(taskId)
    } else if (['StoryboardAgent', 'DialoguePolishAgent', 'PromptAgent', 'ImageAgent', 'VideoAgent', 'SubtitleAgent'].includes(agentName)) {
      agentCache.storyboardData = await videoApi.getStoryboard(taskId)
    }
    agentCache.ready[agentName] = true
  } catch (e) {
    // 预拉取失败无所谓，点击时再拉
  }
}

// Handle card click to open Dialog Modal
const handleAgentClick = async (agent) => {
  const status = getAgentStatus(agent)
  if (status !== 'success') {
    return // Only allow checking detail if it has successfully run
  }

  activeAgentName.value = agent.name
  const titles = {
    TaskPlannerAgent: '任务规划策划案',
    ScriptAgent: '文案脚本与口播旁白',
    StoryboardAgent: '分镜镜头划分清单',
    DialoguePolishAgent: 'DeepSeek 台词润色',
    PromptAgent: '图片与视频提示词',
    ImageAgent: '底图素材',
    VoiceAgent: '音频旁白配音文件',
    SubtitleAgent: 'SRT 字幕文件',
    VideoAgent: '图生视频片段',
    EditorAgent: 'MoviePy 视频合成信息',
    QualityAgent: '视频质量自检报告',
    ExportAgent: '生成资产导出打包'
  }
  currentDialogTitle.value = titles[agent.name] || agent.name
  dialogVisible.value = true
  dialogLoading.value = false  // 不再加载状态，直接用缓存或即时拉取

  try {
    // 先尝试用缓存
    if (agent.name === 'TaskPlannerAgent') {
      if (agentCache.ready[agent.name]) { taskDetail.value = agentCache.taskDetail; return }
      taskDetail.value = await videoApi.getTask(taskId)
    } else if (agent.name === 'ScriptAgent') {
      if (agentCache.ready[agent.name]) { scriptData.value = agentCache.scriptData; return }
      scriptData.value = await videoApi.getScript(taskId)
    } else if (['StoryboardAgent', 'DialoguePolishAgent', 'PromptAgent', 'ImageAgent', 'VideoAgent', 'SubtitleAgent'].includes(agent.name)) {
      if (agentCache.ready[agent.name]) { storyboardData.value = agentCache.storyboardData; return }
      storyboardData.value = await videoApi.getStoryboard(taskId)
    } else if (['EditorAgent', 'QualityAgent', 'ExportAgent'].includes(agent.name)) {
      if (agentCache.ready[agent.name]) { taskDetail.value = agentCache.taskDetail; return }
      taskDetail.value = await videoApi.getTask(taskId)
    }
  } catch (err) {
    console.error('Failed loading agent detail dialog:', err)
  }
}

const fetchProgress = async () => {
  try {
    const data = await videoApi.getTaskProgress(taskId)
    progressInfo.progress = data.progress
    progressInfo.current_agent = data.current_agent
    progressInfo.message = data.message

    // 预拉取已完成智能体的数据
    for (const agent of agentsList) {
      if (data.progress >= agent.threshold && !agentCache.ready[agent.name]) {
        prefetchAgentData(agent.name)
      }
    }

    if (data.task_status === 'success') {
      progressInfo.status = 'success'
      clearInterval(pollInterval)
    } else if (data.task_status === 'failed') {
      progressInfo.status = 'failed'
      clearInterval(pollInterval)
      const taskDetailData = await videoApi.getTask(taskId)
      errorMessage.value = taskDetailData.metadata?.last_error || '任务处理失败'
      progressInfo.message = `失败原因: ${errorMessage.value}`
    }
  } catch (error) {
    console.error('Fetch progress failed', error)
  }
}

const triggerRetry = () => {
  ElMessageBox.confirm(
    '该任务支持从中断进度处继续生成。您可以选择【继续生成】以保留已成功的素材、节省消耗额度；或者选择【全新生成】彻底清空并重新开始。',
    '重新启动视频流水线',
    {
      distinguishCancelAndClose: true,
      confirmButtonText: '继续生成 (推荐)',
      cancelButtonText: '全新生成',
      type: 'info'
    }
  ).then(async () => {
    await executePipelineRetry(true)
  }).catch(async (action) => {
    if (action === 'cancel') {
      await executePipelineRetry(false)
    }
  })
}

const executePipelineRetry = async (resume) => {
  errorMessage.value = ''
  progressInfo.status = 'running'
  progressInfo.message = resume ? '正在恢复生成并连结中断的进度...' : '正在初始化全新生成...'
  
  try {
    await videoApi.runPipeline(taskId, resume)
    ElMessage.success(resume ? '已从上次失败进度处继续生成' : '全新视频生成流水线已启动')
    
    // 重新开启轮询
    if (pollInterval) {
      clearInterval(pollInterval)
    }
    pollInterval = setInterval(fetchProgress, 2000)
  } catch (error) {
    errorMessage.value = error.message || '重试请求失败'
    progressInfo.status = 'failed'
    progressInfo.message = `失败原因: ${errorMessage.value}`
    ElMessage.error(errorMessage.value)
  }
}

const goToPreview = () => {
  router.push(`/preview/${taskId}`)
}

onMounted(async () => {
  try {
    await fetchProgress()
    
    const taskDetailData = await videoApi.getTask(taskId)
    if (taskDetailData.topic) {
      taskTopic.value = taskDetailData.topic
    }
    // 如果任务还是 created 状态（页面直接刷新的情况），触发 Pipeline
    if (taskDetailData.status === 'created') {
      videoApi.runPipeline(taskId).catch(err => {
        console.warn('Pipeline auto-trigger failed:', err)
      })
    }
    // 开始轮询进度
    pollInterval = setInterval(fetchProgress, 2000)
  } catch (err) {
    errorMessage.value = err.message
    progressInfo.status = 'failed'
  }
})

onUnmounted(() => {
  if (pollInterval) {
    clearInterval(pollInterval)
  }
})
</script>

<style scoped>
.progress-page {
  padding: 16px 20px;
  width: 100%;
  box-sizing: border-box;
}

.header-intro {
  margin-bottom: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-main-title {
  font-size: 26px;
  font-weight: 800;
  color: #0f172a;
  margin: 0;
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.header-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.page-subtitle {
  font-size: 13px;
  color: #64748b;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-topic-badge {
  display: inline-block;
  background: #e0e7ff;
  color: #4338ca;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.buttons-row {
  display: flex;
  gap: 12px;
}

.view-video-btn {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  border: none;
  font-weight: 600;
  box-shadow: 0 4px 10px rgba(16, 185, 129, 0.2);
}

.view-video-btn:hover {
  background: linear-gradient(135deg, #059669 0%, #047857 100%);
  box-shadow: 0 6px 14px rgba(16, 185, 129, 0.3);
}

.retry-btn {
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  border: none;
  font-weight: 600;
  box-shadow: 0 4px 10px rgba(239, 68, 68, 0.2);
}

.retry-btn:hover {
  background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
  box-shadow: 0 6px 14px rgba(239, 68, 68, 0.3);
}

.status-message-box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-radius: 10px;
  margin-bottom: 24px;
  border: 1px solid #e2e8f0;
  font-size: 15px;
  font-weight: 600;
  background-color: #ffffff;
}

.status-message-box.running {
  border-color: #e0e7ff;
  background-color: #f5f3ff;
  color: #4f46e5;
}

.status-message-box.success {
  border-color: #d1fae5;
  background-color: #ecfdf5;
  color: #059669;
}

.status-message-box.failed {
  border-color: #fee2e2;
  background-color: #fef2f2;
  color: #dc2626;
}

.status-message-icon {
  font-size: 22px;
}

.status-message-icon.running {
  animation: spin 2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.agents-container {
  background: #ffffff;
  padding: 24px;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.intro-tip-box {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 10px 16px;
  border-radius: 8px;
  margin-bottom: 20px;
  font-size: 13.5px;
  font-weight: 500;
}

.intro-tip-box .el-icon {
  font-size: 16px;
  color: #15803d;
}

/* Flex Grid of Cards */
.agents-flex-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

/* Dialog and popups details */
.dialog-loading-box {
  padding: 40px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #64748b;
}

.dialog-loading-box .el-icon {
  font-size: 32px;
}

.detail-subtitle {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin-top: 0;
  margin-bottom: 12px;
  border-left: 3px solid #4f46e5;
  padding-left: 8px;
}

/* Script dialog */
.script-box {
  background: #f8fafc;
  padding: 16px 20px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  color: #334155;
  line-height: 1.6;
}

.script-body {
  white-space: pre-line;
  font-size: 15px;
  line-height: 1.8;
}

/* Storyboard dialog */
.storyboard-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 60vh;
  overflow-y: auto;
}

.scene-item-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
  background: #ffffff;
  flex-shrink: 0;
}

.scene-card-header {
  background: #f8fafc;
  padding: 8px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid #f1f5f9;
}

.scene-idx {
  font-size: 11.5px;
  font-weight: 700;
  background: #0f172a;
  color: #ffffff;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-dur {
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
}

.scene-video-flag {
  font-size: 11px;
  font-weight: 700;
  background: #e0e7ff;
  color: #4f46e5;
  padding: 1px 5px;
  border-radius: 4px;
}

.scene-camera-yes {
  font-size: 10px;
  font-weight: 600;
  color: #0891b2;
  background: #cffafe;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-camera-no {
  font-size: 10px;
  color: #94a3b8;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-status-done {
  font-size: 10px;
  font-weight: 600;
  color: #059669;
  background: #d1fae5;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-status-wait {
  font-size: 10px;
  color: #d97706;
  background: #fef3c7;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-status-static {
  font-size: 10px;
  color: #64748b;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}

.scene-prompt-negative {
  border-color: #fde68a !important;
  background: #fffbeb !important;
}

.scene-path-info {
  font-size: 11px;
  color: #94a3b8;
  word-break: break-all;
  background: #f8fafc;
  padding: 4px 6px;
  border-radius: 4px;
  margin-top: 4px;
}

.scene-video-player {
  width: 90px;
  height: 160px;
  object-fit: cover;
  border-radius: 6px;
  background: #000;
}

.scene-video-preview {
  width: 90px;
  height: 160px;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  background: #0f172a;
}

.scene-card-body {
  padding: 16px;
  display: flex;
  gap: 16px;
}

.scene-img-box {
  width: 90px;
  height: 160px; /* 9:16 */
  background: #f1f5f9;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.scene-img {
  width: 100%;
  height: 100%;
}

.scene-no-img {
  color: #94a3b8;
  font-size: 10px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.scene-no-img .el-icon {
  font-size: 20px;
}

.scene-text-details {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.scene-text-details p {
  font-size: 13px;
  margin: 0;
  color: #334155;
  line-height: 1.5;
}

.scene-prompt-box {
  font-size: 11.5px;
  color: #64748b;
  background: #f8fafc;
  padding: 6px 8px;
  border-radius: 4px;
  border: 1px dashed #cbd5e1;
}

.scene-video-box {
  font-size: 12px;
  margin-top: 4px;
}

/* Audio dialog */
.audio-box {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  padding: 20px;
  border-radius: 8px;
  text-align: center;
  color: #14532d;
}

.dialog-audio-player {
  width: 100%;
  max-width: 400px;
}
/* Subtitle dialog */
.subtitle-timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 45vh;
  overflow-y: auto;
  background: #f8fafc;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  margin-bottom: 16px;
}

.subtitle-line-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px dashed #e2e8f0;
}

.subtitle-line-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.subtitle-time-badge {
  font-size: 11px;
  font-weight: 700;
  background: #e2e8f0;
  color: #475569;
  padding: 2px 8px;
  border-radius: 4px;
  flex-shrink: 0;
  margin-top: 2px;
}

.subtitle-text-content {
  font-size: 13.5px;
  color: #1e293b;
  line-height: 1.6;
}

.subtitle-action-bar {
  display: flex;
  justify-content: flex-end;
}
</style>
