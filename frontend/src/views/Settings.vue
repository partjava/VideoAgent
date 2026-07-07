<template>
  <div class="settings-page">
    <div class="page-header">
      <h2 class="page-heading">API Key 配置</h2>
      <el-button text @click="$router.push('/')" size="large">
        <el-icon size="18"><ArrowLeft /></el-icon>
        <span style="font-size:16px">返回</span>
      </el-button>
    </div>
    <p class="page-desc">填写各平台 Key，保存后自动写入 <code>.env</code> 文件。</p>

    <el-alert
      v-if="saveSuccess"
      title="保存成功"
      type="success"
      :description="'已保存 ' + savedCount + ' 个 Key'"
      show-icon
      closable
      class="mb-4"
      @close="saveSuccess = false"
    />

    <el-card class="keys-card">
      <el-form
        ref="formRef"
        :model="form"
        label-width="160px"
        label-position="left"
        size="large"
      >
        <!-- DeepSeek -->
        <el-form-item
          label="DeepSeek"
          prop="deepseek_api_key"
          :rules="[{ required: true, message: 'DeepSeek Key 必填', trigger: 'blur' }]"
        >
          <div class="key-row">
            <el-input
              v-model="form.deepseek_api_key"
              type="password" show-password
              placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              class="key-input"
            />
            <el-button
              size="default"
              :loading="testing === 'deepseek'"
              :type="testStatus.deepseek === true ? 'success' : testStatus.deepseek === false ? 'danger' : 'default'"
              @click="testOne('deepseek_api_key')"
            >
              {{ testStatus.deepseek === true ? '正常' : testStatus.deepseek === false ? '失败' : '测试' }}
            </el-button>
          </div>
          <div class="field-hint">脚本生成、分镜设计、提示词</div>
          <div v-if="testMsg.deepseek" class="test-msg" :class="testStatus.deepseek ? 'ok' : 'fail'">{{ testMsg.deepseek }}</div>
        </el-form-item>

        <el-divider />

        <!-- DashScope -->
        <el-form-item
          label="DashScope"
          prop="dashscope_api_key"
          :rules="[{ required: true, message: 'DashScope Key 必填', trigger: 'blur' }]"
        >
          <div class="key-row">
            <el-input
              v-model="form.dashscope_api_key"
              type="password" show-password
              placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              class="key-input"
            />
            <el-button
              size="default"
              :loading="testing === 'dashscope'"
              :type="testStatus.dashscope === true ? 'success' : testStatus.dashscope === false ? 'danger' : 'default'"
              @click="testOne('dashscope_api_key')"
            >
              {{ testStatus.dashscope === true ? '正常' : testStatus.dashscope === false ? '失败' : '测试' }}
            </el-button>
          </div>
          <div class="field-hint">Qwen-Image 图片生成 + Wan 图生视频</div>
          <div v-if="testMsg.dashscope" class="test-msg" :class="testStatus.dashscope ? 'ok' : 'fail'">{{ testMsg.dashscope }}</div>
        </el-form-item>

        <el-divider />

        <!-- Vidu（可选） -->
        <el-form-item label="Vidu（可选）" prop="vidu_api_token">
          <div class="key-row">
            <el-input
              v-model="form.vidu_api_token"
              type="password" show-password
              placeholder="vda_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              class="key-input"
            />
            <el-button
              size="default"
              :loading="testing === 'vidu'"
              :type="testStatus.vidu === true ? 'success' : testStatus.vidu === false ? 'danger' : 'default'"
              @click="testOne('vidu_api_token')"
            >
              {{ testStatus.vidu === true ? '正常' : testStatus.vidu === false ? '失败' : '测试' }}
            </el-button>
          </div>
          <div class="field-hint">可选，Wan 失败时的备选视频服务</div>
          <div v-if="testMsg.vidu" class="test-msg" :class="testStatus.vidu ? 'ok' : 'fail'">{{ testMsg.vidu }}</div>
        </el-form-item>

        <el-divider />

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving" size="large">
            保存配置
          </el-button>
          <el-button @click="handleReset" size="large">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 已配置状态 -->
    <el-card class="status-card mt-4">
      <template #header><span>当前配置状态</span></template>
      <div class="status-grid">
        <div v-for="(info, key) in keyStatus" :key="key" class="status-item">
          <span class="status-label">{{ info.label }}</span>
          <el-tag :type="info.configured ? 'success' : 'danger'" effect="plain" size="small">
            {{ info.configured ? '已配置' : '未配置' }}
          </el-tag>
          <span v-if="info.masked" class="status-masked">{{ info.masked }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsApi } from '../api/settings.js'

const formRef = ref(null)
const saving = ref(false)
const saveSuccess = ref(false)
const savedCount = ref(0)
const keyStatus = ref({})

// 逐 Key 测试状态
const testing = ref(null) // 'deepseek' | 'dashscope' | 'vidu' | null
const testStatus = reactive({ deepseek: null, dashscope: null, vidu: null })
const testMsg = reactive({ deepseek: '', dashscope: '', vidu: '' })

// 后端 key 名 → 前端简称
const KEY_NAMES = {
  deepseek_api_key: 'deepseek',
  dashscope_api_key: 'dashscope',
  vidu_api_token: 'vidu',
}

const form = reactive({
  deepseek_api_key: '',
  dashscope_api_key: '',
  vidu_api_token: '',
})

async function loadStatus() {
  try {
    keyStatus.value = await settingsApi.getKeys()
  } catch (e) {
    console.error('加载配置状态失败:', e)
  }
}

async function handleSave() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch { return }

  saving.value = true
  try {
    const payload = {}
    let count = 0
    for (const [key, val] of Object.entries(form)) {
      if (val && val.trim()) { payload[key] = val.trim(); count++ }
    }
    savedCount.value = count
    await settingsApi.saveKeys(payload)
    saveSuccess.value = true
    await loadStatus()
    for (const key of Object.keys(form)) form[key] = ''
  } catch (e) {
    ElMessage.error('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}

async function testOne(formKey) {
  const name = KEY_NAMES[formKey]
  if (!name) return

  const val = form[formKey]
  if (!val || !val.trim()) {
    testStatus[name] = false
    testMsg[name] = '请先填写 Key 再测试'
    return
  }

  testing.value = name
  testStatus[name] = null
  testMsg[name] = ''

  try {
    const res = await settingsApi.testKeys({ [formKey]: val.trim() })
    const r = res[formKey]
    if (r) {
      testStatus[name] = r.ok
      testMsg[name] = r.msg
    } else {
      testStatus[name] = false
      testMsg[name] = '无响应'
    }
  } catch (e) {
    testStatus[name] = false
    testMsg[name] = '请求失败: ' + e.message
  } finally {
    testing.value = null
  }
}

function handleReset() {
  for (const key of Object.keys(form)) form[key] = ''
  for (const k of Object.keys(testStatus)) { testStatus[k] = null; testMsg[k] = '' }
}

onMounted(() => { loadStatus() })
</script>

<style scoped>
.settings-page { max-width: 680px; margin: 0 auto; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.page-heading { font-size: 22px; font-weight: 700; color: #0f172a; margin: 0; }
.page-desc { color: #64748b; font-size: 14px; margin-bottom: 24px; }
.page-desc code { background: #f1f5f9; padding: 2px 8px; border-radius: 4px; font-size: 13px; }
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }

.key-row { display: flex; gap: 8px; width: 100%; }
.key-input { flex: 1; }

.field-hint { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.test-msg { font-size: 12px; margin-top: 2px; }
.test-msg.ok { color: #22c55e; }
.test-msg.fail { color: #ef4444; }

.keys-card :deep(.el-form-item__label) { font-weight: 600; color: #334155; }
.status-card :deep(.el-card__header) { font-weight: 600; color: #0f172a; }
.status-grid { display: flex; flex-direction: column; gap: 12px; }
.status-item { display: flex; align-items: center; gap: 16px; }
.status-label { width: 160px; font-size: 14px; color: #334155; }
.status-masked { font-size: 13px; color: #94a3b8; font-family: monospace; }
</style>
