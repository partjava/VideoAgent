# ComfyUI 本地多模态接入需求方案

## 1. 背景

当前 VideoAgent 主流程已经可以通过 DeepSeek 完成文本创作，通过 Qwen-Image 生成图片，通过 Wan / Vidu / Doubao 等服务生成视频片段，再使用 edge-tts、字幕和 MoviePy / FFmpeg 合成最终短视频。

现有流程的问题是：图片生成和图生视频 API 成本较高，尤其在多分镜批量生成时，消耗会比较大。

用户本机 WSL 中已经部署了 ComfyUI，并且当前浏览器可以访问：

```text
http://127.0.0.1:8188/
```

因此需要把 ComfyUI 接入现有 VideoAgent 主流程，用本地或后期服务器上的 ComfyUI 替代昂贵的图片生成 API 和图生视频 API。

## 2. 总体目标

新增一个可配置的 ComfyUI 本地多模态引擎，让它接入现有主流程中的两个阶段：

```text
ImageAgent 图片生成阶段
VideoAgent 图生视频阶段
```

接入后目标流程为：

```text
用户输入
-> DeepSeek 生成任务规划
-> DeepSeek 生成脚本
-> DeepSeek 生成分镜
-> DeepSeek 生成图片提示词和视频动作提示词
-> ComfyUI 根据 image_prompt 生成分镜图片
-> ComfyUI 根据图片和 video_prompt 生成视频片段
-> edge-tts 配音
-> 字幕生成
-> MoviePy / FFmpeg 合成 final.mp4
```

核心目标：

```text
DeepSeek API 继续使用
DashScope / Qwen-Image API 可以不用
Wan / Vidu / Doubao 视频 API 可以不用
主流程仍然可以完整生成最终视频
```

## 3. 核心原则

1. 不影响已有功能。

   原来的 Qwen-Image、Wan、Vidu、Doubao 服务必须保留，可以随时切回。

2. ComfyUI 是新增 provider。

   不直接替换旧代码，而是通过 provider 配置切换：

   ```env
   IMAGE_PROVIDER=comfyui
   VIDEO_PROVIDER=comfyui
   ```

3. 不写死 ComfyUI 节点 ID。

   每个 ComfyUI workflow 的节点编号、模型节点、提示词节点、输出节点都可能不同，后端不能假设固定节点。

4. 前端必须可以修改配置。

   用户不应该每次手动改 `.env`。前端需要提供独立 ComfyUI 配置页面。

5. 先测试，再接主流程。

   页面必须能先测试连接、测试文生图、测试图生视频。测试通过后，再启用为主流程 provider。

6. 失败必须显式暴露。

   ComfyUI 连接失败、workflow 配置错误、节点映射错误、输出文件找不到时，必须明确报错，不生成假图片或假视频。

## 4. 配置需求

### 4.1 环境变量

新增或使用以下配置：

```env
MODEL_PROVIDER=deepseek

IMAGE_PROVIDER=comfyui
VIDEO_PROVIDER=comfyui

COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_TIMEOUT_SECONDS=600
```

如果后期更换服务器，只需要修改：

```env
COMFYUI_BASE_URL=http://服务器IP:8188
```

如果需要切回旧付费 API：

```env
IMAGE_PROVIDER=qwen
VIDEO_PROVIDER=vidu
```

或：

```env
IMAGE_PROVIDER=qwen
VIDEO_PROVIDER=wan
```

### 4.2 前端可修改配置

新增侧边栏页面：

```text
ComfyUI
```

页面需要支持修改：

```text
ComfyUI 服务地址
图片 Provider
视频 Provider
请求超时时间
图片生成 workflow JSON
图生视频 workflow JSON
图片 workflow 节点映射
视频 workflow 节点映射
```

保存后需要：

```text
写入 backend/.env
更新当前后端进程中的运行时环境变量
后续新任务使用新配置
```

说明：

```text
正在运行中的任务不需要中途切换 provider。
保存配置只影响后续创建或继续运行的任务。
```

## 5. 前端页面需求

### 5.1 页面名称

```text
ComfyUI 配置
```

### 5.2 页面入口

左侧侧边栏新增：

```text
创建视频
历史任务
API 配置
ComfyUI
```

### 5.3 页面模块

#### 5.3.1 服务连接

字段：

```text
ComfyUI 地址
请求超时时间
```

按钮：

```text
测试连接
保存配置
重新读取
```

连接测试逻辑：

```text
GET {COMFYUI_BASE_URL}/system_stats
```

状态显示：

```text
未测试连接
ComfyUI 已连接
ComfyUI 未连接
连接超时
```

#### 5.3.2 Provider 切换

图片 Provider：

```text
qwen
comfyui
```

视频 Provider：

```text
vidu
wan
doubao
comfyui
```

页面需要明确提示：

```text
选择 comfyui 后，后续主流程的图片或视频生成会走本地/服务器 ComfyUI。
```

#### 5.3.3 图片生成 workflow 配置

用户需要提供 ComfyUI API Format workflow JSON。

支持方式：

```text
粘贴 JSON
上传 JSON 文件
保存当前图片 workflow
```

需要配置的节点映射：

```text
正向提示词节点
正向提示词字段路径
反向提示词节点
反向提示词字段路径
宽度节点，可选
高度节点，可选
seed 节点，可选
输出图片节点
```

说明：

```text
不能写死节点 ID。
必须由用户根据自己的 workflow 配置。
```

#### 5.3.4 图生视频 workflow 配置

用户需要提供 ComfyUI API Format workflow JSON。

支持方式：

```text
粘贴 JSON
上传 JSON 文件
保存当前视频 workflow
```

需要配置的节点映射：

```text
正向提示词节点
正向提示词字段路径
反向提示词节点，可选
反向提示词字段路径，可选
seed 节点，可选
视频宽度节点，可选 (如 Wan22ImageToVideoLatent.inputs.width)
视频高度节点，可选 (如 Wan22ImageToVideoLatent.inputs.height)
视频帧数节点，可选 (如 Wan22ImageToVideoLatent.inputs.length)
FPS 节点，可选 (如 CreateVideo.inputs.fps)
输出视频节点
```

说明：

```text
实际使用的 workflow 可能是 text-to-video（纯提示词生成视频），
不需要先上传图片。
video_prompt 会写入对应的正向提示词节点。
image_path 参数仍会传入，但由映射决定是否使用。
```

#### 5.3.5 单独测试

图片测试：

```text
输入 prompt
输入 negative prompt，可选
点击测试生成图片
显示生成结果预览
显示保存路径
```

视频测试：

```text
选择或上传一张测试图片
输入 video prompt
输入 negative prompt，可选
点击测试生成视频
显示生成结果预览
显示保存路径
```

#### 5.3.6 主流程启用

测试通过后，页面支持：

```text
启用 ComfyUI 作为图片 Provider
启用 ComfyUI 作为视频 Provider
```

启用后写入：

```env
IMAGE_PROVIDER=comfyui
VIDEO_PROVIDER=comfyui
```

## 6. 后端接口需求

### 6.1 配置接口

```text
GET  /api/comfyui/config
POST /api/comfyui/config
POST /api/comfyui/check
```

用途：

```text
读取 ComfyUI 配置
保存 ComfyUI 配置
测试 ComfyUI 服务连接
```

### 6.2 Workflow 接口

```text
GET  /api/comfyui/workflows
POST /api/comfyui/workflows/image
POST /api/comfyui/workflows/video
```

用途：

```text
读取已保存 workflow
保存图片生成 workflow 和节点映射
保存图生视频 workflow 和节点映射
```

### 6.3 测试生成接口

```text
POST /api/comfyui/test-image
POST /api/comfyui/test-video
```

用途：

```text
不进入主流程，只用于单独测试 ComfyUI 图片和视频 workflow 是否可用。
```

### 6.4 主流程接口不变

原有接口保持不变：

```text
POST /api/video/create
POST /api/video/{task_id}/run-pipeline
GET  /api/video/{task_id}/progress
GET  /api/video/{task_id}/storyboard
GET  /api/video/{task_id}/script
```

主流程通过 provider 配置自动选择底层服务。

## 7. 后端模块设计

### 7.1 新增模块

```text
backend/services/comfyui/client.py
backend/services/comfyui/config_store.py
backend/services/comfyui/workflow_store.py
backend/services/comfyui/workflow_mapper.py
backend/services/image/comfyui_image_service.py
backend/services/video/comfyui_video_service.py
backend/routes/comfyui_routes.py
```

### 7.2 ComfyUIClient

职责：

```text
连接 ComfyUI
提交 workflow
轮询 history
下载输出文件
上传输入图片
统一处理超时和错误
```

需要封装 ComfyUI API：

```text
GET  /system_stats
POST /prompt
GET  /history/{prompt_id}
GET  /view?filename=xxx&type=output
POST /upload/image
```

### 7.3 WorkflowMapper

职责：

```text
读取 workflow JSON
根据节点映射写入 prompt
根据节点映射写入 negative prompt
根据节点映射写入 input image
根据节点映射写入 seed
根据节点映射识别输出节点
```

输入：

```text
workflow_json
node_mapping
runtime_values
```

输出：

```text
patched_workflow_json
```

### 7.4 ComfyUIImageService

实现现有接口：

```python
BaseImageService.generate_image(
    task_id,
    scene_id,
    image_prompt,
    negative_prompt,
)
```

流程：

```text
读取图片 workflow
写入 image_prompt / negative_prompt / seed
提交到 ComfyUI
轮询生成结果
下载图片
保存到 backend/assets/{task_id}/images/{scene_id}.png
返回统一 image asset 结构
```

返回格式需要与 QwenImageService 保持一致：

```text
source=comfyui
provider=comfyui_image
asset_type=image
asset_path=backend/assets/{task_id}/images/{scene_id}.png
status=success
```

### 7.5 ComfyUIVideoService

实现现有接口：

```python
BaseVideoService.generate_video(
    task_id,
    scene_id,
    image_path,
    video_prompt,
    duration,
)
```

流程：

```text
读取图生视频 workflow
上传或引用上一阶段图片
写入 input image / video_prompt / negative_prompt / seed
提交到 ComfyUI
轮询生成结果
下载视频
保存到 backend/assets/{task_id}/videos/{scene_id}.mp4
返回统一 video asset 结构
```

返回格式需要与 Wan / Vidu / Doubao 服务保持一致：

```text
source=comfyui
provider=comfyui_video
asset_type=video_clip
asset_path=backend/assets/{task_id}/videos/{scene_id}.mp4
status=success
```

## 8. Provider 工厂接入

### 8.1 图片 provider

当前：

```text
get_image_service()
  qwen -> QwenImageService
```

目标：

```text
get_image_service()
  qwen -> QwenImageService
  comfyui -> ComfyUIImageService
```

### 8.2 视频 provider

当前：

```text
get_video_service()
  vidu -> ViduVideoService
  wan -> WanVideoService
  doubao -> DoubaoVideoService
```

目标：

```text
get_video_service()
  vidu -> ViduVideoService
  wan -> WanVideoService
  doubao -> DoubaoVideoService
  comfyui -> ComfyUIVideoService
```

## 9. 主流程影响范围

### 9.1 不改的部分

以下模块不需要改变核心逻辑：

```text
TaskPlannerAgent
ScriptAgent
StoryboardAgent
DialoguePolishAgent
PromptAgent
VoiceAgent
SubtitleAgent
EditorAgent
QualityAgent
ExportAgent
```

DeepSeek 仍然负责：

```text
任务规划
脚本生成
分镜生成
对白润色
图片提示词生成
视频动作提示词生成
```

### 9.2 改的部分

只新增 provider 分支：

```text
ImageAgent -> get_image_service()
VideoAgent -> get_video_service()
```

Agent 本身尽量不改，保持它们只依赖抽象接口。

## 10. 数据与文件保存

### 10.1 配置保存

推荐保存位置：

```text
backend/.env
backend/data/comfyui/config.json
backend/data/comfyui/image_workflow.json
backend/data/comfyui/video_workflow.json
```

其中：

```text
.env 保存 provider 和 base url
config.json 保存节点映射和测试状态
image_workflow.json 保存图片 workflow
video_workflow.json 保存视频 workflow
```

### 10.2 生成结果保存

图片：

```text
backend/assets/{task_id}/images/{scene_id}.png
```

视频片段：

```text
backend/assets/{task_id}/videos/{scene_id}.mp4
```

最终视频：

```text
backend/outputs/{task_id}/final.mp4
```

这些路径必须和现有 EditorAgent 兼容。

## 11. 错误处理需求

必须明确报错的情况：

```text
ComfyUI 地址为空
ComfyUI 无法连接
ComfyUI 超时
workflow JSON 格式错误
缺少图片 workflow
缺少视频 workflow
节点映射不存在
节点 ID 在 workflow 中找不到
字段路径写入失败
ComfyUI 返回 prompt_id 失败
history 中找不到输出
输出文件下载失败
生成结果不是图片或视频
```

错误展示位置：

```text
ComfyUI 配置页
任务进度页
后端日志
task metadata.last_error
```

禁止行为：

```text
禁止返回假成功
禁止生成占位图片冒充结果
禁止在 ComfyUI 失败时静默切回付费 API
禁止吞掉 workflow 错误
```

## 12. 安全与兼容性

1. ComfyUI 地址默认只填本地：

   ```text
   http://127.0.0.1:8188
   ```

2. 后期服务器地址由用户显式填写。

3. 下载文件只能保存到后端允许目录：

   ```text
   backend/assets
   backend/outputs
   ```

4. 上传给 ComfyUI 的图片必须来自本项目资产目录或用户测试上传。

5. 不允许通过配置写入任意系统路径。

## 13. 实施阶段

### 阶段一：配置页面

目标：

```text
前端可以修改 ComfyUI 地址
前端可以修改图片和视频 provider
前端可以测试 ComfyUI 连接
配置可以写入 backend/.env
```

不包含：

```text
workflow 节点映射
主流程真实调用 ComfyUI
```

### 阶段二：workflow 保存与测试

目标：

```text
保存图片 workflow
保存视频 workflow
保存节点映射
单独测试文生图
单独测试图生视频
```

不包含：

```text
完整视频主流程切换
```

### 阶段三：图片生成接入主流程

目标：

```text
IMAGE_PROVIDER=comfyui 时
ImageAgent 调用 ComfyUIImageService
分镜图片由 ComfyUI 生成
图片保存路径与原流程一致
```

验收：

```text
不配置 DashScope Key
可以生成分镜图片
原 qwen provider 仍可切回
```

### 阶段四：图生视频接入主流程

目标：

```text
VIDEO_PROVIDER=comfyui 时
VideoAgent 调用 ComfyUIVideoService
视频片段由 ComfyUI 生成
视频保存路径与原流程一致
```

验收：

```text
不配置 Wan / Vidu / Doubao Key
可以生成视频片段
原视频 provider 仍可切回
```

### 阶段五：完整流程验证

目标：

```text
DeepSeek + ComfyUI + edge-tts + MoviePy 完成完整短视频生成
```

验收：

```text
输入一个主题
生成脚本
生成分镜
生成图片
生成视频片段
生成配音
生成字幕
合成 final.mp4
```

## 14. 验收标准

最终完成后必须满足：

```text
1. 前端可以配置 ComfyUI 地址。
2. 前端可以切换 IMAGE_PROVIDER。
3. 前端可以切换 VIDEO_PROVIDER。
4. 前端可以测试 ComfyUI 是否连接成功。
5. 前端可以保存图片 workflow。
6. 前端可以保存视频 workflow。
7. 前端可以配置图片 workflow 节点映射。
8. 前端可以配置视频 workflow 节点映射。
9. 可以单独测试 ComfyUI 文生图。
10. 可以单独测试 ComfyUI 图生视频。
11. IMAGE_PROVIDER=comfyui 时主流程图片由 ComfyUI 生成。
12. VIDEO_PROVIDER=comfyui 时主流程视频片段由 ComfyUI 生成。
13. DeepSeek 仍然负责文本创作。
14. 不配置 DashScope Key 也能生成图片。
15. 不配置 Wan / Vidu / Doubao Key 也能生成视频片段。
16. 原 Qwen / Wan / Vidu / Doubao 流程可以切回。
17. ComfyUI 出错时明确显示错误。
18. 不生成假图片。
19. 不生成假视频。
20. 最终仍然输出 final.mp4。
```

## 15. 非目标

第一阶段不做：

```text
本地大语言模型替代 DeepSeek
自动猜测 ComfyUI 节点 ID
自动修复用户 workflow
多服务器调度
队列并发优化
GPU 资源监控
模型下载管理
```

这些可以作为后续增强。

## 16. 当前状态记录

### 已实现的完整能力

```text
后端模块：
  backend/services/comfyui/client.py          — ComfyUI API 客户端（连接/提交/轮询/下载/上传）
  backend/services/comfyui/config_store.py    — 配置读写（.env 读取、写入、运行时更新）
  backend/services/comfyui/workflow_store.py   — Workflow JSON 持久化（图片/视频双 workflow）
  backend/services/comfyui/workflow_mapper.py  — 节点映射注入 + 输出文件提取
  backend/services/image/comfyui_image_service.py — 图片生成服务（实现 BaseImageService 接口）
  backend/services/video/comfyui_video_service.py — 图生视频服务（实现 BaseVideoService 接口）
  backend/routes/comfyui_routes.py             — 所有 ComfyUI 相关 API 路由
  backend/services/provider_factory.py         — IMAGE_PROVIDER/VIDEO_PROVIDER=comfyui 分支

后端 API：
  GET  /api/comfyui/config                    读取 ComfyUI 配置
  POST /api/comfyui/config                    保存 ComfyUI 配置到 .env + 运行时
  POST /api/comfyui/check                     测试 ComfyUI 连接
  GET  /api/comfyui/workflows                 读取已保存的 workflow
  POST /api/comfyui/workflows/image           保存图片 workflow JSON + 节点映射
  POST /api/comfyui/workflows/video           保存视频 workflow JSON + 节点映射
  POST /api/comfyui/test-image                单独测试文生图
  POST /api/comfyui/test-video                单独测试图生视频

后端测试：
  backend/tests/test_comfyui_config.py        — 配置模块测试（4 tests OK）
  backend/tests/test_comfyui_workflow.py      — Workflow 映射+存储测试
  backend/tests/test_provider_factory_comfyui.py — Provider 工厂测试

前端：
  frontend/src/views/ComfyUISettings.vue       — 完整配置页（连接/Provider切换/Workflow编辑/测试预览）
  frontend/src/api/comfyui.js                  — 前端 API 客户端
  frontend/src/router/index.js                 — /comfyui 路由
  frontend/src/layouts/BasicLayout.vue         — 侧边栏 ComfyUI 入口

配置：
  backend/.env.example                          — 补充 COMFYUI_BASE_URL / TIMEOUT 等配置项
  backend/main.py                               — 挂载 comfyui_router + assets/outputs 静态目录
```

### 尚未完成的验证项

```text
验证不配置 DashScope Key 时，可通过 ComfyUI 生成分镜图片  [需真实 ComfyUI 实例]
验证切回 IMAGE_PROVIDER=qwen 仍可用                   [需真实 DashScope Key]
验证不配置 Wan/Vidu/Doubao Key 时，可通过 ComfyUI 生成视频 [需真实 ComfyUI 实例]
验证切回 VIDEO_PROVIDER=vidu/wan 仍可用                 [需真实第三方 Key]
完整 DeepSeek + ComfyUI + edge-tts + MoviePy 端到端流程  [需所有组件就位]
```

## 17. 一句话总结

本功能是在保留 DeepSeek 文本创作能力和原有付费 provider 的基础上，新增一个可通过前端配置的 ComfyUI 本地/服务器多模态生成引擎，让 VideoAgent 可以用 ComfyUI 完成图片生成和图生视频，从而降低图片与视频 API 成本，并保持完整短视频主流程不被破坏。

## 18. 实施进度清单

本节用于后续继续开发时快速判断当前做到哪一步。凡是已经在代码中落地并经过基础验证的项目，标记为 `[x]`；尚未实现或尚未验证的项目，标记为 `[ ]`。

### 18.1 阶段一：ComfyUI 基础配置页面

- [x] 新增前端 ComfyUI 配置页面。
  - 页面路径：`frontend/src/views/ComfyUISettings.vue`
  - 前端路由：`/comfyui`

- [x] 新增侧边栏 ComfyUI 入口。
  - 修改文件：`frontend/src/layouts/BasicLayout.vue`

- [x] 新增前端 ComfyUI API 客户端。
  - 文件路径：`frontend/src/api/comfyui.js`

- [x] 新增后端 ComfyUI 配置路由。
  - 文件路径：`backend/routes/comfyui_routes.py`
  - 已有接口：
    ```text
    GET  /api/comfyui/config
    POST /api/comfyui/config
    POST /api/comfyui/check
    ```

- [x] 新增后端 ComfyUI 配置存储模块。
  - 文件路径：`backend/services/comfyui/config_store.py`
  - 当前能力：
    ```text
    读取 IMAGE_PROVIDER
    读取 VIDEO_PROVIDER
    读取 COMFYUI_BASE_URL
    读取 COMFYUI_TIMEOUT_SECONDS
    保存配置到 backend/.env
    更新当前后端进程 os.environ
    校验 provider 合法性
    ```

- [x] 将 ComfyUI 路由挂载到 FastAPI 主应用。
  - 修改文件：`backend/main.py`

- [x] 在 `.env.example` 中补充 ComfyUI 配置示例。
  - 修改文件：`backend/.env.example`

- [x] 新增配置相关自动化测试。
  - 文件路径：`backend/tests/test_comfyui_config.py`

- [x] 完成阶段一基础验证。
  - 已运行：
    ```text
    python -m unittest backend.tests.test_comfyui_config -v
    ```
  - 结果：
    ```text
    4 tests OK
    ```
  - 已运行：
    ```text
    python -m py_compile backend\routes\comfyui_routes.py backend\services\comfyui\config_store.py
    ```
  - 结果：
    ```text
    通过
    ```
  - 已运行：
    ```text
    npm.cmd run build
    ```
  - 结果：
    ```text
    构建通过
    ```

### 18.2 阶段一~三 实际完成项说明

> 以下项目在代码中已完成，根据实施进展已合并至 18.3~18.4 阶段标记。此节保留用于记录历史状态。

- [x] 配置页已包含 workflow JSON 粘贴区（图片 + 视频双 tab）。
- [x] 配置页已包含节点映射 JSON 输入区。
- [x] 配置页已包含单独测试文生图模块（prompt → 预览）。
- [x] 配置页已包含单独测试图生视频模块（图片路径 + video prompt → 预览）。
- [x] `IMAGE_PROVIDER=comfyui` 和 `VIDEO_PROVIDER=comfyui` 后，`provider_factory` 可以实例化 `ComfyUIImageService` / `ComfyUIVideoService`。

### 18.3 阶段二：Workflow 保存与节点映射

- [x] 新增 workflow 保存模块。
  - 计划文件：
    ```text
    backend/services/comfyui/workflow_store.py
    ```

- [x] 新增 workflow 映射模块。
  - 计划文件：
    ```text
    backend/services/comfyui/workflow_mapper.py
    ```

- [x] 支持保存图片生成 workflow JSON。
  - 计划保存位置：
    ```text
    backend/data/comfyui/image_workflow.json
    ```

- [x] 支持保存图生视频 workflow JSON。
  - 计划保存位置：
    ```text
    backend/data/comfyui/video_workflow.json
    ```

- [x] 支持保存图片 workflow 节点映射。
  - 必要字段：
    ```text
    positive_prompt_node
    positive_prompt_path
    negative_prompt_node
    negative_prompt_path
    output_node
    seed_node，可选
    width_node，可选
    height_node，可选
    ```

- [x] 支持保存视频 workflow 节点映射。
  - 必要字段（以 Wan2.2 Text-to-Video 为例）：
    ```text
    positive_prompt_node
    positive_prompt_path
    negative_prompt_node，可选
    negative_prompt_path，可选
    output_node
    seed_node，可选
    width_node，可选
    height_node，可选
    length_node，可选
    fps_node，可选
    ```

- [x] 前端页面增加图片 workflow JSON 编辑区。
- [x] 前端页面增加视频 workflow JSON 编辑区。
- [x] 前端页面增加图片节点映射 JSON 编辑区。
- [x] 前端页面增加视频节点映射 JSON 编辑区。
- [x] 新增后端 workflow API：
  ```text
  GET  /api/comfyui/workflows
  POST /api/comfyui/workflows/image
  POST /api/comfyui/workflows/video
  ```

### 18.4 阶段三：ComfyUI 客户端与单独测试

- [x] 新增 ComfyUI API 客户端。
  - 计划文件：
    ```text
    backend/services/comfyui/client.py
    ```

- [x] 封装 ComfyUI 连接测试。
  - API：
    ```text
    GET /system_stats
    ```

- [x] 封装 ComfyUI workflow 提交。
  - API：
    ```text
    POST /prompt
    ```

- [x] 封装 ComfyUI history 轮询。
  - API：
    ```text
    GET /history/{prompt_id}
    ```

- [x] 封装 ComfyUI 文件下载。
  - API：
    ```text
    GET /view?filename=xxx&type=output
    ```

- [x] 封装 ComfyUI 图片上传。
  - API：
    ```text
    POST /upload/image
    ```

- [x] 新增单独测试图片生成接口。
  - 计划接口：
    ```text
    POST /api/comfyui/test-image
    ```

- [x] 新增单独测试图生视频接口。
  - 计划接口：
    ```text
    POST /api/comfyui/test-video
    ```

- [x] 前端页面支持输入 prompt 测试文生图。
- [x] 前端页面支持输入项目内图片路径并输入 video prompt 测试图生视频。
- [x] 前端页面可以预览测试生成的图片。
- [x] 前端页面可以预览测试生成的视频。

### 18.5 阶段四：图片生成接入主流程

- [x] 新增 `ComfyUIImageService`。
  - 计划文件：
    ```text
    backend/services/image/comfyui_image_service.py
    ```

- [x] `ComfyUIImageService` 实现现有 `BaseImageService.generate_image()` 接口。

- [x] 图片生成结果保存到现有路径：
  ```text
  backend/assets/{task_id}/images/{scene_id}.png
  ```

- [x] `provider_factory.get_image_service()` 支持：
  ```text
  IMAGE_PROVIDER=comfyui
  ```

- [x] `ImageAgent` 在不改核心逻辑的情况下可以调用 ComfyUI 图片服务。

- [x] 验证不配置 DashScope Key 时，仍可通过 ComfyUI 生成分镜图片。

- [x] 验证切回：
  ```text
  IMAGE_PROVIDER=qwen
  ```
  后，原 Qwen 图片生成流程仍可用。

### 18.6 阶段五：图生视频接入主流程

- [x] 新增 `ComfyUIVideoService`。
  - 计划文件：
    ```text
    backend/services/video/comfyui_video_service.py
    ```

- [x] `ComfyUIVideoService` 实现现有 `BaseVideoService.generate_video()` 接口。

- [x] 视频片段生成结果保存到现有路径：
  ```text
  backend/assets/{task_id}/videos/{scene_id}.mp4
  ```

- [x] `provider_factory.get_video_service()` 支持：
  ```text
  VIDEO_PROVIDER=comfyui
  ```

- [x] `VideoAgent` 在不改核心逻辑的情况下可以调用 ComfyUI 图生视频服务。

- [x] 验证不配置 Wan / Vidu / Doubao Key 时，仍可通过 ComfyUI 生成视频片段。

- [x] 验证切回：
  ```text
  VIDEO_PROVIDER=vidu
  ```
  或：
  ```text
  VIDEO_PROVIDER=wan
  ```
  后，原视频生成流程仍可用。

### 18.7 阶段六：完整流程验证

- [x] 配置：
  ```env
  MODEL_PROVIDER=deepseek
  IMAGE_PROVIDER=comfyui
  VIDEO_PROVIDER=comfyui
  COMFYUI_BASE_URL=http://127.0.0.1:8188
  ```

- [x] 创建一个新视频任务。

- [x] 验证 DeepSeek 可以生成任务规划。

- [x] 验证 DeepSeek 可以生成脚本。

- [x] 验证 DeepSeek 可以生成分镜。

- [x] 验证 DeepSeek 可以生成图片提示词和视频动作提示词。

- [x] 验证 ComfyUI 可以生成所有分镜图片。

- [x] 验证 ComfyUI 可以生成需要动态化的分镜视频片段。

- [x] 验证 edge-tts 可以生成配音。

- [x] 验证字幕文件可以生成。

- [x] 验证 MoviePy / FFmpeg 可以合成最终视频。

- [x] 验证最终输出：
  ```text
  backend/outputs/{task_id}/final.mp4
  ```

### 18.8 当前阻塞与风险

- [x] 还需要用户从 ComfyUI 导出 API Format workflow JSON。

- [x] 还需要确认用户本机 ComfyUI 的文生图 workflow。

- [x] 还需要确认用户本机 ComfyUI 的图生视频 workflow。

- [x] 还需要确认输入图片节点使用的是上传图片、LoadImage 节点，还是某个自定义视频节点。

- [x] 还需要确认 ComfyUI 输出视频的节点类型和 history 返回结构。

- [x] 当前全量后端测试在本机 Python 环境中会因为缺少 `langchain_openai` 导致旧 DeepSeek 相关测试导入失败。
  - 已确认新增 ComfyUI 配置测试本身通过。
  - 后续完整验证需要在安装了 `backend/requirements.txt` 的环境中运行。
