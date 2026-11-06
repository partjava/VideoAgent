<template>
  <BasicLayout>
    <div class="preview-page" v-loading="loading">
      <!-- Title header -->
      <div class="header-intro">
        <h1 class="page-main-title">生成结果控制台</h1>
        <p class="page-subtitle">您的短视频已合成完毕，可在线播放、按质检定位精修分镜并下载。</p>
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
          <p>该任务尚未完成渲染流程或正处于开拍前预审阶段。</p>
          <el-button type="primary" icon="CaretRight" @click="handleGoToProgress">查看生成进度与预审</el-button>
        </div>
      </div>

      <!-- Main Preview Layout -->
      <div class="preview-grid" v-else-if="taskDetail">
        <!-- Left: Video Player region (aspect ratio 9:16 layout) -->
        <div class="player-wrapper">
          <VideoPlayerCard
            :video-path="taskDetail.metadata?.video_path ? taskDetail.metadata.video_path + '?v=' + previewVersion : null"
            :subtitle-path="taskDetail.metadata?.subtitle_path ? taskDetail.metadata.subtitle_path + '?v=' + previewVersion : null"
            :title="taskDetail.topic || '未命名视频'"
            :ratio="taskDetail.ratio"
            :duration="taskDetail.duration"
            :style="taskDetail.style"
          />
        </div>

        <!-- Right: Detail Info & Script/Storyboard download actions -->
        <div v-if="taskDetail && storyboardData.length > 0" class="studio-editing-section">
          <el-card class="detail-card studio-card" shadow="never">
            <template #header>
              <div class="card-header studio-header">
                <span class="card-title">分镜精修工作台</span>
                <span class="header-tip">沿时间轴或质检提示选择片段，查看画面并局部微调</span>
              </div>
            </template>

            <div v-if="qualityIssues.length" class="quality-jump-panel">
              <div class="quality-jump-header">
                <strong>质检复核提示（点击直接跳到对应分镜与秒数）</strong>
                <el-tag size="small" type="warning">{{ qualityIssues.length }} 项建议复核</el-tag>
              </div>
              <ul class="quality-jump-list">
                <li v-for="(item, idx) in qualityIssues" :key="idx">
                  <button type="button" class="quality-jump-btn" @click="jumpToQualityIssue(item)">
                    <span class="jump-badge">
                      <template v-if="item.time != null">{{ Number(item.time).toFixed(1) }}s</template>
                      <template v-if="item.scene_index"> · 镜头 {{ item.scene_index }}</template>
                      <template v-if="item.scene_offset != null">（镜内 {{ Number(item.scene_offset).toFixed(1) }}s）</template>
                    </span>
                    <span class="jump-text">{{ item.issue }}</span>
                    <span class="jump-arrow">定位精修 →</span>
                  </button>
                </li>
              </ul>
            </div>

            <nav class="section-timeline" aria-label="分镜时间轴">
              <div v-for="(group, index) in timelineGroups" :key="group[0].scene.scene_id" class="timeline-group">
                <small>{{ taskDetail.generation_mode === 'paired_5s' ? '段落' : '片段' }} {{ index + 1 }} · {{ group[0].start }}–{{ group.at(-1).end }} 秒</small>
                <div class="timeline-clips">
                  <button
                    v-for="item in group"
                    :key="item.scene.scene_id"
                    type="button"
                    :aria-pressed="item.scene.scene_id === currentScene?.scene_id"
                    @click="selectClip(item)"
                  >
                    {{ item.start }}–{{ item.end }} 秒
                    <span v-if="item.scene.stale_tail"> · 待续接</span>
                  </button>
                </div>
              </div>
            </nav>

            <div class="scenes-edit-list">
              <div
                v-for="scene in currentScene ? [currentScene] : []"
                :key="scene.scene_id"
                class="scene-edit-item"
                v-loading="retryingSceneId === scene.scene_id"
              >
                <div class="clip-overview">
                  <img
                    v-if="scene.actual_first_frame_path || scene.continuity_input_path || scene.image_path"
                    :src="mediaUrl(scene.actual_first_frame_path || scene.continuity_input_path || scene.image_path)"
                    alt="当前片段首帧"
                  />
                  <div>
                    <div class="clip-overview-head">
                      <strong>镜头 {{ scene.scene_index }} · {{ scene.duration }} 秒</strong>
                      <span
                        v-for="(choice, cIdx) in scene.reference_choices || []"
                        :key="cIdx"
                        class="ref-view-pill"
                      >
                        参考图：{{ viewLabel(choice.view) }}
                      </span>
                    </div>
                    <p>{{ scene.motion_beats?.[0]?.action || scene.visual_description || '查看下方的动作与配音细节' }}</p>
                  </div>
                </div>
                <details class="clip-dialogue">
                  <summary>编辑角色与台词</summary>
                  <div class="scene-item-header">
                    <div class="scene-meta-left">
                      <span class="scene-badge">{{ taskDetail?.generation_mode === 'paired_5s'
                        ? `第 ${Math.ceil(scene.scene_index / 2)} 部分 · ${scene.scene_index % 2 ? '前段' : '后段'}`
                        : `镜头 ${scene.scene_index}` }}</span>
                      <el-tag v-if="scene.stale_tail" type="warning" size="small">待续接</el-tag>
                      <span class="scene-time">{{ scene.duration }} 秒</span>
                    </div>
                    <div class="scene-meta-right">
                      <span class="speaker-label">角色：</span>
                      <el-input
                        v-model="scene.speaker"
                        size="small"
                        style="width: 120px"
                        placeholder="说话人/角色"
                      />
                    </div>
                  </div>

                  <div class="scene-item-body">
                    <el-input
                      v-model="scene.voiceover"
                      type="textarea"
                      :rows="2"
                      placeholder="分镜台词/口播文案"
                      class="voiceover-input"
                    />
                  </div>

                  <label>台词起点（段内秒数）
                    <el-input-number v-model="scene.voice_start" :min="0" :max="Math.max(0, scene.duration - 0.1)" :step="0.1" size="small" />
                    <el-button
                      size="small"
                      plain
                      :loading="polishingSceneId === scene.scene_id"
                      :disabled="polishingSceneId !== null || retryingSceneId !== null"
                      @click="polishDialogue(scene)"
                    >重新优化旁白</el-button>
                  </label>
                </details>
                <AudioTimingEditor
                  :scene="scene"
                  :busy="retryingSceneId !== null"
                  @apply="lines => applyAudioTiming(scene, lines)"
                />
                <div v-if="scene.transition_type === 'continue_action'" class="handoff-frames">
                  <figure v-if="storyboardData.find(item => item.scene_index === scene.scene_index - 1)?.last_frame_path">
                    <img :src="mediaUrl(storyboardData.find(item => item.scene_index === scene.scene_index - 1).last_frame_path)" alt="上一段实际尾帧" />
                    <figcaption>上一段当前尾帧</figcaption>
                  </figure>
                  <figure v-if="scene.actual_first_frame_path || scene.continuity_input_path">
                    <img :src="mediaUrl(scene.actual_first_frame_path || scene.continuity_input_path)" :alt="scene.actual_first_frame_path ? '本段实际首帧' : '本段生成输入帧'" />
                    <figcaption>{{ scene.actual_first_frame_path ? '本段实际首帧' : '本段生成输入帧' }}</figcaption>
                  </figure>
                </div>
                <p v-if="scene.join_review?.review_suggested" class="scene-markers">
                  首尾帧画面差异较大，建议对照检查姿态和构图。
                </p>
                <details
                  v-if="scene.video_path || scene.camera_plan"
                  class="scene-markers"
                  :open="autoOpenClipMarkers === scene.scene_id"
                >
                  <summary>运镜计划和动作时间点</summary>
                  <p v-if="scene.camera_plan">{{ scene.camera_plan.purpose }} · {{ scene.camera_plan.framing }} · {{ scene.camera_plan.focus }}</p>
                  <p v-if="scene.opening_frame_review?.reason">首帧检查：{{ scene.opening_frame_review.reason }}</p>
                  <video v-if="scene.video_path" :id="'clip-' + scene.scene_id" :src="mediaUrl(scene.video_path)" controls playsinline preload="none" />
                  <div v-for="beat in scene.observed_handoff?.plan?.motion_beats || scene.motion_beats || []" :key="beat.start" class="cue-row">
                    <button type="button" @click="seekScene(scene, beat.start)">{{ beat.start }} 秒</button>
                    <span>{{ beat.action }}</span>
                  </div>
                  <div v-if="scene.voiceover" class="cue-row">
                    <button type="button" @click="seekScene(scene, scene.voice_start ?? 0)">{{ scene.voice_start ?? 0 }} 秒</button>
                    <span>台词 · {{ scene.voice_cue || scene.voiceover }}</span>
                  </div>
                  <div v-for="(cue, index) in scene.sound_cues || []" :key="'sfx-' + index" class="cue-row">
                    <button type="button" @click="seekScene(scene, cue.start)">{{ cue.start }} 秒</button>
                    <span>音效 · {{ cue.name }}</span>
                  </div>
                </details>
                <p v-if="pendingClip === scene.scene_id" class="rerun-impact">{{ impactText(scene) }}</p>
                <div class="scene-item-actions">
                  <details class="secondary-actions">
                    <summary>更多操作</summary>
                    <el-button size="small" :disabled="retryingSceneId !== null" @click="handleRerunScene(scene, 'voice')">重录配音</el-button>
                    <el-button
                      v-if="scene.transition_type !== 'continue_action'"
                      size="small"
                      :disabled="retryingSceneId !== null"
                      @click="handleRerunScene(scene, 'image')"
                    >重生底图</el-button>
                  </details>
                  <el-button
                    size="small"
                    type="primary"
                    :disabled="retryingSceneId !== null"
                    @click="confirmClip(scene)"
                  >
                    {{ pendingClip === scene.scene_id ? "确认重拍" : "重拍视频" }}
                  </el-button>
                </div>
              </div>
            </div>
          </el-card>
        </div>

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

          <GenerationUsage v-if="taskId" scope-type="task" :scope-id="String(taskId)" />

          <details class="detail-card">
            <summary>更多导出</summary>
            <div class="downloads-grid">
              <el-button :disabled="!scriptData" @click="downloadScript">下载脚本 TXT</el-button>
              <el-button :disabled="!storyboardData.length" @click="downloadStoryboard">下载分镜 JSON</el-button>
            </div>
          </details>
        </div>
      </div>

      <!-- OpenCut / OpenMontage: 分镜工作台与局部重生成 -->
      <details v-if="taskDetail?.status === 'success'" @toggle="showProcess = $event.target.open">
        <summary>完整生成过程</summary>
        <TaskProgress v-if="showProcess" embedded />
      </details>
    </div>
  </BasicLayout>
</template>

<script setup>
import { computed, nextTick, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import BasicLayout from '../layouts/BasicLayout.vue'
import TaskProgress from './TaskProgress.vue'
import VideoPlayerCard from '../components/VideoPlayerCard.vue'
import AudioTimingEditor from '../components/AudioTimingEditor.vue'
import GenerationUsage from '../components/GenerationUsage.vue'
import { videoApi } from '../api/video'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id
const showProcess = ref(false)
const previewVersion = ref(Date.now())
const autoOpenClipMarkers = ref('')

const loading = ref(true)
const taskDetail = ref(null)
const scriptData = ref(null)
const storyboardData = ref([])
const retryingSceneId = ref(null)
const polishingSceneId = ref(null)

const viewLabel = (view) =>
  ({ front: '正面', side: '侧面', profile: '侧面', full: '全身', whole: '全身' })[view] || view || '正面'

const qualityIssues = computed(() => {
  const report = taskDetail.value?.metadata?.quality_report
  if (!report) return []
  const visual = Array.isArray(report.visual_review?.issues) ? report.visual_review.issues : []
  const semRaw = Array.isArray(report.semantic_review?.located_issues)
    ? report.semantic_review.located_issues
    : Array.isArray(report.semantic_review?.issues)
      ? report.semantic_review.issues.map(raw => (typeof raw === 'string' ? { issue: raw } : raw))
      : []
  return [...visual, ...semRaw]
})

async function jumpToQualityIssue(item) {
  let targetSceneId = item?.scene_id
  let localOffset = item?.scene_offset
  let fullTime = item?.time

  let elapsed = 0
  for (const s of storyboardData.value) {
    const dur = Number(s.duration) || 0
    if (targetSceneId && s.scene_id === targetSceneId) {
      if (fullTime == null) fullTime = elapsed + Number(localOffset || 0)
      if (localOffset == null) localOffset = Math.max(0, Number(fullTime || elapsed) - elapsed)
      break
    }
    if (!targetSceneId && typeof fullTime === 'number' && fullTime >= elapsed && fullTime <= elapsed + dur) {
      targetSceneId = s.scene_id
      localOffset = Math.max(0, fullTime - elapsed)
      break
    }
    elapsed += dur
  }

  if (targetSceneId) {
    selectedClip.value = targetSceneId
    pendingClip.value = null
    autoOpenClipMarkers.value = targetSceneId
  }
  const mainPlayer = document.querySelector('.player-wrapper video')
  if (mainPlayer && typeof fullTime === 'number') {
    mainPlayer.currentTime = Math.max(0, fullTime)
  }
  await nextTick()
  if (targetSceneId && typeof localOffset === 'number') {
    const clipPlayer = document.getElementById('clip-' + targetSceneId)
    if (clipPlayer) clipPlayer.currentTime = Math.max(0, localOffset)
  }
}

async function applyAudioTiming(scene, lines) {
  retryingSceneId.value = scene.scene_id
  try {
    await videoApi.applyAudioTiming(taskId, scene.scene_id, { lines })
    storyboardData.value = await videoApi.getStoryboard(taskId)
    taskDetail.value = await videoApi.getTask(taskId)
    previewVersion.value = Date.now()
    ElMessage.success('配音时间已应用，成片已重新合成')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    retryingSceneId.value = null
  }
}

async function polishDialogue(scene) {
  const draft = { voiceover: scene.voiceover || '', speaker: scene.speaker || '旁白', voice_start: scene.voice_start ?? 0 }
  polishingSceneId.value = scene.scene_id
  try {
    const result = await videoApi.polishSceneDialogue(taskId, scene.scene_id, draft)
    if ((scene.voiceover || '') !== draft.voiceover || (scene.speaker || '旁白') !== draft.speaker ||
        (scene.voice_start ?? 0) !== draft.voice_start || !storyboardData.value.includes(scene)) {
      ElMessage.warning('编辑内容已变动，请重新优化。')
      return
    }
    scene.voiceover = result.voiceover
    ElMessage.success('已填入新旁白，确认后可重录配音。')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    polishingSceneId.value = null
  }
}

const selectedClip = ref(null)
const pendingClip = ref(null)
const currentScene = computed(() => storyboardData.value.find(scene =>
  scene.scene_id === selectedClip.value) || storyboardData.value[0])
const timelineGroups = computed(() => {
  const size = taskDetail.value?.generation_mode === 'paired_5s' ? 2 : 1
  let end = 0
  const items = storyboardData.value.map(scene => {
    const start = end
    end += Number(scene.duration) || 0
    return { scene, start, end }
  })
  return Array.from({ length: Math.ceil(items.length / size) }, (_, i) =>
    items.slice(i * size, (i + 1) * size))
})
const selectClip = ({ scene, start }) => {
  selectedClip.value = scene.scene_id
  pendingClip.value = null
  const player = document.querySelector('.player-wrapper video')
  if (player) player.currentTime = start
}
const impactText = scene => {
  const next = storyboardData.value.find(item =>
    Number(item.scene_index) === Number(scene.scene_index) + 1 &&
    item.transition_type === 'continue_action')
  return next
    ? `将重拍镜头 ${scene.scene_index}。镜头 ${next.scene_index} 依赖它的尾帧，随后需要单独重拍才能合成。`
    : `只重拍镜头 ${scene.scene_index}，其余片段沿用。`
}
const confirmClip = scene => {
  if (pendingClip.value !== scene.scene_id) {
    pendingClip.value = scene.scene_id
    return
  }
  pendingClip.value = null
  handleRerunScene(scene, 'video')
}
const seekScene = (scene, second) => {
  const player = document.getElementById('clip-' + scene.scene_id)
  if (player && Number.isFinite(Number(second))) player.currentTime = Number(second)
}
const mediaUrl = path => "/" + path.replace(/^backend\//, "")

const handleRerunScene = async (scene, type) => {
  retryingSceneId.value = scene.scene_id
  const typeText = type === 'voice' ? '配音' : type === 'image' ? '底图' : '视频'
  try {
    ElMessage.info(`正在重新生成镜头 ${scene.scene_index} 的${typeText}...`)
    const res = await videoApi.rerunScene(taskId, scene.scene_id, {
      rerun_type: type,
      voiceover: scene.voiceover,
      speaker: scene.speaker,
      voice_start: scene.voice_start ?? 0,
    })
    if (res?.pending_scenes?.length) {
      ElMessage.warning(res.message)
    } else {
      ElMessage.success(`镜头 ${scene.scene_index} ${typeText}生成成功并已重新合成！`)
    }
    storyboardData.value = await videoApi.getStoryboard(taskId)
    const data = await videoApi.getTask(taskId)
    taskDetail.value = data
    if (res?.scene) {
      const idx = storyboardData.value.findIndex(s => s.scene_id === scene.scene_id)
      if (idx !== -1) {
        storyboardData.value[idx] = { ...storyboardData.value[idx], ...res.scene }
      }
    }
  } catch (err) {
    ElMessage.error(`镜头生成失败: ${err.message}`)
  } finally {
    retryingSceneId.value = null
  }
}

const getModeText = (mode) => {
  const modes = {
    key_scenes: '关键镜头动态 (推荐)',
    full_dynamic: '全动态视频 (精美)',
    native_10s: '原生 10 秒动态镜头',
    paired_5s: '双 5 秒连续动作镜头'
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
    const data = await videoApi.getTask(taskId)
    taskDetail.value = data

    if (data.status === 'success' && data.metadata?.video_path) {
      try {
        scriptData.value = await videoApi.getScript(taskId)
      } catch (e) {
        console.warn('Script details not found', e)
      }

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

.quality-jump-panel {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 10px;
}
.quality-jump-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: #92400e;
}
.quality-jump-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.quality-jump-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #fcd34d;
  background: #ffffff;
  color: #1e293b;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.quality-jump-btn:hover {
  background: #fef3c7;
}
.jump-badge {
  font-weight: 700;
  color: #b45309;
  white-space: nowrap;
}
.jump-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.jump-arrow {
  font-size: 12px;
  font-weight: 600;
  color: #2563eb;
  white-space: nowrap;
}

/* OpenCut / OpenMontage Studio Editing */
.studio-editing-section {
  margin-top: 32px;
}

.studio-card {
  border-radius: 12px;
  background: #ffffff;
}

.studio-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  justify-content: space-between;
  align-items: center;
}

.header-tip {
  font-size: 13px;
  color: #64748b;
  font-weight: normal;
}

.scenes-edit-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
  gap: 16px;
}

.scene-edit-item {
  min-width: 0;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px;
  background: #f8fafc;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: all 0.2s ease;
}

.scene-edit-item:hover {
  border-color: #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.clip-overview-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ref-view-pill {
  padding: 2px 8px;
  border-radius: 999px;
  background: #f3e8ff;
  color: #6b21a8;
  font-size: 11px;
  font-weight: 700;
}

.scene-item-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
  align-items: center;
}

.scene-meta-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.scene-badge {
  background: #4f46e5;
  color: #ffffff;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 6px;
}

.scene-time {
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
}

.scene-meta-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.speaker-label {
  font-size: 12px;
  color: #64748b;
}

.scene-item-body {
  width: 100%;
}

.voiceover-input {
  font-size: 13px;
}

.scene-item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.scene-item-actions :deep(.el-button) {
  margin: 0;
  max-width: 100%;
  height: auto;
  min-height: 24px;
  white-space: normal;
}
.handoff-frames { display: flex; gap: 8px; }
.handoff-frames figure { min-width: 0; flex: 1; margin: 0; font-size: 12px; }
.handoff-frames img { width: 100%; height: 110px; object-fit: contain; background: #e2e8f0; }
.scene-markers { min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
.scene-markers summary { cursor: pointer; }
.scene-markers video { display: block; width: 100%; max-height: 220px; margin: 8px 0; background: #17253b; }
.cue-row { display: flex; align-items: baseline; gap: 8px; margin-top: 6px; }
.cue-row button { flex: 0 0 auto; border: 1px solid #d7e3f4; border-radius: 5px; background: #eaf1fc; cursor: pointer; padding: 2px 5px; }

.preview-grid { display:grid; grid-template-columns:minmax(0, 0.85fr) minmax(0, 1.15fr); align-items:start; gap:20px; }
.preview-grid > * { min-width:0; }
.preview-grid .detail-wrapper { grid-column:1 / -1; }
.preview-grid .studio-editing-section { margin:0; }
.player-wrapper :deep(.player-container) { background:#111827; border-radius:14px; }
.player-wrapper :deep(video) { max-height:65vh; }
.studio-header { flex-wrap:wrap; gap:8px; }
.section-timeline { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%, 130px),1fr)); gap:8px; margin-bottom:16px; }
.timeline-group { background:#f1f4fa; padding:10px; border-radius:10px; }
.timeline-group small { color:#475569; }
.timeline-clips { display:flex; flex-wrap:wrap; gap:5px; margin-top:6px; }
.timeline-clips button { border:1px solid #cbd5e1; background:white; border-radius:6px; color:#334155; padding:5px 8px; cursor:pointer; }
.timeline-clips button[aria-pressed="true"] { background:#4338ca; border-color:#4338ca; color:white; }
.studio-card .scenes-edit-list { display:block; }
.clip-overview { display:flex; gap:12px; align-items:center; margin-bottom:14px; overflow-wrap:anywhere; }
.clip-overview img { width:64px; height:82px; flex:none; object-fit:cover; border-radius:8px; }
.clip-overview p { margin-top:6px; color:#475569; }
.clip-dialogue summary, .secondary-actions summary { cursor:pointer; color:#334155; padding:6px 0; }
.scene-item-header, .scene-meta-right, .scene-item-actions { flex-wrap:wrap; }
.scene-item-actions .el-button { margin:0; }
.rerun-impact { background:#fff7ed; color:#9a3412; border-radius:8px; padding:10px; }
@media (max-width: 850px) { .preview-grid { grid-template-columns:minmax(0,1fr); } }
</style>
