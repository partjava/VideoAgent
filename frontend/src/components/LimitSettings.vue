<template>
  <section class="limit-card" aria-label="Generation limit settings">
    <div class="limit-head">
      <div>
        <h3>任务生成额度上限（网页实时生效，无需改 .env 重启）</h3>
        <p class="limit-desc">
          用于防止单集拆镜过多导致意外扣费；任意项填 <strong>0</strong> 表示不限制，保存后立刻生效。
        </p>
      </div>
      <el-tag size="small" :type="source === 'local' ? 'success' : 'info'">
        {{ source === 'local' ? '已保存到数据库与本地（实时生效）' : '当前沿用 .env 默认值' }}
      </el-tag>
    </div>

    <div class="preset-row">
      <span>快捷预设：</span>
      <button type="button" class="preset-chip" @click="applyPreset(0, 0, 0)">
        不限制（全填 0）
      </button>
      <button type="button" class="preset-chip" @click="applyPreset(30, 180, 30)">
        单集标准（30 图 · 180 秒 · 30 镜）
      </button>
      <button type="button" class="preset-chip" @click="applyPreset(10, 60, 10)">
        低额省钱测试（10 图 · 60 秒 · 10 镜）
      </button>
    </div>

    <el-form label-position="top" class="limit-form" @submit.prevent>
      <el-form-item label="单任务最大首帧/设定图数（0 = 不限制）">
        <el-input-number v-model="form.max_images_per_task" :min="0" :max="10000" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item label="单任务视频时长提醒上限 / 秒（0 = 不限制）">
        <el-input-number v-model="form.max_video_seconds_per_task" :min="0" :max="10000" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item label="单任务动态镜头提醒上限（0 = 不限制）">
        <el-input-number v-model="form.max_dynamic_scenes_per_task" :min="0" :max="10000" controls-position="right" style="width: 100%" />
      </el-form-item>
    </el-form>

    <div class="limit-actions">
      <el-button type="primary" :loading="saving" @click="handleSave">
        保存并立即生效
      </el-button>
      <el-button v-if="source === 'local'" plain :loading="resetting" @click="handleReset">
        恢复 .env 默认（{{ envDefaults.max_images_per_task }} 图 / {{ envDefaults.max_video_seconds_per_task }} 秒 / {{ envDefaults.max_dynamic_scenes_per_task }} 镜）
      </el-button>
    </div>

    <el-alert
      v-if="feedback"
      :title="feedback.message"
      :type="feedback.type"
      show-icon
      :closable="false"
      class="limit-feedback"
    />
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { settingsApi } from '../api/settings'

const source = ref('env')
const saving = ref(false)
const resetting = ref(false)
const feedback = ref(null)
const envDefaults = reactive({
  max_images_per_task: 20,
  max_video_seconds_per_task: 600,
  max_dynamic_scenes_per_task: 99,
})
const form = reactive({
  max_images_per_task: 20,
  max_video_seconds_per_task: 600,
  max_dynamic_scenes_per_task: 99,
})

function applyResponse(data) {
  if (!data) return
  form.max_images_per_task = Number(data.max_images_per_task ?? 0)
  form.max_video_seconds_per_task = Number(data.max_video_seconds_per_task ?? 0)
  form.max_dynamic_scenes_per_task = Number(data.max_dynamic_scenes_per_task ?? 0)
  source.value = data.source || 'env'
  if (data.env_defaults) {
    Object.assign(envDefaults, data.env_defaults)
  }
}

function applyPreset(images, seconds, scenes) {
  form.max_images_per_task = images
  form.max_video_seconds_per_task = seconds
  form.max_dynamic_scenes_per_task = scenes
  feedback.value = null
}

async function handleSave() {
  saving.value = true
  feedback.value = null
  try {
    const data = await settingsApi.saveLimits({ ...form })
    applyResponse(data)
    feedback.value = { type: 'success', message: '额度上限已保存到数据库并实时生效，可直接回任务页点击「继续生成」或新建任务。' }
  } catch (e) {
    feedback.value = { type: 'error', message: e.message || '保存失败' }
  } finally {
    saving.value = false
  }
}

async function handleReset() {
  resetting.value = true
  feedback.value = null
  try {
    const data = await settingsApi.resetLimits()
    applyResponse(data)
    feedback.value = { type: 'info', message: '已恢复为 backend/.env 默认值。' }
  } catch (e) {
    feedback.value = { type: 'error', message: e.message || '恢复失败' }
  } finally {
    resetting.value = false
  }
}

onMounted(async () => {
  try {
    applyResponse(await settingsApi.getLimits())
  } catch (e) {
    feedback.value = { type: 'error', message: e.message || '读取额度配置失败' }
  }
})
</script>

<style scoped>
.limit-card {
  margin-top: 24px;
  padding: 20px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #ffffff;
}
.limit-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.limit-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
}
.limit-desc {
  margin: 0;
  font-size: 13px;
  color: #64748b;
}
.preset-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 12px;
  color: #475569;
}
.preset-chip {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #334155;
  font-size: 12px;
  cursor: pointer;
}
.preset-chip:hover {
  border-color: #4f46e5;
  color: #4f46e5;
  background: #eef2ff;
}
.limit-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}
.limit-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.limit-feedback {
  margin-top: 14px;
}
</style>
