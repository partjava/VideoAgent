<template>
  <el-card class="form-card" shadow="never">
    <el-form :model="form" label-position="top" @submit.prevent="handleSubmit">
      <el-form-item label="视频生成需求" required>
        <el-input
          v-model="form.user_input"
          type="textarea"
          :rows="4"
          placeholder="用一句话描述你要的视频，例如：用20秒讲清楚猫为什么会踩奶。"
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
        <el-form-item label="目标时长" class="form-item-third">
          <el-select v-model="form.duration" placeholder="选择预计时长" class="full-width">
            <el-option label="自动" :value="0" />
            <el-option label="10 秒测试" :value="10" />
            <el-option label="30 秒" :value="30" />
            <el-option label="60 秒" :value="60" />
            <el-option label="90 秒" :value="90" />
          </el-select>
        </el-form-item>

        <el-form-item label="画面比例" class="form-item-third">
          <el-radio-group v-model="form.ratio" class="ratio-group">
            <el-radio-button label="9:16">9:16 竖屏</el-radio-button>
            <el-radio-button label="16:9">16:9 横屏</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>

      <div class="usage-estimate-bar">
        <span>每段生成 10 秒动态视频，完成后自动拼接。</span>
        <span class="estimate-badge">预计消耗：约 {{ estimatedUsage.images }} 张图 + {{ estimatedUsage.videos }} 段视频（共 {{ estimatedUsage.total }} 次生成操作）</span>
      </div>

      <div class="workflow-switches">
        <el-checkbox v-model="form.pre_shoot_review">
          开拍前预审首帧与配音（生成完分镜图与配音后暂停，支持单镜试拍）
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
          <el-form-item label="生成操作额度上限（可选）" class="form-item-third">
            <el-input
              v-model="form.generation_limit"
              type="number"
              min="1"
              max="10000"
              placeholder="留空不设上限"
            />
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

defineProps({
  loading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['submit'])

const confirmedCharacters = ref([])

const form = reactive({
  user_input: '',
  duration: 0,
  style: '短剧',
  ratio: '9:16',
  generation_mode: 'native_10s',
  intro_title: '',
  subtitle_style: 'standard',
  pre_shoot_review: false,
  generation_limit: '',
  character_ids: []
})

onMounted(async () => {
  try {
    const list = await charactersApi.list()
    const items = Array.isArray(list) ? list : list?.characters || []
    confirmedCharacters.value = items.filter(c => c && c.confirmed && !c.archived)
  } catch {
    confirmedCharacters.value = []
  }
})

const estimatedUsage = computed(() => {
  const seconds = form.duration > 0 ? form.duration : 30
  const clips = Math.max(1, Math.ceil(seconds / 10))
  return { images: clips, videos: clips, total: clips * 2 }
})

const handleSubmit = () => {
  if (!form.user_input.trim()) {
    return
  }
  const limitNum = String(form.generation_limit ?? '').trim()
    ? Number.parseInt(form.generation_limit, 10)
    : null
  emit('submit', {
    ...form,
    duration: form.duration || null,
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
