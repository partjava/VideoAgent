<template>
  <div class="progress-container">
    <div class="progress-header">
      <span class="status-label">整体进度:</span>
      <span class="status-percent">{{ progress }}%</span>
    </div>
    <el-progress 
      :percentage="progress" 
      :status="progressStatus"
      :stroke-width="12" 
      class="custom-progress"
      :color="customColor"
    ></el-progress>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  progress: {
    type: Number,
    required: true
  },
  status: {
    type: String,
    default: 'running'
  }
})

const progressStatus = computed(() => {
  if (props.status === 'failed') return 'exception'
  if (props.status === 'success') return 'success'
  return ''
})

const customColor = computed(() => {
  if (props.status === 'failed') return '#f87171'
  if (props.status === 'success') return '#34d399'
  // Cool blue-purple gradient base color
  return '#6366f1'
})
</script>

<style scoped>
.progress-container {
  background-color: #ffffff;
  padding: 24px;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  margin-bottom: 24px;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.status-label {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.status-percent {
  font-size: 18px;
  font-weight: 700;
  color: #4f46e5;
}

.custom-progress :deep(.el-progress-bar__inner) {
  background-image: linear-gradient(95deg, #4f46e5 0%, #7c3aed 100%);
  transition: width 0.6s ease;
}

.custom-progress :deep(.el-progress__text) {
  display: none; /* Hide default percentage text */
}
</style>
