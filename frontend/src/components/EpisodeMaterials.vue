<template>
  <el-button @click="open = true">发布素材</el-button>
  <el-dialog v-model="open" title="发布素材" width="min(680px, 94vw)">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-form label-position="top">
      <el-form-item label="文案模板">
        <el-select v-model="platform">
          <el-option label="通用" value="通用" />
          <el-option label="抖音" value="抖音" />
        </el-select>
      </el-form-item>
      <el-button :loading="busy === 'notes'" :disabled="!!busy" @click="generate('notes')">
        生成标题、文案与话题
      </el-button>
      <div v-if="notes" class="material">
        <strong>{{ notes.title }}</strong>
        <p>{{ notes.body }}</p>
        <p>{{ notes.tags.map(tag => '#' + tag).join(' ') }}</p>
        <a :href="url(notes.path)" download>下载文案 TXT</a>
      </div>
      <el-form-item label="封面标题（可留空）" class="cover-title">
        <el-input v-model="title" maxlength="60" />
      </el-form-item>
      <el-form-item label="封面取帧">
        <el-select v-model="second">
          <el-option label="自动选择清晰画面" :value="-1" />
          <el-option v-for="time in [0.4, 1.3, 2.2, 2.9]" :key="time" :label="time + ' 秒'" :value="time" />
        </el-select>
      </el-form-item>
      <el-button :loading="busy === 'cover'" :disabled="!!busy" @click="generate('cover')">生成封面</el-button>
      <div v-if="cover" class="material">
        <img :src="url(cover.path) + '?v=' + cover.version" alt="发布封面" />
        <a :href="url(cover.path)" download>下载封面 JPG</a>
      </div>
    </el-form>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { seriesApi } from '../api/series'

const props = defineProps({ seriesId: String, episodeNum: Number })
const open = ref(false)
const platform = ref('通用')
const title = ref('')
const second = ref(-1)
const notes = ref(null)
const cover = ref(null)
const busy = ref('')
const error = ref('')
const url = path => '/' + path.replace(/^backend\//, '')

async function generate(kind) {
  error.value = ''
  busy.value = kind
  try {
    if (kind === 'notes') {
      notes.value = await seriesApi.publishNotes(props.seriesId, props.episodeNum, platform.value)
    } else {
      const result = await seriesApi.publishCover(props.seriesId, props.episodeNum, {
        second: second.value < 0 ? null : second.value,
        title: title.value,
      })
      cover.value = { ...result, version: Date.now() }
    }
  } catch (failure) {
    error.value = failure.message
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.cover-title, .material { margin-top: 16px; }
.material img { display: block; max-height: 280px; max-width: 100%; margin-bottom: 8px; }
</style>
