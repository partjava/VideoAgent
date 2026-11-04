<template>
  <BasicLayout>
    <main class="studio">
      <section class="studio-hero">
        <div>
          <div class="eyebrow"><span class="signal" :class="status"></span> VIDEO STUDIO <span class="hero-divider"></span> {{ statusLabel }}</div>
          <h1>{{ task?.topic || '视频生成工作台' }}</h1>
          <p>{{ task?.user_input || '从创意、分镜到成片，跟随每一步制作进度。' }}</p>
          <div class="tags">
            <span v-if="task?.duration">{{ task.duration }} 秒</span>
            <span v-if="task?.ratio">{{ task.ratio }}</span>
            <span v-if="task?.style">{{ task.style }}</span>
            <span>{{ scenes.length }} 个镜头</span>
          </div>
        </div>
        <div class="hero-progress">
          <span class="eyebrow">制作进度</span>
          <strong>{{ progress }}<small>%</small></strong>
          <div class="meter" role="progressbar" aria-label="制作进度" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
            <div :style="{ width: progress + '%' }"></div>
          </div>
          <RouterLink v-if="status === 'success'" class="primary" :to="'/preview/' + taskId">观看成片 →</RouterLink>
          <button v-else-if="status === 'failed' || task?.status === 'created'" class="primary" :disabled="starting" @click="resume">
            {{ starting ? '正在启动…' : status === 'failed' ? '继续生成' : '开始生成' }}
          </button>
        </div>
      </section>

      <div v-if="failure || connectionIssue" class="notice" role="status">{{ failure || connectionIssue }}</div>

      <div class="studio-grid">
        <aside class="panel workflow">
          <div class="section-heading">
            <span class="eyebrow">WORKFLOW</span>
            <h2>制作流程</h2>
            <p>选择步骤查看它的作用与产物。</p>
          </div>
          <ol class="steps">
            <li v-for="(step, index) in steps" :key="step.key">
              <button :class="[stepState(step), { selected: active.key === step.key }]" :aria-pressed="active.key === step.key" @click="selected = step.key">
                <span class="step-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <span class="step-name">{{ step.title }}</span>
                <span class="step-status">{{ stateLabels[stepState(step)] }}</span>
              </button>
            </li>
          </ol>
        </aside>

        <div class="workspace">
          <section class="panel stage" aria-labelledby="stage-title">
            <div class="stage-header">
              <div>
                <span class="eyebrow">STEP {{ String(steps.indexOf(active) + 1).padStart(2, '0') }} / {{ steps.length }}</span>
                <h2 id="stage-title">{{ active.title }}</h2>
                <p>{{ active.description }}</p>
              </div>
              <span class="badge" :class="stepState(active)">{{ stateLabels[stepState(active)] }}</span>
            </div>

            <div v-if="active.key === 'TaskPlannerAgent' && plan" class="stage-output">
              <p v-if="plan.optimized_brief">{{ plan.optimized_brief }}</p>
              <ol v-if="plan.plot_beats?.length" class="beats">
                <li v-for="(beat, index) in plan.plot_beats" :key="index">{{ typeof beat === 'string' ? beat : beat.description || beat.beat_name }}</li>
              </ol>
            </div>
            <div v-else-if="active.key === 'ScriptAgent' && script" class="stage-output">
              <h3>{{ script.title || '视频剧本' }}</h3>
              <p class="script">{{ script.content }}</p>
            </div>
            <div v-else-if="active.key === 'QualityAgent' && report" class="stage-output">
              <p>{{ report.semantic_review?.verdict === 'pass' ? '内容覆盖检查通过' : report.semantic_review?.verdict === 'needs_review' ? '部分内容建议复核' : '媒体与时间轴检查通过' }}</p>
              <ul v-if="Array.isArray(report.semantic_review?.issues)" class="beats">
                <li v-for="(issue, i) in report.semantic_review.issues" :key="i">{{ issue }}</li>
              </ul>
              <p v-if="report.visual_review?.verdict">画面抽检：{{ ({ pass: '已通过', needs_review: '建议复核', incomplete: '部分未完成', disabled: '未配置视觉模型' })[report.visual_review.verdict] }}</p>
              <ul v-if="report.visual_review?.issues?.length" class="beats">
                <li v-for="(item, i) in report.visual_review.issues" :key="i">{{ item.time.toFixed(1) }} 秒 · {{ item.issue }}</li>
              </ul>
              <p v-if="report.static_fallbacks?.length">使用静态画面的镜头 {{ report.static_fallbacks.join('、') }}</p>
            </div>
            <div v-else-if="['EditorAgent', 'ExportAgent'].includes(active.key) && finalVideo" class="stage-output">
              <video class="final-video" :src="finalVideo" controls preload="metadata" playsinline></video>
              <div class="links"><a :href="finalVideo" download>下载成片</a><a v-if="task?.metadata?.cover_path" :href="mediaUrl(task.metadata.cover_path)" download>下载封面</a><a v-if="subtitleFile" :href="subtitleFile" download>下载字幕</a></div>
            </div>
            <div v-else class="stage-output summary">
              <span class="signal" :class="stepState(active)"></span>
              {{ stageSummary }}
              <a v-if="active.key === 'SubtitleAgent' && subtitleFile" :href="subtitleFile" download>下载 SRT</a>
            </div>
          </section>

          <section class="panel timeline" aria-labelledby="timeline-title">
            <div class="section-heading timeline-heading">
              <div><span class="eyebrow">SHOT TIMELINE</span><h2 id="timeline-title">分镜时间线</h2></div>
              <span class="badge neutral">{{ readyCount }} / {{ scenes.length }} 镜头就绪</span>
            </div>
            <div v-if="!scenes.length" class="empty">分镜生成后会出现在这里。首帧、配音和片段就绪后会自动更新。</div>
            <template v-for="(scene, i) in scenes" :key="scene.scene_id">
              <div v-if="task?.generation_mode === 'paired_5s' && i % 2 === 0" class="story-section-label">
                段落 {{ Math.floor(i / 2) + 1 }} · {{ shotTimes[i] }}
              </div>
              <article class="shot">
              <div class="shot-media">
                <video v-if="scene.video_path" :src="mediaUrl(scene.video_path)" controls preload="metadata" playsinline></video>
                <img v-else-if="scene.image_path" :src="mediaUrl(scene.image_path)" :alt="'镜头 ' + (i + 1) + ' 的首帧'" loading="lazy">
                <div v-else class="frame-placeholder"><span>◫</span><small>等待画面</small></div>
              </div>
              <div class="shot-body">
                <div class="shot-title"><h3>镜头 {{ String(i + 1).padStart(2, '0') }}</h3><span>{{ shotTimes[i] }} · {{ scene.duration }} 秒</span></div>
                <p v-if="scene.visual_description">{{ scene.visual_description }}</p>
                <p v-if="scene.voiceover" class="narration">{{ scene.voiceover }}</p>
                <div class="shot-tags">
                  <span v-if="scene.status === 'static_fallback'" class="badge failed">静态替代</span>
                  <span v-else-if="scene.video_path" class="badge success">片段已生成</span>
                  <span v-else-if="scene.image_path" class="badge neutral">首帧就绪</span>
                  <span v-if="scene.audio_path" class="badge neutral">配音就绪</span>
                </div>
                <audio v-if="scene.audio_path" :src="mediaUrl(scene.audio_path)" :aria-label="'镜头 ' + (i + 1) + ' 配音'" controls preload="none"></audio>
                <details v-if="scene.motion_beats?.length || scene.image_prompt || scene.video_prompt">
                  <summary>动作节拍与生成提示</summary>
                  <ol v-if="scene.motion_beats?.length" class="beats">
                    <li v-for="(beat, j) in scene.motion_beats" :key="j">
                      {{ typeof beat === 'string' ? beat : (beat.start ?? '—') + '–' + (beat.end ?? '—') + ' 秒 · ' + (beat.action || '') }}
                    </li>
                  </ol>
                  <p v-if="scene.image_prompt"><strong>首帧</strong> {{ scene.image_prompt }}</p>
                  <p v-if="scene.video_prompt"><strong>运动</strong> {{ scene.video_prompt }}</p>
                </details>
              </div>
              </article>
            </template>
          </section>
        </div>
      </div>
    </main>
  </BasicLayout>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import BasicLayout from '../layouts/BasicLayout.vue'
import { videoApi } from '../api/video'

const steps = [
  { key: 'TaskPlannerAgent', title: '创意规划', description: '理解你的描述，确定题材、时长、风格和内容结构。' },
  { key: 'ScriptAgent', title: '剧本创作', description: '写出视频的开头、关键信息、旁白和结尾。' },
  { key: 'StoryboardAgent', title: '分镜设计', description: '把剧本拆成镜头，为每个镜头安排时长和可见画面。' },
  { key: 'DialoguePolishAgent', title: '台词润色', description: '调整台词和旁白，让它们听起来自然并适合镜头时长。' },
  { key: 'PromptAgent', title: '动作与提示词', description: '为每个镜头描述首帧、动作节拍和前后衔接。' },
  { key: 'ImageAgent', title: '首帧绘制', description: '生成用于构图的首帧。连续动作也可以沿用前一镜头的尾帧。' },
  { key: 'VoiceAgent', title: '配音生成', description: '为各镜头生成配音，检查实际朗读时长。此步骤可与首帧绘制并行。' },
  { key: 'VideoAgent', title: '镜头生成', description: '把首帧和动作描述变成视频片段。片段保存后会出现在下方。' },
  { key: 'SubtitleAgent', title: '字幕排布', description: '把每句台词放到对应的时间点，生成字幕文件。' },
  { key: 'EditorAgent', title: '成片剪辑', description: '按时间线组合画面、配音和字幕。' },
  { key: 'QualityAgent', title: '成片质检', description: '检查时长、音轨、字幕和素材完整性，并记录内容覆盖结果。' },
  { key: 'ExportAgent', title: '导出视频', description: '整理成片和字幕，提供播放与下载。' }
]
const stateLabels = { success: '已完成', running: '进行中', failed: '失败', waiting: '待开始' }
const route = useRoute()
const taskId = computed(() => String(route.params.id || ''))
const task = ref(null)
const script = ref(null)
const scenes = ref([])
const selected = ref('')
const starting = ref(false)
const connectionIssue = ref('')
const plan = computed(() => task.value?.metadata?.task_plan)
const report = computed(() => task.value?.metadata?.quality_report)
const status = computed(() => task.value?.status || 'loading')
const statusLabel = computed(() => ({ success: '已交付', failed: '生成中断', created: '等待启动', loading: '正在连接' }[status.value] || '正在制作'))
const progress = computed(() => Math.max(0, Math.min(100, Number(task.value?.progress) || 0)))
const failure = computed(() => status.value === 'failed' ? task.value?.metadata?.last_error || '生成中断，可以从当前进度继续。' : '')
const mediaUrl = path => path ? '/' + path.replace(/^backend\//, '').replace(/^\/+/, '') : ''
const finalVideo = computed(() => mediaUrl(task.value?.metadata?.video_path || task.value?.video_path))
const subtitleFile = computed(() => mediaUrl(task.value?.metadata?.subtitle_path || task.value?.subtitle_path))
const shotTimes = computed(() => {
  let elapsed = 0
  const timestamp = seconds => String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0')
  return scenes.value.map(scene => {
    const start = elapsed
    elapsed += Number(scene.duration) || 0
    return timestamp(start) + '–' + timestamp(elapsed)
  })
})
const readyCount = computed(() => scenes.value.filter(scene =>
  scene.video_path || (scene.image_path && (!scene.need_dynamic_video || scene.status === 'static_fallback'))
).length)
const active = computed(() => steps.find(step => step.key === selected.value)
  || steps.find(step => step.key === (status.value === 'success' ? 'ExportAgent' : task.value?.current_agent))
  || steps[0])

function stepState(step) {
  if (status.value === 'success') return 'success'
  const saved = task.value?.metadata?.stage_states?.[step.key]
  if (saved && (saved !== 'running' || status.value !== 'failed')) return saved
  if (task.value?.current_agent === step.key) return status.value === 'failed' ? 'failed' : 'running'
  const completed = {
    TaskPlannerAgent: Boolean(plan.value), ScriptAgent: Boolean(script.value),
    StoryboardAgent: scenes.value.length > 0,
    DialoguePolishAgent: scenes.value.length > 0 && scenes.value.every(s => s.dialogue_polished),
    PromptAgent: scenes.value.length > 0 && scenes.value.every(s => s.motion_beats?.length),
    SubtitleAgent: Boolean(subtitleFile.value), EditorAgent: Boolean(finalVideo.value),
    QualityAgent: Boolean(report.value)
  }
  return completed[step.key] ? 'success' : 'waiting'
}

const stageSummary = computed(() => {
  const count = field => scenes.value.filter(scene => scene[field]).length
  const summaries = {
    StoryboardAgent: `已保存 ${scenes.value.length} 个分镜`,
    DialoguePolishAgent: `已润色 ${count('dialogue_polished')} 个镜头`,
    PromptAgent: `已有 ${count('motion_beats')} 个镜头具备动作节拍`,
    ImageAgent: `已有 ${count('image_path')} 张首帧`,
    VoiceAgent: `已有 ${count('audio_path')} 段配音`,
    VideoAgent: `已有 ${count('video_path')} 个动态片段`
  }
  return summaries[active.value.key] || stateLabels[stepState(active.value)]
})

let refreshNow = () => {}
watch(taskId, (id, _, onCleanup) => {
  let disposed = false
  let timer
  let refreshing = false
  task.value = null
  scenes.value = []
  script.value = null
  selected.value = ''
  connectionIssue.value = ''

  async function refresh() {
    if (!id || disposed || refreshing) return
    clearTimeout(timer)
    refreshing = true
    try {
      const [detail, storyboard, draft] = await Promise.allSettled([
        videoApi.getTask(id), videoApi.getStoryboard(id),
        script.value || (task.value?.progress || 0) < 30 ? Promise.resolve(script.value) : videoApi.getScript(id)
      ])
      if (disposed) return
      if (detail.status === 'fulfilled') {
        task.value = detail.value
        connectionIssue.value = ''
      } else {
        connectionIssue.value = '暂时无法读取进度，页面会自动重试。'
      }
      if (storyboard.status === 'fulfilled' && Array.isArray(storyboard.value)) {
        scenes.value = [...storyboard.value].sort((a, b) => a.scene_index - b.scene_index)
      }
      if (draft.status === 'fulfilled') script.value = draft.value
      if (!script.value && detail.status === 'fulfilled' && ['success', 'failed'].includes(status.value) && progress.value >= 30) {
        const savedScript = await videoApi.getScript(id).catch(() => null)
        if (!disposed) script.value = savedScript
      }
    } finally {
      refreshing = false
      if (!disposed && (!['success', 'failed'].includes(status.value) || connectionIssue.value)) {
        timer = setTimeout(refresh, 2000)
      }
    }
  }

  refreshNow = refresh
  refresh()
  onCleanup(() => { disposed = true; clearTimeout(timer) })
}, { immediate: true })

async function resume() {
  const id = taskId.value
  starting.value = true
  try {
    await videoApi.runPipeline(id, true)
    if (taskId.value === id) {
      task.value = { ...task.value, status: 'starting' }
      selected.value = ''
      refreshNow()
    }
  } catch (error) {
    if (taskId.value === id) connectionIssue.value = error.message || '启动失败'
  } finally {
    starting.value = false
  }
}
</script>

<style scoped>
.studio { --ink: #192131; --muted: #647184; --line: #e2e8f0; --accent: #baf873; max-width: 1480px; margin: auto; color: var(--ink); }
.studio-hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(165px, 235px); gap: 32px; align-items: center; padding: clamp(24px, 4vw, 52px); margin-bottom: 24px; border-radius: 24px; color: #f6f8ff; background: radial-gradient(ellipse at 94% 10%, #38506a 0%, transparent 55%), linear-gradient(125deg, #101b30, #20304b); box-shadow: 0 15px 32px #192c4722; overflow: hidden; }
.eyebrow { display: inline-flex; gap: 9px; align-items: center; font-size: 11px; font-weight: 750; letter-spacing: .15em; color: #4b627b; }
.studio-hero .eyebrow { color: #b8c7db; }
.signal { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: #94a3b8; }
.signal.running, .studio-hero .signal:not(.failed):not(.created) { background: var(--accent); box-shadow: 0 0 12px #baf8737a; }
.signal.failed { background: #fb8f91; }
.signal.success { background: #318260; }
.hero-divider { height: 12px; width: 1px; background: #8997ad77; }
h1, h2, h3, p { margin: 0; }
.studio-hero h1 { color: #f6f8ff; margin: 12px 0 9px; font-size: clamp(26px, 3.5vw, 46px); letter-spacing: -.04em; overflow-wrap: anywhere; }
.studio-hero p { color: #c2d0e4; line-height: 1.7; overflow-wrap: anywhere; }
.tags, .shot-tags, .links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.tags span { padding: 5px 10px; border: 1px solid #e9f0ff28; border-radius: 7px; color: #e1eaf8; font-size: 12px; background: #ffffff09; }
.hero-progress { border-left: 1px solid #a8bdd546; padding-left: 28px; }
.hero-progress strong { display: block; font-size: 54px; line-height: 1.14; font-variant-numeric: tabular-nums; color: var(--accent); letter-spacing: -.055em; }
.hero-progress small { font-size: 21px; margin-left: 3px; }
.meter { height: 6px; border-radius: 20px; overflow: hidden; margin-top: 10px; background: #ffffff23; }
.meter div { height: 100%; background: var(--accent); border-radius: inherit; transition: width .35s ease; }
.primary { display: inline-block; margin-top: 15px; color: #172616; background: var(--accent); border: 0; border-radius: 8px; padding: 9px 13px; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
.primary:disabled { opacity: .6; cursor: wait; }
.studio-grid { display: grid; grid-template-columns: minmax(210px, 265px) minmax(0, 1fr); gap: 22px; align-items: start; }
@media (width > 980px) {
  .studio-grid > .workflow {
    position: sticky;
    top: 16px;
    max-height: calc(100dvh - 96px);
    display: flex;
    flex-direction: column;
  }
  .workflow .steps {
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior-y: contain;
  }
}
.panel { background: white; border: 1px solid var(--line); border-radius: 18px; overflow: hidden; box-shadow: 0 3px 12px #172a4510; }
.section-heading, .stage-header { padding: 21px 23px 16px; }
.section-heading h2, .stage-header h2 { font-size: 22px; margin-top: 5px; }
.section-heading p, .stage-header p { color: var(--muted); margin-top: 7px; line-height: 1.55; font-size: 13px; }
.steps { list-style: none; padding: 0 10px 12px; margin: 0; }
.steps button { display: grid; grid-template-columns: 30px 1fr auto; width: 100%; gap: 9px; align-items: center; text-align: left; background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 9px; color: var(--ink); font: inherit; cursor: pointer; }
.steps button:hover { background: #f2f6fb; }
.steps .selected { background: #eaf1fc; border-color: #d7e5fa; }
.step-index { display: grid; place-items: center; width: 29px; height: 29px; border-radius: 7px; background: #edf1f6; font-size: 11px; font-weight: 750; color: #58677d; }
.steps .success .step-index { background: #e5f5eb; color: #276342; }
.steps .running .step-index { background: #cde4ff; color: #1e5188; }
.step-name { font-size: 13px; font-weight: 650; }
.step-status { font-size: 11px; color: var(--muted); white-space: nowrap; }
.stage-header, .timeline-heading { display: flex; justify-content: space-between; gap: 15px; align-items: flex-start; }
.stage-output { margin: 0 23px 20px; padding: 14px 16px; background: #f4f7fb; border-radius: 10px; line-height: 1.65; overflow-wrap: anywhere; }
.stage-output p + p, .stage-output h3 + p { margin-top: 8px; }
.stage-output.summary { display: flex; gap: 9px; align-items: center; font-size: 13px; color: #485a72; }
.script { white-space: pre-wrap; max-height: 330px; overflow: auto; }
.badge { display: inline-flex; align-items: center; flex-shrink: 0; white-space: nowrap; padding: 4px 8px; border-radius: 6px; background: #edf1f6; color: #526077; font-size: 11px; font-weight: 700; }
.badge.running { background: #dcecff; color: #1c5089; }
.badge.success { background: #e5f5eb; color: #276342; }
.badge.failed, .notice { background: #fff0eb; color: #8c382c; }
.notice { padding: 14px 17px; border-radius: 10px; margin-bottom: 18px; overflow-wrap: anywhere; }
.timeline { margin-top: 18px; }
.empty { padding: 24px; color: var(--muted); border-top: 1px dashed var(--line); }
.shot { display: grid; grid-template-columns: minmax(125px, 185px) minmax(0, 1fr); gap: 16px; padding: 16px 23px; border-top: 1px solid var(--line); }
.shot-media { align-self: start; display: grid; place-items: center; aspect-ratio: 4 / 3; background: #17253b; border-radius: 9px; overflow: hidden; }
.shot-media video, .shot-media img { display: block; width: 100%; height: 100%; max-height: 184px; object-fit: contain; }
.frame-placeholder { display: grid; place-items: center; gap: 7px; color: #afbed3; }
.frame-placeholder span { font-size: 26px; }
.shot-title { display: flex; gap: 12px; align-items: center; margin-bottom: 7px; }
.shot-title h3 { font-size: 15px; }
.shot-title span, .shot-body p { color: var(--muted); font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.shot-body .narration { margin-top: 7px; border-left: 2px solid #a2bbdf; padding-left: 9px; color: #3d4b62; }
.shot-tags { margin-top: 9px; }
.shot audio { width: min(100%, 320px); height: 35px; margin-top: 10px; }
.shot details { margin-top: 10px; font-size: 13px; color: #405a7a; }
.shot summary { cursor: pointer; }
.beats { padding-left: 21px; margin: 8px 0; line-height: 1.65; }
.final-video { display: block; width: 100%; max-height: 420px; background: #132034; border-radius: 8px; }
.links a, .summary a { color: #234f84; font-weight: 700; }
button:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid #326eb1; outline-offset: 3px; }
@media (max-width: 980px) { .studio-grid { grid-template-columns: 1fr; } .steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); } }
@media (max-width: 620px) { .studio-hero, .shot { grid-template-columns: 1fr; gap: 14px; } .hero-progress { padding-left: 0; border-left: none; } .shot-media { aspect-ratio: 16 / 9; } .section-heading, .stage-header, .shot { padding-left: 14px; padding-right: 14px; } .stage-output { margin-left: 14px; margin-right: 14px; } }
@media (max-width: 620px) {
  .steps { display: flex; overflow-x: auto; scroll-snap-type: x proximity; gap: 5px; }
  .steps li { flex: 0 0 auto; scroll-snap-align: start; }
  .steps button { grid-template-columns: 29px auto; }
  .step-status { grid-column: 2; }
}
@media (prefers-reduced-motion: reduce) { .meter div { transition: none; } }
.story-section-label { margin:14px 20px 0; padding:9px 12px; border-radius:8px; background:#eef2ff; color:#3730a3; font-weight:650; font-size:13px; }
</style>
