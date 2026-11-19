export const MODE_LABELS = {
  auto_director: "AI 自主导演",
  storyboard_flow: "剧组单元自由流",
  native_10s: "原生 10 秒整切",
};

export function modeText(mode) {
  return MODE_LABELS[mode || "native_10s"] || mode || "原生 10 秒整切";
}

export function durationText(project) {
  if (!project) return "";
  const lo = project.episode_duration_min;
  const hi = project.episode_duration_max;
  if ((project.generation_mode || "native_10s") !== "native_10s" && lo && hi) {
    return `${lo}–${hi} 秒（弹性）`;
  }
  return `${project.episode_duration} 秒`;
}
