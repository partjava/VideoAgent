<template>
  <section class="model-grid" aria-label="Model configuration">
    <article v-for="item in stages" :key="item.id" class="model-card">
      <h3>{{ item.title }}</h3>
      <p>{{ item.protocol }}</p>
      <template v-if="profiles[item.id]">
        <el-tag size="small" :type="profiles[item.id].active ? 'success' : 'info'">
          {{ profiles[item.id].active ? '已启用' : '保存后启用' }}
        </el-tag>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="API URL">
            <el-input
              v-model.trim="profiles[item.id].url"
              placeholder="例如 https://api.deepseek.com/v1 或自定义中转地址"
              @input="clearCatalog(item.id)"
            />
          </el-form-item>
          <el-form-item label="模型名称">
            <el-select
              v-model="profiles[item.id].model"
              filterable
              allow-create
              default-first-option
              placeholder="查询后选择，也可以直接输入模型名称"
              style="width:100%"
              @change="clearResult(item.id)"
            >
              <el-option v-for="model in modelOptions(item.id)" :key="model" :label="model" :value="model">
                <span>{{ model }}</span>
                <span v-if="suggestions[item.id]?.includes(model)" class="option-hint">
                  {{ suggestionSources[item.id] === 'catalog' ? '（接口返回候选型号）' : '（项目支持，权限待确认）' }}
                </span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input
              v-model.trim="profiles[item.id].key"
              type="password"
              show-password
              autocomplete="new-password"
              @input="clearCatalog(item.id)"
              :placeholder="keyPlaceholder(item.id)"
            />
            <span class="key-status" :class="{ 'key-status-warn': isUrlChanged(item.id) && !profiles[item.id].key }">
              {{ keyStatusText(item.id) }}
            </span>
          </el-form-item>
          <div class="model-actions">
            <el-button :loading="busy[item.id] === 'catalog'" :disabled="!!busy[item.id]" @click="submit(item.id, 'catalog')">查询模型</el-button>
            <el-button :loading="busy[item.id] === 'test'" :disabled="!!busy[item.id]" @click="submit(item.id, 'test')">测试连接</el-button>
            <el-button type="primary" :loading="busy[item.id] === 'save'" :disabled="!!busy[item.id]" @click="submit(item.id, 'save')">保存配置</el-button>
            <el-button
              v-if="profiles[item.id].key_source === 'local'"
              type="danger"
              plain
              :loading="busy[item.id] === 'clear'"
              :disabled="!!busy[item.id]"
              @click="submit(item.id, 'clear')"
            >清除本地 Key</el-button>
          </div>
        </el-form>
        <el-alert
          v-if="feedback[item.id]"
          :title="feedback[item.id].message"
          :type="feedback[item.id].type"
          :closable="false"
          show-icon
          class="model-feedback"
        />
      </template>
      <p v-else>正在读取配置…</p>
    </article>
  </section>
  <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { settingsApi } from '../api/settings'

const stages = [
  { id: 'prompt', title: '提示词模型', protocol: '支持 OpenAI / DeepSeek / 通义千问兼容地址（自动适配 /v1/models 与 /models）。' },
  { id: 'image', title: '图片生成模型', protocol: '支持 DashScope 原生接口及兼容中转地址（自动回退探测模型目录）。' },
  { id: 'video', title: '视频生成模型', protocol: '支持 DashScope 原生 Wan 接口及兼容中转地址（支持查询目录或手动输入型号）。' },
]
const profiles = reactive({})
const savedUrls = reactive({})
const feedback = reactive({})
const catalogs = reactive({})
const suggestions = reactive({})
const suggestionSources = reactive({})

const modelOptions = id => [...new Set([
  profiles[id].model, ...(catalogs[id] || []), ...(suggestions[id] || [])
].filter(Boolean))]

const isUrlChanged = id => {
  const current = (profiles[id]?.url || '').trim().replace(/\/+$/, '')
  const saved = (savedUrls[id] || '').trim().replace(/\/+$/, '')
  return Boolean(saved && current && current !== saved)
}

const keyPlaceholder = id => {
  if (isUrlChanged(id)) return 'URL 已变更，请输入新地址对应的 API Key'
  return profiles[id]?.has_key ? '同一 URL 留空可沿用已有 Key' : '输入 API Key'
}

const keyStatusText = id => {
  if (isUrlChanged(id) && !profiles[id]?.key) {
    return '已更换 URL，请重新输入新地址对应的 Key（原 Key 不会自动发往新地址）'
  }
  return keySources[profiles[id]?.key_source] || ''
}

function clearCatalog(id) {
  delete catalogs[id]
  delete suggestions[id]
  delete suggestionSources[id]
  clearResult(id)
}

const busy = reactive({})
const loadError = ref('')
const clearResult = id => { delete feedback[id] }
const keySources = { local: '已保存本地 Key', environment: '使用环境配置的 Key', none: '未配置 Key' }
const messages = {
  model_listed: ['success', '连接成功（零成本只读校验），服务返回的模型列表包含当前模型。'],
  model_unlisted: ['warning', '接口连接与 Key 校验成功，但返回的模型列表未包含当前填写的模型名称。'],
  authenticated: ['success', '只读接口连接成功，当前 Key 鉴权通过（不消耗额度）。'],
  denied: ['error', '鉴权失败（401/403），请检查 API Key、服务商地址与工作空间权限。'],
  balance: ['error', '服务商报告账户余额不足（402）。'],
  timeout: ['error', '连接超时，请检查 API URL 或网络连通性。'],
  unreachable: ['error', '无法连接到该地址，请检查 URL、网络和 HTTPS 证书。'],
  unconfirmed: ['warning', '服务器有响应，但未开放只读校验接口；可保存后通过实际任务验证。'],
  http_error: ['warning', '服务返回异常状态码，请检查接口地址或稍后再试。'],
}

function localizeError(raw = '') {
  const text = String(raw || '').trim()
  if (text === 'Enter a key for this URL.') {
    return '请为当前 URL 输入对应的 API Key（更换 URL 后需重新输入 Key）。'
  }
  if (text === 'The catalog rejected this key or workspace.') {
    return '模型目录拒绝了该 API Key（401/403），请检查 Key 是否与当前 URL 匹配。'
  }
  if (text === 'Model catalog request timed out.') {
    return '查询模型目录超时，请检查 URL 或网络状况。'
  }
  if (text === 'Could not connect to the model catalog.') {
    return '无法连接到模型目录接口，请检查 URL 是否正确。'
  }
  return text || '操作失败，请稍后重试。'
}

onMounted(async () => {
  try {
    for (const [id, config] of Object.entries(await settingsApi.getModels())) {
      profiles[id] = { ...config, key: '' }
      savedUrls[id] = config.url
    }
  } catch (error) {
    loadError.value = localizeError(error.message)
  }
})

async function submit(id, action) {
  busy[id] = action
  clearResult(id)
  const { url, model, key } = profiles[id]
  const payload = { url, model, key }
  try {
    if (action === 'clear') {
      const { has_key, key_source } = await settingsApi.clearModelKey(id)
      Object.assign(profiles[id], { has_key, key_source, key: '' })
      clearCatalog(id)
      feedback[id] = { type: 'success', message: '已清除本地 Key。请输入新 Key 并保存配置。' }
    } else if (action === 'save') {
      Object.assign(profiles[id], await settingsApi.saveModel(id, payload), { key: '' })
      savedUrls[id] = profiles[id].url
      feedback[id] = { type: 'success', message: key.trim() ? '配置与新 Key 已保存。' : '已保存配置，沿用已有 Key。' }
    } else if (action === 'catalog') {
      const { models, available, suggested_models = [], suggestion_source = 'none' } = await settingsApi.queryModels(id, payload)
      if (profiles[id].url !== url || profiles[id].key !== key) return
      catalogs[id] = models
      suggestions[id] = suggested_models
      suggestionSources[id] = suggestion_source
      feedback[id] = models.length
        ? { type: 'success', message: `找到 ${models.length} 个匹配模型，请在「模型名称」下拉框中选择。` }
        : suggested_models.length
          ? {
              type: 'warning',
              message: suggestion_source === 'catalog'
                ? `已从该地址识别出 ${suggested_models.length} 个候选视频模型，可在下拉框中选择或直接手动输入。`
                : '目录未返回直接匹配的视频型号。下拉框已列出项目支持的型号，实际权限以服务商为准。'
            }
          : {
              type: 'warning',
              message: available
                ? '服务返回的列表中没有自动匹配当前分类的模型，可直接在输入框手动填写模型名称。'
                : '该地址未开放标准模型目录接口，可直接手动输入模型名称并保存使用。'
            }
    } else {
      const { result } = await settingsApi.testModel(id, payload)
      const [type, message] = messages[result] || ['warning', '未知测试结果']
      feedback[id] = { type, message }
    }
  } catch (error) {
    feedback[id] = { type: 'error', message: localizeError(error.message) }
  } finally {
    busy[id] = null
  }
}
</script>

<style scoped>
.model-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr)); gap:16px; }
.model-card { padding:20px; border:1px solid #dbe3ef; border-radius:14px; background:white; min-width:0; }
.model-card h3 { margin:0 0 8px; }
.model-card p { color:#475569; margin:0 0 12px; font-size:13px; min-height:38px; }
.model-card form { margin-top:12px; }
.key-status { color:#475569; font-size:12px; display:block; margin-top:4px; line-height:1.4; }
.key-status-warn { color:#d97706; font-weight:500; }
.option-hint { color:#64748b; font-size:12px; margin-left:4px; }
.model-actions { display:flex; flex-wrap:wrap; gap:8px; }
.model-actions .el-button { margin:0; }
.model-feedback { margin-top:12px; }
</style>
