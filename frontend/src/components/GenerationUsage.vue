<template>
  <el-card class="usage-card" shadow="never" v-loading="loading">
    <div class="usage-heading">
      <div>
        <h2>生成操作额度</h2>
        <p>新生成的图片和视频计入额度，复用已有素材不占额度。</p>
      </div>
      <el-button :disabled="loading || saving" @click="load">刷新记录</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="summary">
      <dl class="usage-stats">
        <div><dt>已占用</dt><dd>{{ summary.used }} 次</dd></div>
        <div><dt>剩余额度</dt><dd>{{ summary.remaining ?? '不限' }}{{ summary.remaining == null ? '' : ' 次' }}</dd></div>
        <div><dt>素材复用</dt><dd>{{ summary.reused }} 次</dd></div>
        <div v-if="summary.estimate"><dt>计划估计</dt><dd>约 {{ summary.estimate.total }} 次</dd></div>
      </dl>
      <p v-if="summary.scope_type === 'series'">同一连载的所有集数共享此额度。</p>
      <p v-if="summary.estimate">
        按当前时长估计约 {{ summary.estimate.images }} 次图片生成、{{ summary.estimate.videos }} 次视频生成。实际数量随分镜、复用和重新生成而变动。
      </p>
      <el-alert
        v-if="summary.remaining === 0"
        title="额度已用完。调高上限或取消限制后，可以继续生成。"
        type="warning"
        :closable="false"
        show-icon
      />

      <el-form class="usage-form" label-position="top" @submit.prevent="saveLimit">
        <el-form-item label="操作总上限">
          <el-input
            v-model="limitText"
            inputmode="numeric"
            placeholder="留空表示不限"
            :disabled="saving"
          />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="saving" :disabled="loading">保存额度</el-button>
      </el-form>
      <p>尚未确认结果的请求也计入额度。服务商的实际费用请以其账单为准。</p>

      <el-collapse>
        <el-collapse-item title="查看最近的执行记录" name="logs">
          <p v-if="!summary.logs.length">暂无生成记录。</p>
          <template v-else>
            <p>最多显示最近 {{ summary.log_limit }} 条记录。</p>
            <el-table :data="summary.logs" style="width: 100%">
              <el-table-column label="时间" min-width="160">
                <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="类型" min-width="100">
                <template #default="{ row }">{{ kindLabels[row.kind] || row.kind }}</template>
              </el-table-column>
              <el-table-column label="状态" min-width="120">
                <template #default="{ row }">{{ statusLabels[row.status] || row.status }}</template>
              </el-table-column>
              <el-table-column prop="scene_id" label="分镜" min-width="100" show-overflow-tooltip />
              <el-table-column v-if="summary.scope_type === 'series'" prop="task_id" label="单集任务" min-width="160" show-overflow-tooltip />
            </el-table>
          </template>
        </el-collapse-item>
      </el-collapse>
    </template>
  </el-card>
</template>

<script setup>
import { ref, watch } from 'vue'
import { usageApi } from '../api/usage'

const props = defineProps({
  scopeType: { type: String, required: true },
  scopeId: { type: String, required: true },
})

const summary = ref(null)
const limitText = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
let request = 0

const kindLabels = { image: '图片生成', video: '视频生成' }
const statusLabels = {
  reserved: '已预占',
  submitted: '已提交',
  success: '生成成功',
  unconfirmed: '结果未确认',
  failed: '生成失败',
  reused: '复用素材',
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

function applySummary(data) {
  summary.value = data
  limitText.value = data.limit == null ? '' : String(data.limit)
}

async function load() {
  if (!props.scopeId) return
  const id = ++request
  loading.value = true
  error.value = ''
  try {
    const data = await usageApi.get(props.scopeType, props.scopeId)
    if (id === request) applySummary(data)
  } catch (exception) {
    if (id === request) error.value = exception.message
  } finally {
    if (id === request) loading.value = false
  }
}

async function saveLimit() {
  if (saving.value || loading.value) return
  const text = limitText.value.trim()
  if (text && (!/^[1-9]\d*$/.test(text) || Number(text) > 2147483647)) {
    error.value = '请输入正整数，或留空取消限制。'
    return
  }
  const id = ++request
  saving.value = true
  error.value = ''
  try {
    const data = await usageApi.setLimit(props.scopeType, props.scopeId, text ? Number(text) : null)
    if (id === request) applySummary(data)
  } catch (exception) {
    if (id === request) error.value = exception.message
  } finally {
    if (id === request) saving.value = false
  }
}

watch(() => [props.scopeType, props.scopeId], () => {
  summary.value = null
  saving.value = false
  load()
}, { immediate: true })
</script>

<style scoped>
.usage-card { margin-top: 20px; }
.usage-heading, .usage-form { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.usage-heading { justify-content: space-between; margin-bottom: 16px; }
.usage-heading h2 { margin: 0 0 8px; font-size: 19px; }
p { color: #64748b; font-size: 13px; line-height: 1.6; }
.usage-stats { display: flex; flex-wrap: wrap; gap: 16px 32px; margin: 16px 0; }
.usage-stats dt { font-size: 13px; color: #64748b; }
.usage-stats dd { margin: 6px 0 0; font-size: 18px; font-weight: 600; }
.usage-form { align-items: end; margin-top: 16px; }
.usage-form :deep(.el-form-item) { margin-bottom: 0; }
</style>
