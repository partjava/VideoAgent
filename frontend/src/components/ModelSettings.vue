<template>
  <section class="model-grid" aria-label="Model configuration">
    <article
      v-for="item in stages"
      :key="item.id"
      class="model-card"
      :class="{ active: profiles[item.id]?.active }"
    >
      <div class="model-card-head">
        <h3>{{ item.title }}</h3>
        <el-tag v-if="profiles[item.id]" size="small" :type="profiles[item.id].active ? 'success' : 'info'" effect="light">
          {{ profiles[item.id].active ? '已启用' : '保存后启用' }}
        </el-tag>
      </div>
      <p>{{ item.protocol }}</p>
      <template v-if="profiles[item.id]">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="API URL">
            <el-input
              v-model.trim="profiles[item.id].url"
              placeholder="例如 https://api.siliconflow.cn/v1 或 https://api.deepseek.com/v1"
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
        <div v-if="(catalogs[item.id]?.length || suggestions[item.id]?.length)" class="candidate-chips">
          <small>点击可直接切换使用：</small>
          <button
            v-for="m in (catalogs[item.id] || [])"
            :key="'c-' + m"
            type="button"
            class="candidate-chip primary"
            :class="{ active: profiles[item.id].model === m }"
            @click="selectCandidate(item.id, m)"
          >
            {{ m }}
          </button>
          <button
            v-for="m in (suggestions[item.id] || [])"
            :key="'s-' + m"
            type="button"
            class="candidate-chip"
            :class="{ active: profiles[item.id].model === m }"
            @click="selectCandidate(item.id, m)"
          >
            {{ m }}
          </button>
        </div>
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
  { id: 'prompt', title: '提示词模型', protocol: '支持 OpenAI / DeepSeek / 硅基流动 / 通义千问兼容地址（自动适配 /v1/models）。' },
  { id: 'image', title: '图片生成模型', protocol: '支持硅基流动（推荐 Qwen/Qwen-Image-Edit-2509 + Qwen/Qwen-Image 自动配对：定妆三视图/尾帧参考编辑 + 满画幅实景首帧，亦支持 FLUX.1-Kontext-dev）及 DashScope 原生接口。' },
  { id: 'video', title: '视频生成模型', protocol: '支持硅基流动（MiniMax-H3 等）及 DashScope Wan 异步接口，自动按协议调用。' },
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

const normalizeBase = url =>
  String(url || '')
    .trim()
    .replace(/\/+$/, '')
    .replace(/\/(?:compatible-mode\/v1|api\/v1|v1)$/i, '')

const isUrlChanged = id => {
  const current = normalizeBase(profiles[id]?.url)
  const saved = normalizeBase(savedUrls[id])
  return Boolean(saved && current && current !== saved)
}

function selectCandidate(id, model) {
  profiles[id].model = model
  clearResult(id)
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
const keySources = { local: '已保存到数据库与本地缓存（实时生效）', environment: '使用环境配置的 Key', none: '未配置 Key' }
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
      feedback[id] = { type: 'success', message: key.trim() ? '配置与新 Key 已保存到数据库与本地缓存（实时生效，无需重启）。' : '已保存配置并同步到数据库，沿用已有 Key。' }
    } else if (action === 'catalog') {
      const { models, available, suggested_models = [], suggestion_source = 'none' } = await settingsApi.queryModels(id, payload)
      if (profiles[id].url !== url || profiles[id].key !== key) return
      catalogs[id] = models
      suggestions[id] = suggested_models
      suggestionSources[id] = suggestion_source
      if (models.length && suggested_models.length) {
        feedback[id] = {
          type: 'success',
          message: suggestion_source === 'official'
            ? `目录返回 ${models.length} 个模型，并已补充 ${suggested_models.length} 个项目支持的官方候选型号（实际权限以百炼控制台为准）。`
            : `找到 ${models.length} 个匹配模型及 ${suggested_models.length} 个兼容候选模型，可直接点击下方标签或下拉框选择。`,
        }
      } else if (models.length) {
        feedback[id] = { type: 'success', message: `找到 ${models.length} 个匹配模型，可直接点击下方标签或「模型名称」下拉框选择。` }
      } else if (suggested_models.length) {
        feedback[id] = {
          type: suggestion_source === 'catalog' ? 'success' : 'warning',
          message: suggestion_source === 'catalog'
            ? `已从该地址识别出 ${suggested_models.length} 个候选视频模型，可直接点击下方标签选用。`
            : '百炼兼容目录未直接返回生图/视频型号，下方已列出当前通道项目支持的官方候选型号（需在百炼模型广场开通权限）。',
        }
      } else {
        feedback[id] = {
          type: 'warning',
          message: available
            ? '服务返回的列表中没有自动匹配当前分类的模型，可直接在输入框手动填写模型名称。'
            : '该地址未开放标准模型目录接口，可直接手动输入模型名称并保存使用。',
        }
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
.model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 18px;
}
.model-card {
  padding: 20px;
  border: 1px solid var(--va-border);
  border-top: 3px solid var(--va-border-strong);
  border-radius: var(--va-radius-lg);
  background: var(--va-surface);
  box-shadow: var(--va-shadow-sm);
  min-width: 0;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}
.model-card.active {
  border-top-color: var(--va-brand);
}
.model-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.model-card h3 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 800;
}
.model-card p {
  color: var(--va-ink-muted);
  margin: 0 0 14px;
  font-size: 0.8125rem;
  line-height: 1.55;
  min-block-size: 3.1em;
}
.model-card form { margin-top: 8px; }
.key-status { color: var(--va-ink-muted); font-size: 0.75rem; display: block; margin-top: 4px; line-height: 1.4; }
.key-status-warn { color: #d97706; font-weight: 600; }
.option-hint { color: var(--va-ink-muted); font-size: 0.75rem; margin-left: 4px; }
.model-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.model-actions .el-button { margin: 0; }
.model-feedback { margin-top: 12px; }
.candidate-chips { display: flex; flex-wrap: wrap; flex-wrap: balance; align-items: center; gap: 6px; margin-top: 12px; }
.candidate-chips small { color: var(--va-ink-muted); font-size: 0.75rem; width: 100%; }
.candidate-chip { border: 1px solid var(--va-border-strong); background: var(--va-surface-subtle); color: var(--va-ink-secondary); font-size: 0.75rem; font-weight: 600; padding: 3px 10px; border-radius: 999px; cursor: pointer; }
.candidate-chip.primary { border-color: var(--va-brand-border); background: var(--va-brand-soft); color: var(--va-brand-hover); }
.candidate-chip.active { background: var(--va-brand); border-color: var(--va-brand); color: #ffffff; }
</style>
