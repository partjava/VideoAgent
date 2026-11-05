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
            <el-input v-model.trim="profiles[item.id].url" @input="clearCatalog(item.id)" />
          </el-form-item>
          <el-form-item label="模型名称">
            <el-select v-model="profiles[item.id].model" filterable allow-create default-first-option
              placeholder="查询后选择，也可以输入模型名称" style="width:100%" @change="clearResult(item.id)">
              <el-option v-for="model in modelOptions(item.id)" :key="model" :label="model" :value="model">
                {{ model }}<span v-if="suggestions[item.id]?.includes(model)">（项目支持，权限待确认）</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model.trim="profiles[item.id].key" type="password" show-password
              autocomplete="new-password" @input="clearCatalog(item.id)"
              :placeholder="profiles[item.id].has_key ? '同一 URL 留空可沿用已有 Key' : '输入 API Key'" />
            <span class="key-status">{{ keySources[profiles[item.id].key_source] }}</span>
          </el-form-item>
          <div class="model-actions">
            <el-button :loading="busy[item.id] === 'catalog'" :disabled="!!busy[item.id]" @click="submit(item.id, 'catalog')">查询模型</el-button>
            <el-button :loading="busy[item.id] === 'test'" :disabled="!!busy[item.id]" @click="submit(item.id, 'test')">测试连接</el-button>
            <el-button type="primary" :loading="busy[item.id] === 'save'" :disabled="!!busy[item.id]" @click="submit(item.id, 'save')">保存配置</el-button>
            <el-button v-if="profiles[item.id].key_source === 'local'" type="danger" plain
              :loading="busy[item.id] === 'clear'" :disabled="!!busy[item.id]"
              @click="submit(item.id, 'clear')">清除本地 Key</el-button>
          </div>
        </el-form>
        <el-alert v-if="feedback[item.id]" :title="feedback[item.id].message" :type="feedback[item.id].type"
          :closable="false" show-icon class="model-feedback" />
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
  { id: 'prompt', title: '提示词模型', protocol: 'OpenAI 兼容 API，填写包含 /v1 的基础地址（如服务商要求）。' },
  { id: 'image', title: '图片生成模型', protocol: 'DashScope 原生图片接口，填写服务域名或代理的基础地址。' },
  { id: 'video', title: '视频生成模型', protocol: 'DashScope 原生 Wan 接口，填写服务域名或代理的基础地址。' },
]
const profiles = reactive({})
const feedback = reactive({})
const catalogs = reactive({})
const suggestions = reactive({})
const modelOptions = id => [...new Set([
  profiles[id].model, ...(catalogs[id] || []), ...(suggestions[id] || [])
].filter(Boolean))]
function clearCatalog(id) {
  delete catalogs[id]
  delete suggestions[id]
  clearResult(id)
}
const busy = reactive({})
const loadError = ref('')
const clearResult = id => { delete feedback[id] }
const keySources = { local: '已保存本地 Key', environment: '使用环境配置的 Key', none: '未配置 Key' }
const messages = {
  model_listed: ['success', '连接成功，服务返回的列表包含该模型。'],
  model_unlisted: ['warning', '接口已响应，模型列表未包含填写的模型。'],
  authenticated: ['success', '只读接口连接成功，当前 Key 可访问。生成权限和额度需以服务商为准。'],
  denied: ['error', '鉴权失败，检查 Key、工作空间和权限。'],
  balance: ['error', '服务报告余额不足。'],
  timeout: ['error', '连接超时。'],
  unreachable: ['error', '无法连接，检查 URL、网络和证书。'],
  unconfirmed: ['warning', '服务器有响应，但该只读接口无法确认 Key。'],
  http_error: ['warning', '服务返回异常，检查接口地址或稍后再试。'],
}

onMounted(async () => {
  try {
    for (const [id, config] of Object.entries(await settingsApi.getModels())) {
      profiles[id] = { ...config, key: '' }
    }
  } catch (error) {
    loadError.value = error.message
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
      feedback[id] = { type: 'success', message: key.trim() ? '新 Key 已保存。' : '已保存配置，沿用已有 Key。' }
    } else if (action === 'catalog') {
      const { models, available, suggested_models = [] } = await settingsApi.queryModels(id, payload)
      if (profiles[id].url !== url || profiles[id].key !== key) return
      catalogs[id] = models
      suggestions[id] = suggested_models
      feedback[id] = models.length
        ? { type: 'success', message: `找到 ${models.length} 个模型，在模型名称下拉框选择。` }
        : suggested_models.length
          ? { type: 'warning', message: '目录未返回匹配的视频型号。下拉框已列出项目支持的型号，实际权限以阿里云为准。' }
        : { type: 'warning', message: available
          ? '服务返回的列表中没有匹配当前类型的模型，可继续手动填写。'
          : '该地址未返回模型目录，可继续使用当前模型或手动填写。' }
    } else {
      const { result } = await settingsApi.testModel(id, payload)
      const [type, message] = messages[result]
      feedback[id] = { type, message }
    }
  } catch (error) {
    feedback[id] = { type: 'error', message: error.message }
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
.key-status { color:#475569; font-size:12px; }
.model-actions { display:flex; flex-wrap:wrap; gap:8px; }
.model-actions .el-button { margin:0; }
.model-feedback { margin-top:12px; }
</style>
