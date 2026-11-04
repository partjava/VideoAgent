# VideoAgent — 基于多智能体协作的一键短视频生成系统

> **输入任意主题，AI 自动生成完整短视频。** 系统自动完成脚本、分镜、图片、视频、配音、字幕到最终合成，全程无需人工剪辑。

---

## 项目截图

![首页](backend/image/home1.png)
*首页 — 创建视频任务，输入一句话即可开始生成*

![历史记录与进度](backend/image/home2.png)
*历史任务列表与实时生成进度追踪*

![视频预览与下载](backend/image/home3.png)
*生成完成后自动跳转预览页，可播放视频、下载 MP4 和字幕文件*

![系统架构图](backend/image/架构图.png)
*系统总体架构：Vue 前端 → FastAPI 后端 → 多智能体协作 → MongoDB 存储 + 本地文件系统*

![生成流程图](backend/image/流程图.png)
*12 个智能体在 LangGraph StateGraph 中编排执行（串行 + Send 并行分支 + 条件分支）*

---

## 核心技术特性与 GitHub Top 视频 Skills 融合

本项目深度吸收并工程化融合了开源社区最顶尖的视频生成与剪辑 Skills（如 `MoneyPrinterTurbo`、`OpenCut`、`Remotion`、`OpenMontage`、`video-shotcraft`、`seedance2-skill`）：

| 借鉴开源 Skill | 解决的痛点 | 本项目工程化落地机制 |
| :--- | :--- | :--- |
| **`seedance2-skill`** | AI 生成动作平淡、五官微抽、角色漂移 | **三段式动作动力学（Action Arc）**：前1/3时长蓄力起手 $\rightarrow$ 中1/3时长核心爆发与物体形变 $\rightarrow$ 后1/3时长动能结算定格；单镜头单一焦点律；环境物理受力联动（衣摆翻飞、纸张掀起、冷光反光）。 |
| **`video-shotcraft`** | 运镜指令死板、缺乏影视镜头感 | **专业镜头配方卡（Shotcraft Recipe）**：规范 6 级景别（EWS/FS/MS/MCU/CU/ECU）与标准化摄影机运镜动词（Dolly In、Tracking Pan、Low-angle Tilt、Orbit 等）。 |
| **`MoneyPrinterTurbo`** | 短视频留存差、配音被背景音乐掩盖 | **爆款短视频叙事引擎**：前3秒视觉与身份错位钩子；**音频自动混音与智能闪避（Audio Ducking）**，人声对白时自动压低 BGM 至 15%，静音处恢复丰满度。多角色独立音色与自然语速调节（防变音）。 |
| **`OpenMontage`** | 跨镜头动作断层、画面跳戏 | **尾帧继承与同机位续片（Continue Action Handoff）**：长事件跨镜头时，自动截取前一镜的末尾帧 `tail_frame.png` 自动作为后一镜首帧输入。 |
| **`Remotion` & `HyperFrames`** | 静态图片死板定格 | **Ken Burns 呼吸感推镜**：静态图片通过时间函数连续平滑微推（`1.0 + 0.05 * t / duration`），配合抗锯齿双线性重采样。 |
| **`OpenCut`** | 生成不满意需全部重跑 | **分镜精修工坊**：前端提供单镜头独立工作台，支持单镜**重录配音**、**重绘底图**、**重拍视频**并局部重新合成成片。 |

---

## 系统架构

系统由 12 个智能体在 LangGraph StateGraph 中编排执行：

```
第一阶段（串行）：
TaskPlanner → Script → Storyboard → DialoguePolish → Prompt

第二阶段（并行准备）：
Prompt ──┬── Image ──┐
         └── Voice ──┴── Video → Subtitle

第三阶段（串行）：
Subtitle → Editor → Quality → Export → final.mp4
```

### 智能体说明

> **使用 LangGraph 编排多智能体流程，LangChain 调用大模型。** 12 个 Agent 注册为 LangGraph StateGraph 节点，Send 并行分支 + 屏障汇聚 + 导出前质检；LLM 统一通过 LangChain ChatOpenAI 调用 DeepSeek。

| 智能体 | 职责 | 调用服务 |
|--------|------|---------|
| TaskPlannerAgent | 解析用户需求，生成任务规划 | DeepSeek |
| ScriptAgent | 生成视频脚本和发布文案 | DeepSeek |
| StoryboardAgent | 拆分为连续分镜，每段≥4秒 | DeepSeek |
| DialoguePolishAgent | 精修对白和字幕文本 | DeepSeek |
| PromptAgent | 生成图片/视频提示词、运动节拍 | DeepSeek |
| ImageAgent | 生成底图（4路并发） | **Doubao Seedream** / Qwen / ComfyUI |
| VideoAgent | 图生视频（连续动作继承上一镜尾帧） | **Doubao Seedance** / Wan / Vidu / ComfyUI |
| VoiceAgent | 每个分镜独立配音文件 | edge-tts |
| SubtitleAgent | 按镜头时间轴和配音时长生成 SRT | 本地 |
| EditorAgent | 按镜头时间轴对齐音画，统一比例并烧录字幕 | MoviePy + FFmpeg |
| QualityAgent | 验证素材完整、视频解码、时长、比例、音轨与字幕 | 文件检查 |
| ExportAgent | 保存结果到数据库 | — |

---

## 目录结构

```
video/
├── backend/
│   ├── agents/           # 12 个智能体
│   │   ├── pipeline.py   # LangGraph StateGraph 编排
│   │   ├── task_planner_agent.py
│   │   ├── script_agent.py
│   │   ├── storyboard_agent.py
│   │   ├── dialogue_polish_agent.py
│   │   ├── prompt_agent.py
│   │   ├── image_agent.py
│   │   ├── video_agent.py
│   │   ├── voice_agent.py
│   │   ├── subtitle_agent.py
│   │   ├── editor_agent.py
│   │   ├── quality_agent.py
│   │   └── export_agent.py
│   ├── core/
│   │   └── database.py   # MongoDB 连接
│   ├── models/           # Pydantic 数据模型
│   ├── routes/           # FastAPI 路由（含 API Key 设置接口）
│   ├── services/         # 外部服务封装
│   │   ├── llm/          # DeepSeek（LangChain ChatOpenAI）
│   │   ├── image/        # Doubao Seedream / Qwen-Image / ComfyUI
│   │   ├── video/        # Doubao Seedance / Wan / Vidu / ComfyUI
│   │   ├── comfyui/      # ComfyUI 客户端 + 配置
│   │   ├── provider_factory.py  # 模型路由工厂
│   │   ├── task_service.py
│   │   └── tts_service.py
│   ├── image/            # 项目截图与说明图片
│   ├── config.py         # 环境配置
│   └── main.py           # FastAPI 入口
├── frontend/             # Vue 3 + Element Plus 前端
├── VideoAgent_项目设计/  # 完整设计文档
│   ├── 01_项目总体设计.md
│   ├── 02_智能体与后端设计.md
│   ├── 03_前端页面与UI设计.md
│   ├── 04_ComfyUI本地多模态接入需求方案.md
│   └── 项目整体设计方案.md
├── .env.example          # 环境变量模板
├── .gitignore
└── README.md
```

---

## 快速开始

### 环境要求

- Python 3.13+
- MongoDB（本地运行）
- Node.js 18+（前端开发）
- FFmpeg（视频合成）

### MongoDB 安装

**Windows：**

1. 前往 [MongoDB Community Server 下载页](https://www.mongodb.com/try/download/community) 下载 MSI 安装包
2. 安装时选择 **Complete** 安装，勾选 "Install MongoDB as a Service"
3. 一路下一步完成安装，MongoDB 会自动作为 Windows 服务启动
4. 验证安装：
   ```bash
   # 在 PowerShell 中运行
   mongosh --eval "db.version()"
   # 输出版本号即表示安装成功
   ```

**macOS：**

```bash
brew tap mongodb/brew
brew install mongodb-community
brew services start mongodb-community
```

**Linux（Ubuntu / Debian）：**

```bash
# 导入 MongoDB GPG 密钥
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
# 添加源
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
# 安装
sudo apt update
sudo apt install -y mongodb-org
sudo systemctl start mongod
```

### 验证 MongoDB

安装并启动 MongoDB 后，验证数据库正常运行：

```bash
# 连接测试
mongosh --eval "db.version()"
# 应输出：7.0.x

# 检查服务状态（Windows PowerShell）
Get-Service MongoDB

# 检查服务状态（Linux）
sudo systemctl status mongod
```

项目默认连接 `mongodb://localhost:27017`，数据库名 `video_agent`。
如需修改，编辑 `.env` 文件中的 `MONGODB_URI` 和 `MONGODB_DB_NAME`。

启动后端后，访问 [http://localhost:8000/health](http://localhost:8000/health) 可查看 MongoDB 连接状态：

```json
{
  "status": "ok",
  "database": {
    "configured": true,
    "name": "video_agent"
  }
}

### 快速启动

```bash
# 1. 后端
cd backend
pip install -r requirements.txt
cp .env.example .env
# 在 .env 中填入 API Key，或在启动后通过前端页面配置
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 2. 前端（新开终端）
cd frontend
npm install
npm run dev

# 3. 打开浏览器访问 http://localhost:5173
```

### 生成视频

以 **"李白考上九八五"** 为例：

```bash
# 1. 创建任务
curl -X POST http://localhost:8000/api/video/create \
  -H "Content-Type: application/json" \
  -d '{"user_input": "李白考上九八五", "duration": 30}'

# 返回示例（记录 task_id）：
# {"status":"success","data":{"task_id":"task_xxx...","status":"created","progress":0}}

# 2. 启动 pipeline（把 {task_id} 换成上一步返回的 task_id）
curl -X POST http://localhost:8000/api/video/task_xxx/run-pipeline

# 3. 查看进度
curl http://localhost:8000/api/video/task_xxx/progress

# 4. 生成完成后获取结果
curl http://localhost:8000/api/video/task_xxx
# 返回中 status 为 "success" 表示完成
# video_path 即为最终视频路径
```

**通过前端页面：**

1. 访问首页，在输入框填写 `李白考上九八五`
2. 可选参数：时长 30 秒、风格 短剧、平台 抖音
3. 点击「创建任务」→ 自动跳转到进度页
4. 等待各智能体依次执行（约 5-10 分钟）
5. 完成后自动跳转预览页，可下载视频和字幕

---

## 环境变量与模型配置

| 变量 | 说明 | 必填 | 默认 / 推荐值 |
|------|------|:---:|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（用于导演策划、编剧、分镜与提示词） | 是 | `sk-...` |
| `DEEPSEEK_MODEL` | DeepSeek 模型版本标识 | 否 | `deepseek-chat` / `deepseek-v4.1-flash-1017` |
| `DASHSCOPE_API_KEY` | 阿里百炼 DashScope 密钥（用于 Qwen/万相/HappyHorse） | 使用 Wan/Qwen 时必填 | `sk-...` |
| `WAN_VIDEO_MODEL` | 视频生成模型名称（支持长镜头生成） | 否 | `wan3.0-video` / `happyhorse-1.1-i2v` / `wan2.6-i2v-flash` |
| `QWEN_IMAGE_MODEL` | 首帧图片生成模型 | 否 | `qwen-image-2.1-turbo` / `qwen-image-3.0-pro` / `wan2.7-image` |
| `VOLCENGINE_API_KEY` | 火山引擎方舟 API Key（豆包模型） | 使用 Doubao 时必填 | `sk-...` |
| `MONGODB_URI` | MongoDB 本地或远程连接地址 | 否 | `mongodb://localhost:27017` |
| `ENABLE_PAID_API` | 是否启用付费云端 API（开启后才真实调用视频生成） | 否 | 生产设为 `true` |

### 模型支持矩阵与长镜头配置

本项目全面支持 5 ~ 15 秒长镜头视频生成：

```bash
# .env 常用配置组合示例（百炼全家桶）
MODEL_PROVIDER=deepseek
IMAGE_PROVIDER=qwen
VIDEO_PROVIDER=wan

ENABLE_PAID_API=true
DEEPSEEK_MODEL=deepseek-v4.1-flash-1017

# 视频模型选型（任选一种）：
WAN_VIDEO_MODEL=wan3.0-video        # 阿里万相 3.0，支持 2~30 秒长时序生成
# WAN_VIDEO_MODEL=happyhorse-1.1-i2v # 阿里 HappyHorse 多模态图生视频，支持 3~15 秒
# WAN_VIDEO_MODEL=wan2.6-i2v-flash   # 极速低成本 5 秒快闪生成

# 底图模型选型：
QWEN_IMAGE_MODEL=qwen-image-2.1-turbo # 高速竖屏 9:16 生成
# QWEN_IMAGE_MODEL=qwen-image-3.0-pro   # 千问 3.0 专业版生图
```

火山引擎方舟配置步骤：
1. 在[火山引擎方舟控制台](https://console.volcengine.com/ark)开通模型
2. 为 API Key 勾选对应模型的访问权限
3. 在 `.env` 中填入 `VOLCENGINE_API_KEY`
4. 模型 ID 使用带日期后缀的完整 ID（如 `doubao-seedream-5-0-lite-260128`）

---

## 设计文档

详见 [VideoAgent_项目设计/](./VideoAgent_项目设计/) 目录：

- [01_项目总体设计.md](./VideoAgent_项目设计/01_项目总体设计.md) — 总体架构、技术栈
- [02_智能体与后端设计.md](./VideoAgent_项目设计/02_智能体与后端设计.md) — 各智能体详细设计
- [03_前端页面与UI设计.md](./VideoAgent_项目设计/03_前端页面与UI设计.md) — 前端界面设计
- [项目整体设计方案.md](./VideoAgent_项目设计/项目整体设计方案.md) — 完整设计方案

---

## 许可证

MIT

---

## 使用守则

当前流程会检查分镜总时长、动作节拍覆盖和成片媒体完整性；连续动作镜头使用上一镜头的实际尾帧。角色外观和动作完成度仍应通过成片目测确认。详见 [六项优化与离线验证](docs/plans/2026-11-03-generation-quality.md)。

本系统旨在辅助合法、合规的短视频内容创作。使用本系统时，请遵守以下规则：

### 允许的用途

- 知识科普、教育类视频创作
- 合法的商业内容营销
- 个人创作者内容制作
- 学术研究与教学演示
- 新闻与媒体内容生产

### 禁止的用途

- 生成虚假信息、谣言或恶意误导内容
- 制作诽谤、侮辱、歧视或煽动仇恨的内容
- 生成或传播色情、暴力、赌博等违法内容
- 仿冒他人身份、制作 deepfake 诈骗视频
- 制作政治敏感或违反中国法律法规的内容
- 利用系统进行任何形式的欺诈、钓鱼或社会工程攻击
- 规避或破坏第三方平台内容审核机制

### 责任声明

本系统仅提供技术工具。使用者应自行对生成内容承担全部法律责任。
项目开发者不对使用者利用本系统制作的任何内容负责。
如发现系统被用于违法违规目的，项目方有权停止服务并保留追究权利。
