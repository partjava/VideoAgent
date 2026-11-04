import axios from 'axios'

const client = axios.create({
  baseURL: '/api/settings',
  timeout: 15000
})

client.interceptors.response.use(
  (response) => {
    if (response.data && response.data.status === 'success') {
      return response.data.data
    }
    return response.data
  },
  (error) => {
    const msg = (typeof error.response?.data?.detail === "string" && error.response.data.detail) || error.response?.data?.message || error.message || '请求失败'
    return Promise.reject(new Error(msg))
  }
)

export const settingsApi = {
  getModels() {
    return client.get('/models')
  },
  queryModels(kind, payload) {
    return client.post(`/models/${kind}/catalog`, payload)
  },
  saveModel(kind, payload) {
    return client.put(`/models/${kind}`, payload)
  },
  testModel(kind, payload) {
    return client.post(`/models/${kind}/test`, payload)
  },
  getKeys() {
    return client.get('/keys')
  },
  saveKeys(payload) {
    return client.post('/keys', payload)
  },
  checkKeys() {
    return client.get('/keys/check')
  },
  testKeys(payload) {
    return client.post('/keys/test', payload)
  }
}
