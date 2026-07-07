import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/assets': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        bypass: function (req, res, proxyOptions) {
          // 如果请求的是前端打包的静态资源（比如以 .js, .css 结尾，或者包含 index- 等前缀），不走代理
          if (req.url.match(/\.(js|css|ico|woff2?|svg|vue)$/) || req.url.includes('/assets/index-')) {
            return req.url;
          }
        }
      },
      '/outputs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
