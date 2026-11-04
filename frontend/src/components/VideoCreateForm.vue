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

      <el-form-item label="画面风格" class="style-select">
        <el-radio-group v-model="form.style" class="style-group">
          <el-radio-button label="">自动</el-radio-button>
          <el-radio-button label="漫剧">漫剧（漫画风格）</el-radio-button>
          <el-radio-button label="短剧">短剧（写实风格）</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <div class="form-row-three">
        <el-form-item label="发布平台" class="form-item-third">
          <el-select v-model="form.platform" placeholder="选择目标平台" class="full-width">
            <el-option label="抖音" value="抖音" />
            <el-option label="Bilibili" value="B站" />
            <el-option label="小红书" value="小红书" />
          </el-select>
        </el-form-item>

        <el-form-item label="目标时长" class="form-item-third">
          <el-select v-model="form.duration" placeholder="选择预计时长" class="full-width">
            <el-option label="自动" :value="0" />
            <el-option label="10 秒测试" :value="10" />
            <el-option label="30 秒" :value="30" />
            <el-option v-if="form.generation_mode !== 'wan3_whole'" label="60 秒" :value="60" />
            <el-option v-if="form.generation_mode !== 'wan3_whole'" label="90 秒" :value="90" />
          </el-select>
        </el-form-item>

        <el-form-item label="画面比例" class="form-item-third">
          <el-radio-group v-model="form.ratio" class="ratio-group">
            <el-radio-button label="9:16">9:16 竖屏</el-radio-button>
            <el-radio-button label="16:9">16:9 横屏</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>

      <el-form-item label="生成方式">
        <el-select v-model="form.generation_mode" @change="form.duration = 0">
          <el-option label="双段续接 · 每 10 秒一组" value="paired_5s" />
          <el-option label="Wan 3.0 整段 · 最长 30 秒" value="wan3_whole" />
        </el-select>
      </el-form-item>
      <el-form-item label="字幕样式">
        <el-input v-model="form.intro_title" maxlength="60" placeholder="Optional opening title" />
        <el-select v-model="form.subtitle_style">
          <el-option label="标准字幕" value="standard" />
          <el-option label="国风诗词" value="poetry" />
        </el-select>
      </el-form-item>
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
import { reactive } from 'vue'

defineProps({
  loading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['submit'])

const form = reactive({
  user_input: '',
  duration: 0,
  style: '',
  platform: '抖音',
  ratio: '9:16',
  generation_mode: 'paired_5s',
  intro_title: '',
  subtitle_style: 'standard'
})

const handleSubmit = () => {
  if (!form.user_input.trim()) {
    return
  }
  emit('submit', { ...form, duration: form.duration || null, style: form.style || null })
}
</script>

<style scoped>
.form-card {
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 8px;
}

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
