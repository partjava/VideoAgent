<template>
  <details v-if="rows.length" class="audio-timing">
    <summary>调整配音时间与语速</summary>
    <p>拖动音频块调整起点，应用后更新声音和字幕。人物已有的口型与动作仍按原视频播放。</p>
    <div v-for="(row, index) in rows" :key="index" class="audio-row">
      <strong>{{ row.speaker }}</strong><span> {{ row.text }}</span>
      <div class="audio-track" @pointerdown="beginDrag($event, index)">
        <span class="track-start">0 秒</span><span class="track-end">{{ scene.duration }} 秒</span>
        <div class="audio-block" :class="{ overflow: invalidRow(index) }"
          :style="{ left: percent(row.start), width: percent(row.duration / row.speed) }">
          {{ Number(row.start).toFixed(2) }}–{{ end(row).toFixed(2) }} 秒
        </div>
      </div>
      <label>起点
        <input v-model.number="row.start" type="number" min="0" :max="scene.duration" step="0.01" :disabled="busy" />
      </label>
      <label>语速 {{ row.speed.toFixed(2) }} 倍
        <input v-model.number="row.speed" type="range" min="0.9" max="1.25" step="0.01" :disabled="busy" />
      </label>
      <audio :src="mediaUrl(row.path)" controls preload="none"
        @play="event => { event.target.playbackRate = row.speed }" />
      <p v-if="invalidRow(index)" role="alert">配音重叠或超出镜头，请调整起点或语速。</p>
    </div>
    <button type="button" :disabled="busy || invalid" @click="$emit('apply', rows.map(row => ({ start: row.start, speed: row.speed })))">
      {{ busy ? '正在重新合成…' : '应用时间并重新合成' }}
    </button>
  </details>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ scene: { type: Object, required: true }, busy: Boolean })
defineEmits(['apply'])
const rows = ref([])
watch(() => props.scene, scene => {
  if (scene.dialogue_audio?.length) {
    rows.value = scene.dialogue_audio.map(line => ({
      ...line,
      duration: line.source_duration || (line.end - line.start) * line.speed
    }))
  } else if (scene.audio_path && scene.audio_duration) {
    const start = scene.voice_start || 0
    rows.value = [{
      start, speed: scene.audio_speed || Math.max(1, scene.audio_duration / (scene.duration - start)),
      duration: scene.audio_duration, speaker: scene.speaker || '旁白',
      text: scene.audio_text || scene.voiceover, path: scene.audio_path
    }]
  } else rows.value = []
}, { immediate: true })
const end = row => row.start + row.duration / row.speed
const percent = seconds => (seconds / props.scene.duration * 100) + '%'
const invalidRow = index => {
  const row = rows.value[index]
  return !Number.isFinite(row.start) || row.start < 0 || !Number.isFinite(row.speed) ||
    row.speed < 0.9 || row.speed > 1.25 || end(row) > props.scene.duration + 0.001 ||
    (index > 0 && row.start < end(rows.value[index - 1]) - 0.001)
}
const invalid = computed(() => rows.value.some((_, index) => invalidRow(index)))
const mediaUrl = path => '/' + path.replace(/^backend\//, '')
function beginDrag(event, index) {
  if (props.busy || !event.target.closest('.audio-block')) return
  const track = event.currentTarget
  const bounds = track.getBoundingClientRect()
  const initialX = event.clientX
  const initialStart = rows.value[index].start
  track.setPointerCapture(event.pointerId)
  const move = e => {
    rows.value[index].start = Math.round(Math.max(0, Math.min(props.scene.duration,
      initialStart + (e.clientX - initialX) / bounds.width * props.scene.duration)) * 100) / 100
  }
  const finish = () => {
    track.removeEventListener('pointermove', move)
    track.removeEventListener('pointerup', finish)
    track.removeEventListener('pointercancel', finish)
  }
  track.addEventListener('pointermove', move)
  track.addEventListener('pointerup', finish)
  track.addEventListener('pointercancel', finish)
}
</script>

<style scoped>
.audio-timing { padding:12px; background:#f8fafc; border-radius:10px; margin:12px 0; }
summary { cursor:pointer; }
p { font-size:13px; color:#475569; }
.audio-row { padding:12px 0; border-bottom:1px solid #e2e8f0; overflow:hidden; }
.audio-track { position:relative; height:52px; margin:24px 0 12px; background:#e2e8f0; touch-action:none; }
.track-start,.track-end { position:absolute; top:-20px; font-size:12px; }
.track-end { right:0; }
.audio-block { position:absolute; height:100%; background:#15803d; color:white; cursor:grab; border-radius:5px; min-width:2px; padding:4px; box-sizing:border-box; font-size:12px; user-select:none; }
.overflow { background:#b91c1c; }
label { display:inline-flex; align-items:center; gap:6px; margin:8px 12px 8px 0; }
input[type=number] { width:72px; }
audio { width:100%; height:32px; }
button { margin-top:12px; padding:8px 12px; cursor:pointer; }
button:disabled { cursor:default; opacity:.5; }
</style>
