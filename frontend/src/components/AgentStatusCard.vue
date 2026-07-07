<template>
  <el-card class="agent-card" :class="statusClass" shadow="never">
    <div class="agent-header">
      <div class="agent-info">
        <el-icon class="agent-icon" :class="statusClass">
          <component :is="getAgentIcon(agentName)" />
        </el-icon>
        <span class="agent-title">{{ displayTitle }}</span>
      </div>
      <div class="status-badge" :class="statusClass">
        <span class="status-dot"></span>
        <span class="status-text">{{ statusText }}</span>
      </div>
    </div>
    
    <div class="agent-body">
      <p class="agent-desc">{{ description }}</p>
      <div v-if="status === 'running'" class="running-animation">
        <span class="dot-1"></span>
        <span class="dot-2"></span>
        <span class="dot-3"></span>
      </div>
      <div v-if="outputSummary" class="agent-output">
        <el-icon class="output-icon"><InfoFilled /></el-icon>
        <span class="output-text">{{ outputSummary }}</span>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  agentName: {
    type: String,
    required: true
  },
  status: {
    type: String,
    default: 'waiting' // waiting, running, success, failed
  },
  description: {
    type: String,
    default: ''
  },
  outputSummary: {
    type: String,
    default: ''
  }
})

const displayTitle = computed(() => {
  const titles = {
    TaskPlannerAgent: '任务规划智能体',
    ScriptAgent: '脚本生成智能体',
    StoryboardAgent: '分镜设计智能体',
    DialoguePolishAgent: '台词润色智能体',
    PromptAgent: '提示词智能体',
    ImageAgent: '图片生成智能体',
    VoiceAgent: '语音合成智能体 (edge-tts)',
    SubtitleAgent: '字幕生成智能体',
    EditorAgent: '自动剪辑智能体',
    QualityAgent: '质检智能体',
    ExportAgent: '视频导出智能体'
  }
  return titles[props.agentName] || props.agentName
})

const statusText = computed(() => {
  if (props.status === 'success') return '完成'
  if (props.status === 'running') return '处理中'
  if (props.status === 'failed') return '失败'
  if (props.status === 'retrying') return '重试中'
  return '等待中'
})

const statusClass = computed(() => {
  return props.status
})

const getAgentIcon = (name) => {
  const icons = {
    TaskPlannerAgent: 'Cpu',
    ScriptAgent: 'Document',
    StoryboardAgent: 'Film',
    DialoguePolishAgent: 'EditPen',
    PromptAgent: 'MagicStick',
    ImageAgent: 'Picture',
    VoiceAgent: 'Microphone',
    SubtitleAgent: 'ChatLineSquare',
    EditorAgent: 'VideoPlay',
    QualityAgent: 'Checked',
    ExportAgent: 'Download'
  }
  return icons[name] || 'Setting'
}
</script>

<style scoped>
.agent-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  margin-bottom: 16px;
  transition: all 0.25s ease;
  background-color: #ffffff;
  cursor: pointer;
}

.agent-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.06);
  border-color: #cbd5e1;
}

/* Waiting state */
.agent-card.waiting {
  opacity: 0.65;
  border-left: 4px solid #cbd5e1;
}

/* Running state */
.agent-card.running {
  border-left: 4px solid #4f46e5;
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.08);
  background-color: #faf5ff;
}

/* Success state */
.agent-card.success {
  border-left: 4px solid #10b981;
}

/* Failed state */
.agent-card.failed {
  border-left: 4px solid #ef4444;
  background-color: #fef2f2;
}

/* Retrying state */
.agent-card.retrying {
  border-left: 4px solid #f97316;
  background-color: #fff7ed;
}

.agent-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.agent-icon {
  font-size: 20px;
  color: #64748b;
}

.agent-icon.running {
  color: #4f46e5;
  animation: pulse 1.5s infinite ease-in-out;
}

.agent-icon.success {
  color: #10b981;
}

.agent-icon.failed {
  color: #ef4444;
}

.agent-icon.retrying {
  color: #f97316;
}

.agent-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.waiting {
  background-color: #f1f5f9;
  color: #64748b;
}

.status-badge.running {
  background-color: #e0e7ff;
  color: #4f46e5;
}

.status-badge.success {
  background-color: #d1fae5;
  color: #065f46;
}

.status-badge.failed {
  background-color: #fee2e2;
  color: #991b1b;
}

.status-badge.retrying {
  background-color: #ffedd5;
  color: #c2410c;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: currentColor;
}

.status-badge.running .status-dot {
  animation: blink 1.2s infinite ease-in-out;
}

.agent-body {
  padding-left: 30px;
}

.agent-desc {
  font-size: 13px;
  color: #64748b;
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.agent-output {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #475569;
  background-color: #f8fafc;
  padding: 6px 12px;
  border-radius: 6px;
  margin-top: 8px;
}

.agent-card.failed .agent-output {
  background-color: #fee2e2;
  color: #ef4444;
}

.output-icon {
  font-size: 14px;
}

.output-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Animations */
@keyframes blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

@keyframes pulse {
  0% { transform: scale(1); }
  50% { transform: scale(1.1); }
  100% { transform: scale(1); }
}

.running-animation {
  display: flex;
  gap: 4px;
  margin: 8px 0;
}

.running-animation span {
  width: 5px;
  height: 5px;
  background-color: #4f46e5;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.running-animation .dot-1 { animation-delay: -0.32s; }
.running-animation .dot-2 { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1.0); }
}
</style>
