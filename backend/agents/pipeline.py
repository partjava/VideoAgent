"""
Pipeline 调度 — 使用 LangGraph StateGraph 编排 13 个智能体。

每个 Agent 封装为图中的一个 Node，状态在节点间传递。
QualityAgent 之后有条件分支：通过则导出，失败则回 EditorAgent 重试。
"""
from typing import Annotated, Any, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Send
from typing_extensions import TypedDict

from agents.editor_agent import EditorAgent
from agents.export_agent import ExportAgent
from agents.image_agent import ImageAgent
from agents.dialogue_polish_agent import DialoguePolishAgent
from agents.prompt_agent import PromptAgent
from agents.quality_agent import QualityAgent
from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.subtitle_agent import SubtitleAgent
from agents.task_planner_agent import TaskPlannerAgent
from agents.video_agent import VideoAgent
from agents.voice_agent import VoiceAgent


def _merge_results_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """合并两个 results 字典，b 覆盖 a 的同名 key"""
    return {**a, **b}


class PipelineState(TypedDict):
    """LangGraph 状态：整个 pipeline 中传递的数据"""
    task_id: str
    status: str
    error: Optional[str]
    results: Annotated[dict[str, Any], _merge_results_dict]


# ── Node 函数：每个 Agent 封装为一个节点 ──────────────────────


def _merge_results(state: PipelineState, key: str, value: Any) -> dict:
    """合并结果到 state.results，不覆盖已有字段"""
    return {"results": {**state.get("results", {}), key: value}}


async def task_planner_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering task_planner_node")
    agent = TaskPlannerAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting task_planner_node")
    return _merge_results(state, "task_plan", result)


async def script_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering script_node")
    agent = ScriptAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting script_node")
    return _merge_results(state, "script", result)


async def storyboard_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering storyboard_node")
    agent = StoryboardAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting storyboard_node")
    return _merge_results(state, "storyboard", result)


async def dialogue_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering dialogue_node")
    agent = DialoguePolishAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting dialogue_node")
    return _merge_results(state, "dialogue", result)


async def prompts_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering prompts_node")
    agent = PromptAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting prompts_node")
    return _merge_results(state, "prompts", result)


async def images_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering images_node for task {state['task_id']}")
    agent = ImageAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting images_node, result type: {type(result).__name__}")
    return _merge_results(state, "images", result)


async def video_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering video_node for task {state['task_id']}")
    agent = VideoAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting video_node")
    return _merge_results(state, "video", result)


async def voice_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering voice_node")
    agent = VoiceAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting voice_node")
    return _merge_results(state, "voice", result)


async def subtitle_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering subtitle_node")
    agent = SubtitleAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting subtitle_node")
    return _merge_results(state, "subtitle", result)


async def editor_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering editor_node")
    agent = EditorAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting editor_node")
    return _merge_results(state, "editor", result)


async def quality_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering quality_node")
    agent = QualityAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting quality_node")
    return _merge_results(state, "quality", result)


async def export_node(state: PipelineState) -> dict:
    print(f"[Pipeline] Entering export_node")
    agent = ExportAgent()
    result = await agent.run(state["task_id"])
    print(f"[Pipeline] Exiting export_node")
    return _merge_results(state, "export", result)


# ── 并行路由 ────────────────────────────────────────────────


def route_to_parallel(state: PipelineState) -> list[Send]:
    """
    prompts 之后分叉为两条并行分支：
      分支 A: images → video  (video 依赖 images)
      分支 B: voice           (配音与画面生成无依赖)
    Send() 是 LangGraph 的并行启动机制，同时向两个节点发送状态。
    """
    return [
        Send("images", {"task_id": state["task_id"], "results": state.get("results", {})}),
        Send("voice", {"task_id": state["task_id"], "results": state.get("results", {})}),
    ]


# ── 条件路由：质检通过 → 导出，失败 → 回编辑重试 ─────────────


def route_after_quality(state: PipelineState) -> str:
    quality_result = state["results"].get("quality", {})
    if quality_result.get("status") == "success":
        return "export"
    print(f"[Pipeline] Quality check failed, retrying editor...")
    return "editor"


# ── 构建 LangGraph ──────────────────────────────────────────


def build_pipeline() -> StateGraph:
    workflow = StateGraph(PipelineState)

    # 注册节点
    workflow.add_node("task_planner", task_planner_node)
    workflow.add_node("script", script_node)
    workflow.add_node("storyboard", storyboard_node)
    workflow.add_node("dialogue", dialogue_node)
    workflow.add_node("prompts", prompts_node)
    workflow.add_node("images", images_node)
    workflow.add_node("video", video_node)
    workflow.add_node("voice", voice_node)
    workflow.add_node("subtitle", subtitle_node)
    workflow.add_node("editor", editor_node)
    workflow.add_node("quality", quality_node)
    workflow.add_node("export", export_node)

    # 第一阶段：串行（任务规划 → 脚本 → 分镜 → 润色 → 提示词）
    workflow.set_entry_point("task_planner")
    workflow.add_edge("task_planner", "script")
    workflow.add_edge("script", "storyboard")
    workflow.add_edge("storyboard", "dialogue")
    workflow.add_edge("dialogue", "prompts")

    # 第二阶段：并行分叉（LangGraph Send）
    #   prompts → images → video （分支 A，顺序执行）
    #   prompts → voice         （分支 B，与 A 并行）
    #   subtitle 等待两个分支都完成（屏障汇聚 / fan-in）
    workflow.add_conditional_edges("prompts", route_to_parallel, ["images", "voice"])
    workflow.add_edge("images", "video")
    workflow.add_edge("video", "subtitle")
    workflow.add_edge("voice", "subtitle")

    # 第三阶段：串行（字幕 → 剪辑 → 质检 → 导出）
    workflow.add_edge("subtitle", "editor")
    workflow.add_edge("editor", "quality")

    # 条件分支：质检通过→导出，失败→回编辑重试
    workflow.add_conditional_edges(
        "quality",
        route_after_quality,
        {"export": "export", "editor": "editor"},
    )

    workflow.add_edge("export", END)

    return workflow


# ── 公开入口 ────────────────────────────────────────────────


async def run_pipeline(task_id: str) -> dict[str, Any]:
    print(f"[Pipeline] Starting pipeline for task {task_id}")
    workflow = build_pipeline()
    app = workflow.compile()

    initial_state: PipelineState = {
        "task_id": task_id,
        "status": "running",
        "error": None,
        "results": {},
    }

    try:
        final_state = await app.ainvoke(initial_state)
        # 所有节点跑完即为成功
        final_state["status"] = "success"
        print(f"[Pipeline] Pipeline completed, status: {final_state.get('status')}")
        return {
            "task_id": task_id,
            "status": final_state.get("status", "success"),
            "source": "real",
            "results": final_state.get("results", {}),
        }
    except Exception as exc:
        import traceback
        print(f"[Pipeline] Pipeline FAILED with exception: {exc}")
        traceback.print_exc()
        return {
            "task_id": task_id,
            "status": "failed",
            "source": "real",
            "error": str(exc),
            "results": {},
        }
