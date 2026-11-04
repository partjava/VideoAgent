# CURRENT_TASK.md

## 当前阶段

系统已完成生产级全流程重构与端到端实测验证，融合 GitHub 开源社区顶尖视频 Skills（MoneyPrinterTurbo、OpenCut、Remotion、OpenMontage、video-shotcraft、seedance2-skill）。

## 核心能力与近期重大升级

1. **三段式动作动力学提示词引擎（seedance2-skill & video-shotcraft）**：
   - 告别单调微动作与生硬眨眼，采用影视级动力学（前1/3时长蓄势起手 $\rightarrow$ 中1/3核心爆发形变与交互 $\rightarrow$ 后1/3动能定格）。
   - 单镜头单一焦点律，彻底杜绝单镜头内角色瞬间漂移。
   - 宏观肢体动力学与环境物理反馈（衣摆飘动、书页翻飞、环境冷光反射）。
2. **多时长镜头支持（5秒 ~ 15秒）**：
   - 全面支持阿里万相 Wan 3.0（2~30s）、HappyHorse 1.1（3~15s）、Kling、Vidu 等长镜头生成。
   - 破除固定 5 秒切片限制，自适应生成 7~15 秒具备影视呼吸感的分镜镜头。
3. **影音后期深度升级（Remotion & MoneyPrinterTurbo 级质感）**：
   - **智能音频闪避（Audio Ducking）**：有人声对白时 BGM 自动压低至 15%，对白结束平滑淡入恢复。
   - **Ken Burns 呼吸推镜**：静态与过渡画面通过时间函数微推变焦，消除僵死感。
   - **音画独立对齐**：edge-tts 自然语速调节（SSML rate），杜绝 MoviePy 倍速变调。
4. **交互式分镜重拍精修工坊（OpenCut 模式）**：
   - 支持单镜头独立重录配音、重绘底图、重新渲染视频切片并局部热重构成片。
5. **模型矩阵适配**：
   - LLM: `deepseek-v4.1-flash-1017` / `deepseek-chat`
   - 图片: `qwen-image-2.1-turbo` / `qwen-image-3.0-pro` / `wan2.7-image`
   - 视频: `wan3.0-video` / `happyhorse-1.1-i2v` / `wan2.6-i2v-flash`
6. **实测与工程验证**：
   - 30 秒实机端到端全动态生成测试完全跑通（`final.mp4`，0 静态降级，24fps 720x1280）。
   - 单元测试 26/26 全部通过。
