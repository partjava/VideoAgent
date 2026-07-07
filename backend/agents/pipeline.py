from typing import Any

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


async def run_pipeline(task_id: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    agent_sequence = [
        ("task_plan", TaskPlannerAgent()),
        ("script", ScriptAgent()),
        ("storyboard", StoryboardAgent()),
        ("dialogue", DialoguePolishAgent()),
        ("prompts", PromptAgent()),
        ("images", ImageAgent()),
        ("video", VideoAgent()),
        ("voice", VoiceAgent()),
        ("subtitle", SubtitleAgent()),
        ("editor", EditorAgent()),
        ("quality", QualityAgent()),
        ("export", ExportAgent()),
    ]

    for result_key, agent in agent_sequence:
        results[result_key] = await agent.run(task_id)

    return {
        "task_id": task_id,
        "status": "success",
        "source": "real",
        "results": results,
    }
