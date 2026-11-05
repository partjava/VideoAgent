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

      <p>每段生成 10 秒动态视频，完成后自动拼接。</p>
      <details class="advanced-options">
        <summary>高级设置</summary>
      <el-form-item label="片头标题（可选）">
        <el-input v-model="form.intro_title" maxlength="60" placeholder="输入需要叠加在片头的文字" />
      </el-form-item>
      <el-form-item label="字幕样式">
        <el-select v-model="form.subtitle_style">
          <el-option label="标准字幕" value="standard" />
          <el-option label="国风诗词" value="poetry" />
        </el-select>
      </el-form-item>
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
  style: '短剧',
  ratio: '9:16',
  generation_mode: 'native_10s',
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
.style-help { color:#475569; font-size:13px; line-height:1.6; width:100%; margin:8px 0 0; }
.advanced-options { margin:16px 0; }
.advanced-options summary { cursor:pointer; margin-bottom:12px; }

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
