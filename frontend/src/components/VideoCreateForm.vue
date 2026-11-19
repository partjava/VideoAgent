<template>
  <el-card class="form-card" shadow="never">
    <el-alert
      v-if="prefillNotice"
      :title="prefillNotice"
      type="success"
      show-icon
      closable
      style="margin-bottom: 16px"
      @close="prefillNotice = ''"
    />
    <el-form :model="form" label-position="top" @submit.prevent="handleSubmit">
      <el-form-item label="视频生成需求" required>
        <el-input
          v-model="form.user_input"
          type="textarea"
          :rows="form.generation_mode === 'storyboard_flow' ? 6 : 4"
          :placeholder="form.generation_mode === 'storyboard_flow'
            ? '可输入一句话主题，或直接粘贴多单元剧情梗概/角色设定/分镜模板（不卡逐秒，系统会先生成浅灰纯底角色设定卡，再按单元交替景别自由生成）。'
            : '用一句话描述你要的视频，例如：用20秒讲清楚猫为什么会踩奶。'"
          class="prompt-textarea"
        />
      </el-form-item>

      <el-form-item class="character-select-item">
        <template #label>
          <div class="label-with-link">
            <span>出镜角色（可选，从角色资产库选择已确认角色）</span>
            <router-link to="/characters" class="manage-link">去管理角色资产库 →</router-link>
          </div>
        </template>
        <el-select
          v-model="form.character_ids"
          multiple
          filterable
          clearable
          placeholder="不选则由 AI 自动构思角色；选择后将沿用角色三视图定妆照与专属配音"
          class="full-width"
        >
          <el-option
            v-for="char in confirmedCharacters"
            :key="char.character_id"
            :label="`${char.name}（v${char.revision || 1}）`"
            :value="char.character_id"
          >
            <span>{{ char.name }}</span>
            <span class="char-option-meta">
              · v{{ char.revision || 1 }} · {{ char.signature_clothing || char.face_dna || '已确认定妆照' }}
            </span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="画面风格" class="style-select">
        <el-radio-group v-model="form.style" class="style-group">
          <el-radio-button value="短剧">真人实拍</el-radio-button>
          <el-radio-button value="漫剧">漫画插画</el-radio-button>
        </el-radio-group>
        <p class="style-help">{{ form.style === '短剧'
          ? '真人演员、真实五官比例和皮肤纹理，摄影布光与实景材质。'
          : '绘制的人物与场景，使用线条、色块或笔触表现，保持全片画风统一。' }}</p>
      </el-form-item>

      <div class="form-row-three">
        <el-form-item label="排片导演模式" class="form-item-third">
          <el-select v-model="form.generation_mode" class="full-width">
            <el-option label="AI 自主导演（逐秒动作链 · 4–12s）" value="auto_director" />
            <el-option label="剧组单元自由流（不逐秒 · 角色设定卡+多镜交替）" value="storyboard_flow" />
            <el-option label="原生 10 秒动态镜头" value="native_10s" />
            <el-option label="双 5 秒连续动作镜头" value="paired_5s" />
            <el-option label="关键镜头动态（省额度）" value="key_scenes" />
          </el-select>
        </el-form-item>

        <el-form-item :label="form.generation_mode === 'native_10s' ? '目标时长（10秒倍数）' : '目标时长范围'" class="form-item-third">
          <template v-if="form.generation_mode === 'native_10s'">
            <el-select v-model="form.duration" placeholder="选择预计时长" class="full-width">
              <el-option label="自动（10 秒倍数）" :value="0" />
              <el-option label="10 秒测试" :value="10" />
              <el-option label="20 秒" :value="20" />
              <el-option label="30 秒" :value="30" />
              <el-option label="60 秒" :value="60" />
              <el-option label="90 秒" :value="90" />
            </el-select>
          </template>
          <template v-else>
            <el-select v-model="durationPreset" class="full-width">
              <el-option label="自动（AI 按剧情节奏决定）" value="auto" />
              <el-option label="15–30 秒（快节奏测试）" value="15-30" />
              <el-option label="30–60 秒（标准短剧 · 推荐）" value="30-60" />
              <el-option label="45–90 秒（中长强冲突）" value="45-90" />
              <el-option label="60–120 秒（完整叙事）" value="60-120" />
              <el-option label="自定义范围" value="custom" />
            </el-select>
            <div v-if="durationPreset === 'custom'" class="custom-range-row">
              <el-input-number v-model="form.duration_min" :min="10" :max="180" :step="5" size="small" controls-position="right" />
              <span>~</span>
              <el-input-number v-model="form.duration_max" :min="10" :max="180" :step="5" size="small" controls-position="right" />
              <span>秒</span>
            </div>
          </template>
        </el-form-item>

        <el-form-item label="画面比例" class="form-item-third">
          <el-radio-group v-model="form.ratio" class="ratio-group">
            <el-radio-button label="9:16">9:16 竖屏</el-radio-button>
            <el-radio-button label="16:9">16:9 横屏</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>

      <div class="usage-estimate-bar">
        <span v-if="form.generation_mode === 'storyboard_flow'">不强制逐秒拆碎动作：未选角色库时自动先绘纯灰底 4 视图角色设定卡，按 15 秒情感单元交替景别视角与阶段光影生成连续长镜（支持静默情绪特写与闪白切场）。</span>
        <span v-else-if="form.generation_mode === 'auto_director'">AI 自主导演规划镜头节奏（主镜头 8–12 秒、反应/钩子 4–7 秒），配音自适应时间轴并在质检后自动精修重拍瑕疵镜头。</span>
        <span v-else-if="form.generation_mode === 'native_10s'">每段固定生成 10 秒动态视频，完成后自动拼接。</span>
        <span v-else-if="form.generation_mode === 'paired_5s'">每段由两个 5 秒连续动作镜头拼接而成。</span>
        <span v-else>关键镜头生成动态视频，其余镜头辅以运镜衔接。</span>
        <span class="estimate-badge">预计消耗：约 {{ estimatedUsage.images }} 张图 + {{ estimatedUsage.videos }} 段视频（共 {{ estimatedUsage.total }} 次生成操作）</span>
        <p v-if="limitWarning" class="limit-warning">
          ⚠️ {{ limitWarning }}
          <router-link to="/settings" class="limit-settings-link">去「API 配置」调高或设为不限制 →</router-link>
        </p>
      </div>

      <div class="workflow-switches">
        <el-checkbox v-model="form.pre_shoot_review">
          开拍前预审首帧与配音（生成完分镜图与配音后暂停，支持单镜试拍{{ form.generation_mode === 'storyboard_flow' ? ' · 建议勾选以先看 4 视图设定卡' : '' }}）
        </el-checkbox>
      </div>

      <details class="advanced-options">
        <summary>高级设置与额度控制</summary>
        <div class="form-row-three">
          <el-form-item label="片头标题（可选）" class="form-item-third">
            <el-input v-model="form.intro_title" maxlength="60" placeholder="输入需要叠加在片头的文字" />
          </el-form-item>
          <el-form-item label="字幕样式" class="form-item-third">
            <el-select v-model="form.subtitle_style" class="full-width">
              <el-option label="标准字幕" value="standard" />
              <el-option label="国风诗词" value="poetry" />
            </el-select>
          </el-form-item>
          <el-form-item label="背景音乐情绪" class="form-item-third">
            <el-select v-model="form.bgm_preset" class="full-width">
              <el-option label="自动匹配题材" value="auto" />
              <el-option label="校园/温暖钢琴" value="warm_campus" />
              <el-option label="悬疑强戏剧底噪" value="suspense" />
              <el-option label="历史/燃向进行曲" value="heroic" />
              <el-option label="无背景音乐" value="none" />
            </el-select>
          </el-form-item>
        </div>
        <div class="form-row-three">
          <el-form-item label="生成操作额度上限（可选）" class="form-item-third">
            <el-input
              v-model="form.generation_limit"
              type="number"
              min="1"
              max="10000"
              placeholder="留空不设上限"
            />
          </el-form-item>
          <el-form-item label="质检自动重拍精修" class="form-item-third">
            <el-checkbox v-model="form.auto_heal">
              开启瑕疵镜头自愈重拍（最多 2 镜）
            </el-checkbox>
          </el-form-item>
        </div>
      </details>

      <div class="action-container">
        <el-button
          type="primary"
          class="submit-button"
          :loading="loading"
          @click="handleSubmit"
        >
          开始生成视频
        </el-button>
      </div>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { charactersApi } from '../api/characters'
import { videoApi } from '../api/video'

defineProps({
  loading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['submit'])

const confirmedCharacters = ref([])
const capabilities = ref(null)
const prefillNotice = ref('')
const durationPreset = ref('auto')

const RANGE_PRESETS = {
  '15-30': [15, 30],
  '30-60': [30, 60],
  '45-90': [45, 90],
  '60-120': [60, 120],
}

const form = reactive({
  user_input: '',
  duration: 0,
  duration_min: 30,
  duration_max: 60,
  style: '短剧',
  ratio: '9:16',
  generation_mode: 'auto_director',
  auto_heal: true,
  max_heal_attempts: 2,
  intro_title: '',
  subtitle_style: 'standard',
  bgm_preset: 'auto',
  pre_shoot_review: false,
  generation_limit: '',
  character_ids: []
})

const activeDurationBounds = computed(() => {
  if (form.generation_mode === 'native_10s') return null
  if (durationPreset.value === 'auto') return null
  if (durationPreset.value === 'custom') {
    const a = Number(form.duration_min || 30)
    const b = Number(form.duration_max || 60)
    return [Math.min(a, b), Math.max(a, b)]
  }
  return RANGE_PRESETS[durationPreset.value] || null
})

onMounted(async () => {
  try {
    const list = await charactersApi.list()
    const items = Array.isArray(list) ? list : list?.characters || []
    confirmedCharacters.value = items.filter(c => c && c.confirmed && !c.archived)
  } catch {
    confirmedCharacters.value = []
  }
  try {
    capabilities.value = await videoApi.getCapabilities()
  } catch {
    capabilities.value = null
  }
  try {
    const rawPrefill = localStorage.getItem('videoagent_prefill_task')
    if (rawPrefill) {
      const prefill = JSON.parse(rawPrefill)
      localStorage.removeItem('videoagent_prefill_task')
      if (prefill && typeof prefill === 'object') {
        if (prefill.user_input) form.user_input = String(prefill.user_input)
        if (prefill.style) form.style = String(prefill.style)
        if (prefill.generation_mode) form.generation_mode = String(prefill.generation_mode)
        if (prefill.duration_min && prefill.duration_max) {
          const lo = Number(prefill.duration_min)
          const hi = Number(prefill.duration_max)
          const key = `${lo}-${hi}`
          if (RANGE_PRESETS[key]) {
            durationPreset.value = key
          } else {
            durationPreset.value = 'custom'
            form.duration_min = lo
            form.duration_max = hi
          }
        } else if (prefill.duration !== undefined && prefill.duration !== null) {
          form.duration = Number(prefill.duration)
        }
        if (prefill.intro_title) form.intro_title = String(prefill.intro_title)
        if (Array.isArray(prefill.character_ids) && prefill.character_ids.length) {
          const confirmedIds = new Set(confirmedCharacters.value.map(c => c.character_id))
          form.character_ids = prefill.character_ids.filter(id => confirmedIds.has(id))
        }
        prefillNotice.value = `已从「短剧导演工坊」自动载入剧本：《${prefill.title || '工坊剧本'}》。确认画面比例与模式后，点击下方「开始生成视频」即可开拍。`
      }
    }
  } catch {
    // 忽略异常预填数据
  }
})

const expectedSeconds = computed(() => {
  if (form.generation_mode !== 'native_10s') {
    const bounds = activeDurationBounds.value
    if (bounds) return Math.round((bounds[0] + bounds[1]) / 2)
    return form.generation_mode === 'storyboard_flow' ? 60 : 30
  }
  return form.duration > 0 ? form.duration : 30
})

const estimatedUsage = computed(() => {
  const seconds = expectedSeconds.value
  if (form.generation_mode === 'storyboard_flow') {
    const clips = Math.max(2, Math.round(seconds / 4))
    const images = clips + (Array.isArray(form.character_ids) && form.character_ids.length > 0 ? 0 : 2)
    return { images, videos: clips, total: images + clips }
  }
  let clips = Math.max(1, Math.ceil(seconds / 10))
  if (form.generation_mode === 'auto_director') {
    clips = Math.max(2, Math.round(seconds / 9))
  } else if (form.generation_mode === 'paired_5s') {
    clips = Math.max(1, Math.ceil(seconds / 5))
  }
  return { images: clips, videos: clips, total: clips * 2 }
})

const limitWarning = computed(() => {
  const caps = capabilities.value
  if (!caps) return ''
  const est = estimatedUsage.value
  const seconds = expectedSeconds.value
  const taskGenLimit = String(form.generation_limit ?? '').trim()
    ? Number.parseInt(form.generation_limit, 10)
    : 0
  const maxImages = caps.max_images_per_task > 0
    ? (taskGenLimit > 0 ? Math.max(caps.max_images_per_task, taskGenLimit) : caps.max_images_per_task)
    : 0
  const reasons = []
  if (maxImages > 0 && est.images > maxImages) {
    reasons.push(`预计首帧/设定图约 ${est.images} 张 > 上限 ${maxImages} 张`)
  }
  if (caps.max_dynamic_scenes_per_task > 0 && est.videos > caps.max_dynamic_scenes_per_task) {
    reasons.push(`预计动态镜约 ${est.videos} 段 > 上限 ${caps.max_dynamic_scenes_per_task} 段`)
  }
  if (caps.max_video_seconds_per_task > 0 && seconds > caps.max_video_seconds_per_task) {
    reasons.push(`目标时长 ${seconds} 秒 > 上限 ${caps.max_video_seconds_per_task} 秒`)
  }
  if (!reasons.length) return ''
  return `${reasons.join('；')}。可直接调高额度，或缩短时长 / 拆分成多集连载运行。`
})

const handleSubmit = () => {
  if (!form.user_input.trim()) {
    return
  }
  const limitNum = String(form.generation_limit ?? '').trim()
    ? Number.parseInt(form.generation_limit, 10)
    : null
  const bounds = activeDurationBounds.value
  emit('submit', {
    ...form,
    duration: form.generation_mode === 'native_10s' ? (form.duration || null) : null,
    duration_min: bounds ? bounds[0] : null,
    duration_max: bounds ? bounds[1] : null,
    style: form.style || null,
    generation_limit: Number.isInteger(limitNum) && limitNum > 0 ? limitNum : null,
    character_ids: Array.isArray(form.character_ids) ? form.character_ids : []
  })
}
</script>

<style scoped>
.form-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  padding: 12px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.04);
}
.label-with-link {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: 12px;
}
.manage-link {
  font-size: 13px;
  color: #2563eb;
  text-decoration: none;
  font-weight: 500;
}
.manage-link:hover {
  text-decoration: underline;
}
.char-option-meta {
  color: #64748b;
  font-size: 12px;
  margin-left: 6px;
}
.style-help { color:#475569; font-size:13px; line-height:1.6; width:100%; margin:8px 0 0; }
.usage-estimate-bar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin:12px 0; padding:10px 14px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; color:#475569; font-size:13px; }
.estimate-badge { font-weight:600; color:#1e40af; background:#eff6ff; padding:4px 12px; border-radius:999px; }
.workflow-switches { margin:8px 0 12px; }
.advanced-options { margin:16px 0; padding:12px 14px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; }
.advanced-options summary { cursor:pointer; margin-bottom:12px; font-weight:600; color:#334155; }

.prompt-textarea :deep(.el-textarea__inner) {
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  border-radius: 8px;
  padding: 12px 16px;
  background-color: #f8fafc;
}

.prompt-textarea :deep(.el-textarea__inner:focus) {
  background-color: #ffffff;
}

.style-select {
  margin-top: 12px;
}

.style-group {
  display: flex;
  width: 100%;
}

.style-group :deep(.el-radio-button) {
  flex: 1;
}

.style-group :deep(.el-radio-button__inner) {
  width: 100%;
}

.form-row-three {
  display: flex;
  gap: 24px;
  margin-top: 12px;
}

.form-item-third {
  flex: 1;
}

.full-width {
  width: 100%;
}

.ratio-group {
  display: flex;
  width: 100%;
}

.ratio-group :deep(.el-radio-button) {
  flex: 1;
}

.ratio-group :deep(.el-radio-button__inner) {
  width: 100%;
}

.action-container {
  display: flex;
  justify-content: center;
  margin-top: 32px;
}

.submit-button {
  width: 240px;
  height: 46px;
  font-size: 16px;
  font-weight: 600;
  border-radius: 8px;
  border: none;
  background: #2563eb;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.22);
  transition: all 0.2s ease;
}

.submit-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 14px rgba(37, 99, 235, 0.28);
  background: #1d4ed8;
}
.limit-warning {
  width: 100%;
  margin: 6px 0 0;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fffbeb;
  border: 1px solid #fcd34d;
  color: #92400e;
  font-size: 12px;
  line-height: 1.5;
}
.limit-settings-link {
  margin-left: 6px;
  color: #2563eb;
  font-weight: 600;
  text-decoration: none;
}
.limit-settings-link:hover {
  text-decoration: underline;
}
.custom-range-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.submit-button:active {
  transform: translateY(1px);
}

@media (max-width: 760px) {
  .form-row-three {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
