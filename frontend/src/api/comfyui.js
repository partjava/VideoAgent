import axios from 'axios'

const client = axios.create({
  baseURL: '/api/comfyui',
  timeout: 30000
})

const longClient = axios.create({
  baseURL: '/api/comfyui',
  timeout: 600000  // 10 分钟，给 ComfyUI 生成留够时间
})

// 给 longClient 加上同样的响应拦截
longClient.interceptors.response.use(
  (response) => {
    if (response.data && response.data.status === 'success') {
      return response.data.data
    }
    return response.data
  },
  (error) => {
    const msg = error.response?.data?.message || error.message || 'Request failed'
    return Promise.reject(new Error(msg))
  }
)


client.interceptors.response.use(
  (response) => {
    if (response.data && response.data.status === 'success') {
      return response.data.data
    }
    return response.data
  },
  (error) => {
    const msg = error.response?.data?.message || error.message || 'Request failed'
    return Promise.reject(new Error(msg))
  }
)

export const comfyuiApi = {
  getConfig() {
    return client.get('/config')
  },
  saveConfig(payload) {
    return client.post('/config', payload)
  },
  checkConnection(payload) {
    return client.post('/check', payload)
  },
  getWorkflows() {
    return client.get('/workflows')
  },
  saveImageWorkflow(payload) {
    return client.post('/workflows/image', payload)
  },
  saveVideoWorkflow(payload) {
    return client.post('/workflows/video', payload)
  },
  testImage(payload) {
    return client.post('/test-image', payload)
  },
  getImageResult(promptId) {
    return longClient.get(`/test-image/${promptId}`)
  },
  testVideo(payload) {
    return client.post('/test-video', payload)
  },
  getVideoResult(promptId) {
    return longClient.get(`/test-video/${promptId}`)
  },
  getProgress(promptId) {
    return client.get(`/progress/${promptId}`)
  },
  getTestHistory() {
    return client.get('/history')
  },
  getStories() {
    return client.get('/stories')
  },
  generatePrompt(payload) {
    return client.post('/generate-prompt', payload)
  }
}
