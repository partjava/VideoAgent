<template>
  <BasicLayout>
  <div class="comfyui-page">
    <div class="page-header">
      <div>
        <h2 class="page-heading">ComfyUI 配置</h2>
        <p class="page-desc">把本地 WSL 或服务器上的 ComfyUI 接入后续图片和图生视频流程。</p>
      </div>
      <el-tag :type="statusTagType" effect="plain">{{ statusLabel }}</el-tag>
    </div>

    <el-alert
      title="当前页面只修改后续任务配置"
      description="保存后会写入 backend/.env 并更新当前后端进程环境；正在运行中的任务不会中途切换。"
      type="info"
      show-icon
      :closable="false"
      class="notice"
    />

    <el-card class="config-card">
      <el-form :model="form" label-width="170px" label-position="left" size="large">
        <el-form-item label="ComfyUI 地址">
          <div class="inline-row">
            <el-input v-model="form.comfyui_base_url" placeholder="http://127.0.0.1:8188" />
            <el-button :loading="checking" @click="checkConnection">测试连接</el-button>
          </div>
          <div class="field-hint">本机 WSL 通常使用 http://127.0.0.1:8188，换服务器后填服务器地址。</div>
        </el-form-item>

        <el-divider />

        <el-form-item label="图片 Provider">
          <el-segmented v-model="form.image_provider" :options="imageProviderOptions" />
          <div class="field-hint">选择 comfyui 后，后续主流程图片生成会走本地 ComfyUI。</div>
        </el-form-item>

        <el-form-item label="视频 Provider">
          <el-segmented v-model="form.video_provider" :options="videoProviderOptions" />
          <div class="field-hint">选择 comfyui 后，后续主流程图生视频会走本地 ComfyUI。</div>
        </el-form-item>

        <el-form-item label="请求超时">
          <el-input-number
            v-model="form.comfyui_timeout_seconds"
            :min="10"
            :max="3600"
            :step="30"
            controls-position="right"
          />
          <span class="unit-text">秒</span>
        </el-form-item>

        <el-divider />

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
          <el-button :loading="loading" @click="loadConfig">重新读取</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="status-card">
      <template #header>
        <span>当前生效配置</span>
      </template>
      <div class="status-grid">
        <div class="status-item">
          <span class="status-name">图片生成</span>
          <el-tag effect="plain">{{ form.image_provider }}</el-tag>
        </div>
        <div class="status-item">
          <span class="status-name">图生视频</span>
          <el-tag effect="plain">{{ form.video_provider }}</el-tag>
        </div>
        <div class="status-item">
          <span class="status-name">ComfyUI</span>
          <span class="status-value">{{ form.comfyui_base_url }}</span>
        </div>
      </div>
    </el-card>

    <el-card class="workflow-card">
      <template #header>
        <span>Workflow 配置</span>
      </template>
      <el-tabs v-model="activeWorkflow">
        <el-tab-pane label="图片生成" name="image">
          <el-form label-position="top">
            <el-form-item label="图片 workflow JSON">
              <el-input v-model="imageWorkflowText" type="textarea" :rows="8" placeholder="粘贴 ComfyUI API Format workflow JSON" />
            </el-form-item>
            <el-form-item label="图片节点映射 JSON">
              <el-input v-model="imageMappingText" type="textarea" :rows="6" placeholder='例如 {"positive_prompt":{"node":"6","path":"inputs.text"},"negative_prompt":{"node":"7","path":"inputs.text"}}' />
            </el-form-item>
            <el-button type="primary" :loading="savingImageWorkflow" @click="saveImageWorkflow">保存图片 workflow</el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="图生视频" name="video">
          <el-form label-position="top">
            <el-form-item label="视频 workflow JSON">
              <el-input v-model="videoWorkflowText" type="textarea" :rows="8" placeholder="粘贴 ComfyUI 图生视频 API Format workflow JSON" />
            </el-form-item>
            <el-form-item label="视频节点映射 JSON">
              <el-input v-model="videoMappingText" type="textarea" :rows="6" placeholder='例如 {"positive_prompt":{"node":"6","path":"inputs.text"},"negative_prompt":{"node":"7","path":"inputs.text"},"seed":{"node":"3","path":"inputs.seed"}}' />
            </el-form-item>
            <el-button type="primary" :loading="savingVideoWorkflow" @click="saveVideoWorkflow">保存视频 workflow</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card class="workflow-card">
      <template #header>
        <span>单独测试</span>
      </template>
      <div class="test-grid">
        <div class="test-panel">
          <h3>文生图测试</h3>
          <el-input v-model="testImagePrompt" type="textarea" :rows="4" placeholder="输入图片提示词" />
          <el-input v-model="testImageNegative" class="mt-2" placeholder="negative prompt，可选" />
          <el-button class="mt-2" type="primary" :loading="testingImage" @click="testImage">测试生成图片</el-button>
          <div v-if="testImagePath" class="result-path">{{ testImagePath }}</div>
          <img v-if="testImageUrl" class="preview-image" :src="testImageUrl" alt="ComfyUI test output" />
        </div>

        <div class="test-panel">
          <h3>图生视频测试</h3>

          <div class="story-grid">
            <div
              v-for="s in stories"
              :key="s.id"
              class="story-card"
              :class="{ active: selectedStory === s.id }"
              @click="selectStory(s.id)"
            >
              <div class="story-title">{{ s.title }}</div>
              <div class="story-tag">{{ s.tag }}</div>
              <div class="story-scene">{{ s.scene }}</div>
            </div>
          </div>

          <div class="inline-row mt-2">
            <el-input v-model="testVideoDescription" placeholder="或者自己输入画面描述，DeepSeek 会自动生成提示词" />
            <el-button :loading="generatingPrompt" @click="generatePrompt">DeepSeek 生成</el-button>
          </div>
          <el-input v-model="testVideoPrompt" class="mt-2" type="textarea" :rows="3" placeholder="正向提示词（DeepSeek 会自动生成，可手动修改）" />
          <el-input v-model="testVideoNegative" class="mt-2" placeholder="反向提示词（可选）" />
          <el-button class="mt-2" type="primary" :loading="testingVideo" @click="testVideo">测试生成视频</el-button>
          <div v-if="testingVideo" class="mt-2">
            <el-progress :percentage="videoProgressMax > 0 ? Math.round(videoProgress / videoProgressMax * 100) : 0" :indeterminate="true" :duration="3" />
            <div class="progress-label">{{ videoStage === 'error' ? '生成出错' : 'ComfyUI 生成中，请等待...（约 2-3 分钟）' }}</div>
          </div>
          <div v-if="testVideoPath" class="result-path">{{ testVideoPath }}</div>
          <video v-if="testVideoUrl" class="preview-video" :src="testVideoUrl" controls />
        </div>
      </div>
    </el-card>

  </div>
  </BasicLayout>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { comfyuiApi } from '../api/comfyui.js'
import BasicLayout from '../layouts/BasicLayout.vue'

const imageProviderOptions = [
  { label: 'Qwen', value: 'qwen' },
  { label: 'ComfyUI', value: 'comfyui' }
]

const videoProviderOptions = [
  { label: 'Vidu', value: 'vidu' },
  { label: 'Wan', value: 'wan' },
  { label: 'Doubao', value: 'doubao' },
  { label: 'ComfyUI', value: 'comfyui' }
]

const form = reactive({
  image_provider: 'qwen',
  video_provider: 'vidu',
  comfyui_base_url: 'http://127.0.0.1:8188',
  comfyui_timeout_seconds: 600
})

const loading = ref(false)
const saving = ref(false)
const checking = ref(false)
const connectionOk = ref(null)
const activeWorkflow = ref('image')
const imageWorkflowText = ref('')
const imageMappingText = ref('')
const videoWorkflowText = ref('')
const videoMappingText = ref('')
const savingImageWorkflow = ref(false)
const savingVideoWorkflow = ref(false)
const testImagePrompt = ref('')
const testImageNegative = ref('')
const testImagePath = ref('')
const testImageUrl = ref('')
const testingImage = ref(false)
const stories = ref([])
const selectedStory = ref('')
const testVideoPrompt = ref('')
const testVideoNegative = ref('')
const testVideoDescription = ref('')
const testVideoPath = ref('')
const testVideoUrl = ref('')
const testingVideo = ref(false)
const generatingPrompt = ref(false)
const videoProgress = ref(0)
const videoProgressMax = ref(0)
const videoStage = ref('')

async function pollProgress(promptId) {
  while (true) {
    try {
      const p = await comfyuiApi.getProgress(promptId)
      videoProgress.value = p.value || 0
      videoProgressMax.value = p.max || 0
      videoStage.value = p.stage || 'unknown'
      if (p.done) break
    } catch {
      // ignore polling errors
    }
    await new Promise(r => setTimeout(r, 1000))
  }
}

const statusLabel = computed(() => {
  if (connectionOk.value === true) return 'ComfyUI 已连接'
  if (connectionOk.value === false) return 'ComfyUI 未连接'
  return '未测试连接'
})

const statusTagType = computed(() => {
  if (connectionOk.value === true) return 'success'
  if (connectionOk.value === false) return 'danger'
  return 'info'
})

function applyConfig(config) {
  form.image_provider = config.image_provider || 'qwen'
  form.video_provider = config.video_provider || 'vidu'
  form.comfyui_base_url = config.comfyui_base_url || 'http://127.0.0.1:8188'
  form.comfyui_timeout_seconds = Number(config.comfyui_timeout_seconds || 600)
}

async function loadConfig() {
  loading.value = true
  try {
    const config = await comfyuiApi.getConfig()
    applyConfig(config)
    await loadWorkflows()
    await loadStories()
  } catch (error) {
    ElMessage.error('读取 ComfyUI 配置失败: ' + error.message)
  } finally {
    loading.value = false
  }
}

async function loadStories() {
  try {
    const data = await comfyuiApi.getStories()
    stories.value = data.stories || []
  } catch {
    // 故事模板加载失败不影响主功能
  }
}

function parseJsonText(text, label) {
  try {
    return JSON.parse(text)
  } catch (error) {
    throw new Error(`${label} 不是合法 JSON: ${error.message}`)
  }
}

function assetUrl(path) {
  if (!path) return ''
  return '/' + String(path).replace(/^backend\//, '')
}

async function loadWorkflows() {
  const workflows = await comfyuiApi.getWorkflows()
  if (workflows.image?.workflow) {
    imageWorkflowText.value = JSON.stringify(workflows.image.workflow, null, 2)
    imageMappingText.value = JSON.stringify(workflows.image.mapping || {}, null, 2)
  }
  if (workflows.video?.workflow) {
    videoWorkflowText.value = JSON.stringify(workflows.video.workflow, null, 2)
    videoMappingText.value = JSON.stringify(workflows.video.mapping || {}, null, 2)
  }
}

async function saveImageWorkflow() {
  savingImageWorkflow.value = true
  try {
    await comfyuiApi.saveImageWorkflow({
      workflow: parseJsonText(imageWorkflowText.value, '图片 workflow'),
      mapping: parseJsonText(imageMappingText.value || '{}', '图片节点映射')
    })
    ElMessage.success('图片 workflow 已保存')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    savingImageWorkflow.value = false
  }
}

async function saveVideoWorkflow() {
  savingVideoWorkflow.value = true
  try {
    await comfyuiApi.saveVideoWorkflow({
      workflow: parseJsonText(videoWorkflowText.value, '视频 workflow'),
      mapping: parseJsonText(videoMappingText.value || '{}', '视频节点映射')
    })
    ElMessage.success('视频 workflow 已保存')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    savingVideoWorkflow.value = false
  }
}

async function testImage() {
  testingImage.value = true
  try {
    const result = await comfyuiApi.testImage({
      prompt: testImagePrompt.value,
      negative_prompt: testImageNegative.value
    })
    testImagePath.value = result.asset_path
    testImageUrl.value = assetUrl(result.asset_path)
    ElMessage.success('图片测试生成完成')
  } catch (error) {
    ElMessage.error('图片测试失败: ' + error.message)
  } finally {
    testingImage.value = false
  }
}

async function testVideo() {
  testingVideo.value = true
  videoProgress.value = 0
  videoProgressMax.value = 0
  videoStage.value = 'queued'
  testVideoPath.value = ''
  testVideoUrl.value = ''
  try {
    // 1. 提交到 ComfyUI，获取 prompt_id
    const queueResult = await comfyuiApi.testVideo({ prompt: testVideoPrompt.value })
    // 2. 开始轮询进度（后台跑）
    pollProgress(queueResult.prompt_id)
    // 3. 等结果
    const result = await comfyuiApi.getVideoResult(queueResult.prompt_id)
    testVideoPath.value = result.asset_path
    testVideoUrl.value = assetUrl(result.asset_path)
    videoStage.value = 'done'
    ElMessage.success('视频测试生成完成')
  } catch (error) {
    ElMessage.error('视频测试失败: ' + error.message)
  } finally {
    testingVideo.value = false
  }
}

function selectStory(id) {
  selectedStory.value = id
  const s = stories.value.find(st => st.id === id)
  if (s) {
    testVideoDescription.value = s.scene
    generatePrompt()
  }
}

async function generatePrompt() {
  const desc = testVideoDescription.value.trim()
  if (!desc && !selectedStory.value) {
    ElMessage.warning('请先选择故事模板或输入画面描述')
    return
  }
  generatingPrompt.value = true
  try {
    const payload = selectedStory.value
      ? { story_id: selectedStory.value }
      : { description: desc }
    const result = await comfyuiApi.generatePrompt(payload)
    testVideoPrompt.value = result.positive_prompt || ''
    testVideoNegative.value = result.negative_prompt || ''
    ElMessage.success('提示词生成成功，可以手动修改后测试')
  } catch (error) {
    ElMessage.error('DeepSeek 生成失败: ' + error.message)
  } finally {
    generatingPrompt.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const result = await comfyuiApi.saveConfig({ ...form })
    applyConfig(result.config)
    ElMessage.success('ComfyUI 配置已保存')
  } catch (error) {
    ElMessage.error('保存 ComfyUI 配置失败: ' + error.message)
  } finally {
    saving.value = false
  }
}

async function checkConnection() {
  checking.value = true
  try {
    const result = await comfyuiApi.checkConnection({
      comfyui_base_url: form.comfyui_base_url,
      comfyui_timeout_seconds: form.comfyui_timeout_seconds
    })
    connectionOk.value = result.ok === true
    if (result.ok) {
      ElMessage.success(result.message || 'ComfyUI 连接成功')
    } else {
      ElMessage.error(result.message || 'ComfyUI 连接失败')
    }
  } catch (error) {
    connectionOk.value = false
    ElMessage.error('测试 ComfyUI 连接失败: ' + error.message)
  } finally {
    checking.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.comfyui-page {
  max-width: 980px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.page-heading {
  margin: 0 0 4px;
  color: #0f172a;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.page-desc {
  margin: 0;
  color: #64748b;
  font-size: 14px;
}

.notice {
  margin-bottom: 20px;
  border-radius: 10px;
}

/* ===== 卡片通用 ===== */
.config-card,
.status-card,
.workflow-card {
  border: 1px solid #e9edf4;
  border-radius: 12px;
  margin-bottom: 20px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  transition: box-shadow 0.2s;
}

.config-card:hover,
.workflow-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.06);
}

/* ===== 测试卡片 ===== */
.test-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.test-panel {
  border: 1px solid #e9edf4;
  border-radius: 12px;
  padding: 18px;
  background: #fafbfc;
  transition: border-color 0.2s;
}

.test-panel:focus-within {
  border-color: #4f46e5;
}

.test-panel h3 {
  margin: 0 0 14px;
  color: #0f172a;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.test-panel h3::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 16px;
  background: #4f46e5;
  border-radius: 2px;
}

.mt-2 {
  margin-top: 10px;
}

.result-path {
  margin-top: 10px;
  color: #64748b;
  font-size: 12px;
  word-break: break-all;
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.preview-image,
.preview-video {
  display: block;
  width: 100%;
  max-height: 360px;
  object-fit: contain;
  margin-top: 10px;
  border-radius: 10px;
  background: #020617;
}

.progress-label {
  margin-top: 8px;
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

.inline-row {
  display: flex;
  width: 100%;
  gap: 10px;
}

.field-hint {
  width: 100%;
  margin-top: 6px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.5;
}

.unit-text {
  margin-left: 10px;
  color: #64748b;
}

/* ===== 状态展示 ===== */
.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.status-item {
  min-height: 76px;
  padding: 16px;
  border: 1px solid #e9edf4;
  border-radius: 10px;
  background: #fafbfc;
}

.status-name {
  display: block;
  margin-bottom: 8px;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
}

.status-value {
  color: #0f172a;
  font-size: 14px;
  word-break: break-all;
  font-weight: 500;
}

/* ===== 故事卡片 ===== */
.story-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.story-card {
  position: relative;
  padding: 14px;
  border: 1.5px solid #e9edf4;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  transition: all 0.25s ease;
  overflow: hidden;
}

.story-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 3px;
  background: linear-gradient(90deg, #4f46e5, #7c3aed);
  opacity: 0;
  transition: opacity 0.25s;
}

.story-card:hover {
  border-color: #c7d2fe;
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(79,70,229,0.1);
}

.story-card:hover::before {
  opacity: 1;
}

.story-card.active {
  border-color: #4f46e5;
  background: #f5f3ff;
  box-shadow: 0 2px 8px rgba(79,70,229,0.12);
}

.story-card.active::before {
  opacity: 1;
}

.story-title {
  font-weight: 700;
  font-size: 14px;
  color: #0f172a;
  margin-bottom: 3px;
}

.story-tag {
  font-size: 11px;
  color: #4f46e5;
  margin-bottom: 6px;
  font-weight: 500;
  display: inline-block;
  padding: 2px 8px;
  background: #eef2ff;
  border-radius: 4px;
}

.story-scene {
  font-size: 12px;
  color: #64748b;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ===== 响应式 ===== */
@media (max-width: 900px) {
  .story-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .page-header,
  .inline-row {
    flex-direction: column;
  }

  .status-grid {
    grid-template-columns: 1fr;
  }

  .test-grid {
    grid-template-columns: 1fr;
  }

  .story-grid {
    grid-template-columns: 1fr;
  }
}
</style>
