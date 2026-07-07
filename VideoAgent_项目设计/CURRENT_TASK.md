# CURRENT_TASK.md

## 当前阶段

系统已完整可用：全动态视频生成模式。

## 已实现功能

1. 用户输入一句话创建视频任务
2. TaskPlannerAgent 任务规划
3. ScriptAgent 脚本生成
4. StoryboardAgent 分镜设计
5. DialoguePolishAgent 对白精修
6. PromptAgent 视觉提示词生成
7. ImageAgent 图片生成（Qwen-Image）
8. VideoAgent 图生视频（Wan / Vidu / Doubao），失败自动降级为静态图
9. VoiceAgent 分镜级独立配音（edge-tts），不再覆盖分镜时长
10. SubtitleAgent SRT 字幕生成
11. EditorAgent 最终合成，使用分镜级独立配音
12. QualityAgent 质量检查
13. ExportAgent 导出
14. 前端进度展示、视频预览下载
15. MongoDB 持久化存储
16. 全部 8 个 Agent 串行 pipeline

## pipeline 顺序

task_plan -> script -> storyboard -> dialogue -> prompts -> images -> video -> voice -> subtitle -> editor -> quality -> export

## 配音设计

VoiceAgent 为每个分镜生成独立配音文件（voice_{scene_id}.mp3），
EditorAgent 遍历分镜时从 assets 集合加载对应配音并挂载到各 clip 上。
总配音 voice.mp3 保留用于前端预览。
不再根据配音时长覆盖分镜时长。
