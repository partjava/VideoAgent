import axios from 'axios'

const client = axios.create({
  baseURL: '/api/video',
  timeout: 60000 // 60 seconds
})

// Add response interceptor to automatically unpack the ApiResponse schema
client.interceptors.response.use(
  (response) => {
    // If the backend returns our ApiResponse model {status, data, message}
    if (response.data && response.data.status === 'success') {
      return response.data.data
    }
    return response.data
  },
  (error) => {
    const errorMsg = error.response?.data?.message || error.message || '请求失败'
    return Promise.reject(new Error(errorMsg))
  }
)

export const videoApi = {
  // Create video task
  createTask(payload) {
    return client.post('/create', payload)
  },
  
  // Get task detail
  getTask(taskId) {
    return client.get(`/${taskId}`)
  },
  
  // Get script for a task
  getScript(taskId) {
    return client.get(`/${taskId}/script`)
  },

  // Get storyboard for a task
  getStoryboard(taskId) {
    return client.get(`/${taskId}/storyboard`)
  },

  // Get task execution progress
  getTaskProgress(taskId) {
    return client.get(`/${taskId}/progress`)
  },
  
  // Trigger real pipeline execution
  runPipeline(taskId, resume = true) {
    return client.post(`/${taskId}/run-pipeline?resume=${resume}`)
  },
  
  // Get history tasks list
  listTasks() {
    return client.get('/list')
  },

  // Delete a task
  deleteTask(taskId) {
    return client.delete(`/${taskId}`)
  }
}
