<template>
  <BasicLayout>
    <div class="page" v-loading="loading">
      <div class="heading va-workspace-header">
        <div>
          <span class="va-eyebrow">SERIES STUDIO · 连载短剧排期</span>
          <h1>连载短剧</h1>
          <p>从故事梗概到逐集成片，统一管理多集大纲、角色版本与跨集状态延续。</p>
        </div>
        <div class="header-cta-row">
          <el-button type="success" plain @click="router.push('/studio-craft')">
            📖 小说/长文一键拆剧 →
          </el-button>
          <el-button type="primary" @click="openCreateDialog()">新建连载</el-button>
        </div>
      </div>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      />
      <el-alert
        v-if="syncNotice"
        :title="syncNotice"
        type="success"
        show-icon
        closable
        style="margin-bottom: 14px"
        @close="syncNotice = ''"
      />

      <el-empty
        v-if="!loading && !projects.length && !unsyncedCraftDrafts.length && !standaloneTasks.length"
        description="写下一个故事，开始你的第一部连载"
      />

      <!-- 已创建的连载项目列表 -->
      <div v-if="projects.length" class="cards">
        <el-card v-for="project in projects" :key="id(project)" shadow="hover" class="series-card">
          <div class="card-head">
            <h2>{{ project.title }}</h2>
            <el-tag size="small" :type="project.status === 'completed' ? 'success' : 'primary'" effect="light">
              {{ statusText(project.status) }}
            </el-tag>
          </div>
          <p class="series-brief">{{ project.brief }}</p>
          <div class="series-specs">
            <span>{{ project.total_episodes }} 集</span>
            <span>每集 {{ durationText(project) }}</span>
            <span>{{ modeText(project.generation_mode) }}</span>
            <span>{{ project.style }}</span>
            <span>{{ project.ratio }}</span>
            <span v-if="completedEpisodesCount(project) > 0" class="spec-highlight">
              已成片 {{ completedEpisodesCount(project) }} / {{ project.total_episodes }} 集
            </span>
            <span v-else-if="linkedEpisodesCount(project) > 0" class="spec-linked">
              已关联 {{ linkedEpisodesCount(project) }} 集任务
            </span>
          </div>
          <div class="card-footer-actions">
            <el-button
              type="primary"
              @click="router.push(`/series/${id(project)}`)"
            >进入工作台 →</el-button>
            <el-button type="danger" link @click="archive(project)">归档项目</el-button>
          </div>
        </el-card>
      </div>

      <!-- 待转入连载的工坊拆剧方案 -->
      <div v-if="unsyncedCraftDrafts.length" class="bridge-section">
        <div class="bridge-head">
          <div>
            <span class="va-eyebrow">CRAFT DRAFTS · 导演工坊拆剧方案</span>
            <h2>已拆解的多集剧本（可一键建连载并自动关联已拍单集）</h2>
          </div>
        </div>
        <div class="cards">
          <el-card
            v-for="draft in unsyncedCraftDrafts"
            :key="draft.id"
            shadow="hover"
            class="series-card draft-bridge-card"
          >
            <div class="card-head">
              <h2>《{{ draft.title }}》</h2>
              <el-tag size="small" type="warning" effect="light">
                工坊全案 · {{ draft.episodes_count }} 集
              </el-tag>
            </div>
            <p class="series-brief">
              {{ draft.form?.novel_text || draft.result?.first_episode_script || '含完整分集大纲、第 1 集剧本与四本连续性台账' }}
            </p>
            <div class="series-specs">
              <span>{{ draft.episodes_count }} 集大纲就绪</span>
              <span v-if="matchTaskForTitle(draft.title)" class="spec-highlight">
                检测到已拍第 1 集（{{ matchTaskForTitle(draft.title).status === 'success' ? '已成片' : '制作中' }}）
              </span>
            </div>
            <div class="card-footer-actions">
              <el-button
                type="primary"
                :loading="syncingDraftKey === draft.title"
                @click="quickCreateFromDraft(draft)"
              >
                ⚡ 一键转入连载工作台 →
              </el-button>
              <el-button link @click="openCreateDialogWithDraft(draft)">
                自定义参数建连载
              </el-button>
            </div>
          </el-card>
        </div>
      </div>

      <!-- 已生成的独立单集任务：可一键转为连载第 1 集或并入现有连载 -->
      <div v-if="standaloneTasks.length" class="bridge-section">
        <div class="bridge-head">
          <div>
            <span class="va-eyebrow">STANDALONE EPISODES · 已生成的独立单集</span>
            <h2>独立生成的剧集与视频（可一键作为第 1 集开启连载，或并入已有连载）</h2>
          </div>
        </div>
        <div class="cards">
          <el-card
            v-for="task in standaloneTasks.slice(0, 12)"
            :key="task.task_id"
            shadow="hover"
            class="series-card standalone-task-card"
          >
            <div class="card-head">
              <h2>{{ displayTaskTitle(task) }}</h2>
              <el-tag
                size="small"
                :type="task.status === 'success' ? 'success' : task.status === 'failed' ? 'danger' : 'warning'"
                effect="light"
              >
                {{ task.status === 'success' ? '已成片' : taskStatusLabel(task.status) }}
              </el-tag>
            </div>
            <p class="series-brief">{{ task.user_input || task.topic || '独立单集生成任务' }}</p>
            <div class="series-specs">
              <span>{{ task.style || '短剧' }}</span>
              <span>{{ task.ratio || '9:16' }}</span>
              <span>{{ task.duration ? `${task.duration} 秒` : `${task.duration_min || 45}~${task.duration_max || 90} 秒` }}</span>
              <span>进度 {{ task.progress ?? 0 }}%</span>
            </div>
            <div class="card-footer-actions">
              <div class="footer-btn-group">
                <el-button
                  type="primary"
                  size="small"
                  @click="openCreateFromTask(task)"
                >
                  📺 作为第 1 集建连载
                </el-button>
                <el-button
                  v-if="projects.length"
                  size="small"
                  plain
                  @click="openBindToSeriesDialog(task)"
                >
                  🔗 并入现有连载
                </el-button>
              </div>
              <el-button
                type="primary"
                link
                @click="router.push(task.status === 'success' ? `/preview/${task.task_id}` : `/progress/${task.task_id}`)"
              >
                {{ task.status === 'success' ? '观看成片 →' : '查看进度 →' }}
              </el-button>
            </div>
          </el-card>
        </div>
      </div>

      <!-- 并入现有连载弹窗 -->
      <el-dialog v-model="bindDialog" title="将已生成单集并入连载项目" width="min(520px, 94vw)">
        <p class="hint" style="margin-top: 0">
          当前单集：<strong>{{ bindingTask ? displayTaskTitle(bindingTask) : '' }}</strong>
        </p>
        <el-form label-position="top">
          <el-form-item label="选择目标连载项目">
            <el-select v-model="bindForm.series_id" placeholder="请选择连载项目">
              <el-option
                v-for="p in projects"
                :key="id(p)"
                :label="`${p.title}（共 ${p.total_episodes} 集）`"
                :value="id(p)"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="绑定为第几集">
            <el-select v-model="bindForm.episode_num">
              <el-option
                v-for="n in selectedBindSeriesTotalEpisodes"
                :key="n"
                :label="`第 ${n} 集`"
                :value="n"
              />
            </el-select>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="bindDialog = false">取消</el-button>
          <el-button
            type="primary"
            :loading="bindingBusy"
            :disabled="!bindForm.series_id"
            @click="confirmBindTaskToSeries"
          >
            确认并入该集
          </el-button>
        </template>
      </el-dialog>

      <!-- 新建连载弹窗 -->
      <el-dialog v-model="dialog" title="新建连载" width="min(680px, 94vw)">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 14px"
        >
          <template #title>
            「连载短剧」用于从梗概直接建季并逐集拍片；「短剧导演工坊」用于上游小说拆剧（产出人物卡、九宫格场景、道具四视图、分集大纲与第 1 集剧本）。两者互通：可在工坊拆完一键建连载，也可直接在下方导入工坊草稿或已生成单集。
          </template>
        </el-alert>
        <div v-if="craftDrafts.length" class="craft-import-bar">
          <span class="craft-import-label">📦 从「短剧导演工坊」草稿一键导入：</span>
          <div class="craft-draft-chips">
            <button
              v-for="draft in craftDrafts"
              :key="draft.id"
              type="button"
              class="preset-chip"
              :class="{ active: importedCraftTitle === draft.title }"
              @click="importCraftDraft(draft)"
            >
              导入《{{ draft.title || '未命名拆剧' }}》· {{ draft.episodes_count || 5 }}集
            </button>
          </div>
          <p v-if="importedCraftTitle" class="craft-imported-note">
            ✅ 已载入工坊全案《{{ importedCraftTitle }}》（含分集大纲、第1集剧本与连续性台账）
            <el-button link type="danger" size="small" @click="clearImportedDraft">清除附带大纲</el-button>
          </p>
        </div>
        <el-alert
          v-if="firstEpisodeTaskId"
          type="success"
          :closable="false"
          show-icon
          style="margin-bottom: 14px"
        >
          <template #title>
            已关联已生成单集（{{ firstEpisodeTaskTitle || firstEpisodeTaskId }}）作为第 1 集，创建后无需重拍第 1 集，可直接衔接制作第 2 集。
            <el-button link type="danger" size="small" @click="clearFirstEpisodeTask">取消关联</el-button>
          </template>
        </el-alert>
        <el-form label-position="top">
          <el-form-item label="项目名">
            <el-input v-model="form.title" maxlength="120" />
          </el-form-item>
          <el-form-item label="故事梗概">
            <el-input
              v-model="form.brief"
              type="textarea"
              :rows="4"
              placeholder="主角是谁？想要什么？会遇到什么阻碍？"
            />
          </el-form-item>
          <div class="row">
            <el-form-item label="总集数">
              <el-select v-model="form.total_episodes">
                <el-option
                  v-for="n in [3, 5, 10]"
                  :key="n"
                  :value="n"
                  :label="`${n} 集`"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="生成模式">
              <el-select v-model="form.generation_mode">
                <el-option value="auto_director" label="AI 自主导演（推荐 · 弹性时长）" />
                <el-option value="storyboard_flow" label="剧组单元自由流（多镜交替 · 弹性时长）" />
                <el-option value="native_10s" label="原生 10 秒整切（时长须为 10 的倍数）" />
              </el-select>
            </el-form-item>
          </div>
          <div class="row" v-if="form.generation_mode !== 'native_10s'">
            <el-form-item label="单集时长范围（秒）">
              <div class="range-row">
                <el-input-number v-model="form.episode_duration_min" :min="10" :max="180" :step="5" controls-position="right" />
                <span>~</span>
                <el-input-number v-model="form.episode_duration_max" :min="10" :max="180" :step="5" controls-position="right" />
              </div>
              <div class="preset-row">
                <button
                  v-for="p in rangePresets"
                  :key="p.label"
                  type="button"
                  class="preset-chip"
                  @click="form.episode_duration_min = p.min; form.episode_duration_max = p.max"
                >{{ p.label }}</button>
              </div>
            </el-form-item>
          </div>
          <div class="row" v-else>
            <el-form-item label="单集时长（10 秒整数倍）">
              <el-select v-model="form.episode_duration">
                <el-option
                  v-for="n in [10, 20, 30, 40, 60, 90, 120]"
                  :key="n"
                  :value="n"
                  :label="`${n} 秒`"
                />
              </el-select>
            </el-form-item>
          </div>
          <div class="row">
            <el-form-item label="风格">
              <el-radio-group v-model="form.style">
                <el-radio-button label="短剧" />
                <el-radio-button label="漫剧" />
              </el-radio-group>
            </el-form-item>
            <el-form-item label="画幅">
              <el-radio-group v-model="form.ratio">
                <el-radio-button label="9:16" />
                <el-radio-button label="16:9" />
              </el-radio-group>
            </el-form-item>
            <el-form-item label="剧本市场模式">
              <el-radio-group v-model="form.market_mode">
                <el-radio-button value="cn">国内中文剧</el-radio-button>
                <el-radio-button value="en">出海英文剧</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </div>
          <el-form-item label="固定角色（仅可选择已确认版本）">
            <el-select
              v-model="form.character_ids"
              multiple
              placeholder="可选；也可先到角色资产创建"
              style="width: 100%"
            >
              <el-option
                v-for="character in confirmedCharacters"
                :key="character.character_id || character.id"
                :label="`${character.name} · v${character.revision || 1}`"
                :value="character.character_id || character.id"
              />
            </el-select>
          </el-form-item>
          <div class="row">
            <el-form-item label="整季生成操作额度上限（可选）">
              <el-input
                v-model="form.generation_limit"
                type="number"
                min="1"
                max="10000"
                placeholder="留空不设上限"
              />
            </el-form-item>
          </div>
          <p class="hint">
            预计整季消耗：约 {{ estimatedSeriesOps.images }} 张图 + {{ estimatedSeriesOps.videos }} 段视频（共 {{ estimatedSeriesOps.total }} 次生成操作）。项目会保留所选角色的版本。
          </p>
          <div class="check-group">
            <el-checkbox v-model="form.pre_shoot_review">每集开拍前预审首帧与配音（支持单镜试拍）</el-checkbox>
            <el-checkbox v-model="form.auto_continue">画面证据满足条件时自动继续下一集</el-checkbox>
          </div>
        </el-form>
        <template #footer>
          <el-button @click="dialog = false">取消</el-button>
          <el-button
            type="primary"
            :loading="creating"
            :disabled="!form.title.trim() || !form.brief.trim()"
            @click="create"
          >创建项目</el-button>
        </template>
      </el-dialog>
    </div>
  </BasicLayout>
</template>
<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import BasicLayout from "../layouts/BasicLayout.vue";
import { seriesApi } from "../api/series";
import { videoApi } from "../api/video";
import { charactersApi } from "../api/characters";
import { statusText } from "../utils/seriesStatus";
import { durationText, modeText } from "../utils/seriesDuration";

const DISMISSED_CRAFT_SERIES_KEY = "videoagent_dismissed_craft_series_v1";

const router = useRouter(),
  projects = ref([]),
  allTasks = ref([]),
  characters = ref([]),
  loading = ref(false),
  creating = ref(false),
  dialog = ref(false),
  error = ref(""),
  syncNotice = ref(""),
  syncingDraftKey = ref("");

const form = reactive({
  title: "",
  brief: "",
  total_episodes: 3,
  episode_duration: 30,
  episode_duration_min: 45,
  episode_duration_max: 90,
  generation_mode: "auto_director",
  style: "短剧",
  ratio: "9:16",
  market_mode: "cn",
  character_ids: [],
  auto_continue: false,
  pre_shoot_review: false,
  generation_limit: "",
});
const rangePresets = [
  { label: "30–60 秒", min: 30, max: 60 },
  { label: "45–90 秒", min: 45, max: 90 },
  { label: "60–120 秒", min: 60, max: 120 },
];
const craftDrafts = ref([]);
const importedCraftTitle = ref("");
const importedCraftExtras = ref(null);
const firstEpisodeTaskId = ref(null);
const firstEpisodeTaskTitle = ref("");

// 并入已有连载弹窗状态
const bindDialog = ref(false);
const bindingBusy = ref(false);
const bindingTask = ref(null);
const bindForm = reactive({
  series_id: "",
  episode_num: 1,
});

const selectedBindSeriesTotalEpisodes = computed(() => {
  const found = projects.value.find((p) => id(p) === bindForm.series_id);
  return Number(found?.total_episodes || 3);
});

function loadDismissedCraftTitles() {
  try {
    const raw = localStorage.getItem(DISMISSED_CRAFT_SERIES_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function addDismissedCraftTitle(title) {
  if (!title) return;
  const set = loadDismissedCraftTitles();
  set.add(String(title).trim());
  try {
    localStorage.setItem(DISMISSED_CRAFT_SERIES_KEY, JSON.stringify([...set]));
  } catch {
    // ignore storage quota errors
  }
}

function loadCraftDrafts() {
  try {
    const raw = localStorage.getItem("videoagent_craft_workspace_v1");
    if (!raw) {
      craftDrafts.value = [];
      return;
    }
    const parsed = JSON.parse(raw);
    const list = [];
    const seen = new Set();
    const pushDraft = (idKey, resObj, formObj) => {
      if (!resObj || typeof resObj !== "object") return;
      const title = String(resObj.title || formObj?.title || "未命名拆剧").trim();
      const key = `${title}_${resObj.total_episodes || formObj?.total_episodes || 5}`;
      if (seen.has(key)) return;
      seen.add(key);
      list.push({
        id: idKey,
        title,
        episodes_count: Number(resObj.total_episodes || formObj?.total_episodes || 5),
        form: formObj || {},
        result: resObj,
      });
    };
    if (parsed.novelResult) pushDraft("active", parsed.novelResult, parsed.novelForm);
    for (const d of parsed.recentDecompositions || []) {
      if (d && d.result) pushDraft(d.id || d.title, d.result, d.form);
    }
    craftDrafts.value = list.slice(0, 6);
  } catch {
    craftDrafts.value = [];
  }
}

const standaloneTasks = computed(() => {
  const boundTaskIds = new Set();
  for (const p of projects.value) {
    for (const ep of p.episodes || []) {
      if (ep.task_id) boundTaskIds.add(ep.task_id);
    }
  }
  return allTasks.value.filter(
    (t) => t && t.task_id && !t.series_id && !boundTaskIds.has(t.task_id)
  );
});

function normalizeTitleKey(str) {
  return String(str || "")
    .replace(/[《》【】「」\s]/g, "")
    .trim();
}

function matchTaskForTitle(title) {
  const clean = normalizeTitleKey(title);
  if (!clean) return null;
  const candidates = standaloneTasks.value.filter((t) => {
    const hay = normalizeTitleKey(`${t.topic || ""} ${t.user_input || ""}`);
    return hay.includes(clean);
  });
  if (!candidates.length) return null;
  return candidates.find((t) => t.status === "success") || candidates[0];
}

const unsyncedCraftDrafts = computed(() => {
  const existingTitles = new Set(
    projects.value.map((p) => normalizeTitleKey(p.title))
  );
  return craftDrafts.value.filter(
    (d) => d.title && !existingTitles.has(normalizeTitleKey(d.title))
  );
});

function completedEpisodesCount(project) {
  return (project.episodes || []).filter((e) =>
    ["success", "published", "completed"].includes(e.status)
  ).length;
}

function linkedEpisodesCount(project) {
  return (project.episodes || []).filter((e) => Boolean(e.task_id)).length;
}

function displayTaskTitle(task) {
  const raw = String(task?.topic || task?.user_input || task?.task_id || "").trim();
  const firstLine = raw.split(/\r?\n/)[0].trim();
  return firstLine.length > 42 ? `${firstLine.slice(0, 42)}…` : firstLine;
}

function taskStatusLabel(status) {
  const map = {
    pending: "排队中",
    planning: "规划中",
    script_done: "剧本就绪",
    storyboard_done: "分镜就绪",
    awaiting_shoot_approval: "待确认开拍",
    image_generating: "生图中",
    image_done: "首帧就绪",
    voice_done: "配音就绪",
    video_generating: "视频生成中",
    video_done: "镜头就绪",
    compositing: "剪辑合成中",
    quality_checking: "质检中",
    success: "已成片",
    failed: "失败",
    cancelled: "已取消",
  };
  return map[status] || status || "制作中";
}

function openCreateDialog() {
  importedCraftTitle.value = "";
  importedCraftExtras.value = null;
  firstEpisodeTaskId.value = null;
  firstEpisodeTaskTitle.value = "";
  loadCraftDrafts();
  dialog.value = true;
}

function openCreateDialogWithDraft(draft) {
  openCreateDialog();
  importCraftDraft(draft);
}

function importCraftDraft(draft) {
  if (!draft?.result) return;
  const r = draft.result;
  const f = draft.form || {};
  form.title = String(r.title || f.title || "工坊导入连载").trim();
  form.brief = String(f.novel_text || r.first_episode_script || "").trim();
  const eps = Number(r.total_episodes || f.total_episodes || 5);
  form.total_episodes = eps >= 8 ? 10 : eps <= 4 ? 3 : 5;
  form.episode_duration_min = Number(r.episode_duration_min || f.episode_duration_min || 45);
  form.episode_duration_max = Number(r.episode_duration_max || f.episode_duration_max || 90);
  form.style = r.style || f.style || "短剧";
  form.market_mode = r.market_mode || f.market_mode || "cn";
  importedCraftTitle.value = draft.title;
  importedCraftExtras.value = {
    initial_episodes: r.episodes_outline || null,
    continuity_ledgers: r.continuity_ledgers || null,
    first_episode_script: r.first_episode_script || null,
  };
  const matched = matchTaskForTitle(draft.title);
  if (matched) {
    firstEpisodeTaskId.value = matched.task_id;
    firstEpisodeTaskTitle.value = displayTaskTitle(matched);
  }
}

function clearImportedDraft() {
  importedCraftTitle.value = "";
  importedCraftExtras.value = null;
}

function clearFirstEpisodeTask() {
  firstEpisodeTaskId.value = null;
  firstEpisodeTaskTitle.value = "";
}

function extractTitleFromTask(task) {
  const raw = String(task?.topic || task?.user_input || "").trim();
  const bracketMatch = raw.match(/《([^》]{2,36})》/);
  if (bracketMatch) return bracketMatch[1].trim();
  const tagMatch = raw.match(/【剧名】([^\n【]{2,36})/);
  if (tagMatch) return tagMatch[1].trim();
  const firstLine = raw.split(/\r?\n/)[0].replace(/^【[^】]+】/, "").trim();
  return firstLine.slice(0, 28) || "单集转连载短剧";
}

function openCreateFromTask(task) {
  openCreateDialog();
  const extractedTitle = extractTitleFromTask(task);
  const matchedDraft = craftDrafts.value.find(
    (d) =>
      normalizeTitleKey(d.title) &&
      (normalizeTitleKey(extractedTitle).includes(normalizeTitleKey(d.title)) ||
        normalizeTitleKey(d.title).includes(normalizeTitleKey(extractedTitle)))
  );
  if (matchedDraft) {
    importCraftDraft(matchedDraft);
  } else {
    form.title = extractedTitle;
    form.brief = String(task.user_input || task.topic || extractedTitle).trim();
    form.style = task.style || "短剧";
    form.ratio = task.ratio || "9:16";
    if (task.duration_min && task.duration_max) {
      form.episode_duration_min = Number(task.duration_min);
      form.episode_duration_max = Number(task.duration_max);
    }
  }
  firstEpisodeTaskId.value = task.task_id;
  firstEpisodeTaskTitle.value = displayTaskTitle(task);
}

function openBindToSeriesDialog(task) {
  bindingTask.value = task;
  bindForm.series_id = projects.value[0] ? id(projects.value[0]) : "";
  bindForm.episode_num = 1;
  bindDialog.value = true;
}

async function confirmBindTaskToSeries() {
  if (!bindingTask.value || !bindForm.series_id) return;
  bindingBusy.value = true;
  error.value = "";
  try {
    await seriesApi.bindEpisodeTask(
      bindForm.series_id,
      bindForm.episode_num,
      bindingTask.value.task_id
    );
    bindDialog.value = false;
    ElMessage.success(`已将单集并入第 ${bindForm.episode_num} 集`);
    await router.push(`/series/${bindForm.series_id}`);
  } catch (e) {
    error.value = e.message || "并入连载失败";
  } finally {
    bindingBusy.value = false;
  }
}

async function buildSeriesFromDraftObject(draft, { navigate = false } = {}) {
  const r = draft.result || {};
  const f = draft.form || {};
  const eps = Number(r.total_episodes || f.total_episodes || 3);
  const totalEpisodes = eps >= 8 ? 10 : eps <= 4 ? 3 : 5;
  const lo = Number(r.episode_duration_min || f.episode_duration_min || 45);
  const hi = Number(r.episode_duration_max || f.episode_duration_max || 90);
  const matchedTask = matchTaskForTitle(draft.title);
  const created = await seriesApi.create({
    title: String(r.title || f.title || draft.title || "工坊连载项目").trim(),
    brief: String(f.novel_text || r.first_episode_script || draft.title || "工坊拆剧连载").trim(),
    total_episodes: totalEpisodes,
    episode_duration: Math.round((lo + hi) / 2),
    episode_duration_min: Math.min(lo, hi),
    episode_duration_max: Math.max(lo, hi),
    generation_mode: "auto_director",
    style: r.style || f.style || "短剧",
    ratio: "9:16",
    market_mode: r.market_mode || f.market_mode || "cn",
    character_ids: [],
    auto_continue: false,
    pre_shoot_review: true,
    initial_episodes: r.episodes_outline || null,
    continuity_ledgers: r.continuity_ledgers || null,
    first_episode_script: r.first_episode_script || null,
    first_episode_task_id: matchedTask?.task_id || null,
  });
  const ep1 = (created?.episodes || []).find((e) => e.episode_num === 1);
  if (matchedTask?.task_id && (!ep1 || !ep1.task_id)) {
    try {
      await seriesApi.bindEpisodeTask(id(created), 1, matchedTask.task_id);
    } catch {
      // ignore if backend bind route not yet reloaded
    }
  }
  if (navigate) {
    await router.push(`/series/${id(created)}`);
  }
  return { created, matchedTask };
}

async function quickCreateFromDraft(draft) {
  if (!draft?.result) return;
  syncingDraftKey.value = draft.title;
  error.value = "";
  try {
    await buildSeriesFromDraftObject(draft, { navigate: true });
  } catch (e) {
    error.value = e.message || "转入连载工作台失败";
  } finally {
    syncingDraftKey.value = "";
  }
}

async function autoSyncCraftDraftsOnLoad() {
  const dismissed = loadDismissedCraftTitles();
  const candidates = unsyncedCraftDrafts.value.filter(
    (d) => d.title && !dismissed.has(d.title) && Array.isArray(d.result?.episodes_outline) && d.result.episodes_outline.length > 0
  );
  if (!candidates.length) return;
  const syncedNames = [];
  for (const draft of candidates) {
    try {
      const { matchedTask } = await buildSeriesFromDraftObject(draft, { navigate: false });
      syncedNames.push(
        matchedTask
          ? `《${draft.title}》（已自动并入第 1 集视频）`
          : `《${draft.title}》`
      );
    } catch {
      // skip individual sync error silently
    }
  }
  if (syncedNames.length) {
    syncNotice.value = `已自动将你在「短剧导演工坊」拆解的 ${syncedNames.join("、")} 同步到连载短剧工作台。`;
    const [seriesData, tasksData] = await Promise.all([
      seriesApi.list().catch(() => []),
      videoApi.listTasks().catch(() => []),
    ]);
    projects.value = Array.isArray(seriesData) ? seriesData : seriesData.series || [];
    allTasks.value = Array.isArray(tasksData) ? tasksData : tasksData.tasks || [];
  }
}

const flexibleMode = computed(() => form.generation_mode !== "native_10s");
const estimatedSeriesOps = computed(() => {
  const seconds = flexibleMode.value
    ? Math.round(((form.episode_duration_min || 30) + (form.episode_duration_max || 60)) / 2)
    : form.episode_duration || 30;
  const perClip = form.generation_mode === "storyboard_flow" ? 5 : form.generation_mode === "auto_director" ? 9 : 10;
  const clipsPerEp = Math.max(1, Math.ceil(seconds / perClip));
  const episodes = form.total_episodes || 1;
  const images = clipsPerEp * episodes;
  const videos = clipsPerEp * episodes;
  return { images, videos, total: images + videos };
});
const confirmedCharacters = computed(() =>
  characters.value.filter((c) => c.confirmed),
);
const id = (p) => p.series_id || p.id;

async function load() {
  loading.value = true;
  error.value = "";
  try {
    loadCraftDrafts();
    const [seriesData, tasksData] = await Promise.all([
      seriesApi.list(),
      videoApi.listTasks().catch(() => []),
    ]);
    projects.value = Array.isArray(seriesData) ? seriesData : seriesData.series || [];
    allTasks.value = Array.isArray(tasksData) ? tasksData : tasksData.tasks || [];
    await autoSyncCraftDraftsOnLoad();
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

async function create() {
  creating.value = true;
  error.value = "";
  try {
    const limitNum = String(form.generation_limit ?? "").trim()
      ? Number.parseInt(form.generation_limit, 10)
      : null;
    const lo = Math.min(form.episode_duration_min || 30, form.episode_duration_max || 60);
    const hi = Math.max(form.episode_duration_min || 30, form.episode_duration_max || 60);
    const project = await seriesApi.create({
      ...form,
      episode_duration: flexibleMode.value ? Math.round((lo + hi) / 2) : form.episode_duration,
      episode_duration_min: flexibleMode.value ? lo : null,
      episode_duration_max: flexibleMode.value ? hi : null,
      generation_limit: Number.isInteger(limitNum) && limitNum > 0 ? limitNum : null,
      first_episode_task_id: firstEpisodeTaskId.value || null,
      ...(importedCraftExtras.value || {}),
    });
    const ep1 = (project?.episodes || []).find((e) => e.episode_num === 1);
    if (firstEpisodeTaskId.value && (!ep1 || !ep1.task_id)) {
      try {
        await seriesApi.bindEpisodeTask(id(project), 1, firstEpisodeTaskId.value);
      } catch {
        // ignore if already bound
      }
    }
    dialog.value = false;
    await router.push(`/series/${id(project)}`);
  } catch (e) {
    error.value = e.message;
  } finally {
    creating.value = false;
  }
}

async function archive(project) {
  try {
    await ElMessageBox.confirm(
      `归档“${project.title}”？项目将从列表中隐藏。`,
      "归档项目",
      { type: "warning" },
    );
    addDismissedCraftTitle(project.title);
    await seriesApi.archive(id(project));
    await load();
  } catch (e) {
    if (e instanceof Error) error.value = e.message;
  }
}

onMounted(async () => {
  await load();
  try {
    const data = await charactersApi.list();
    characters.value = Array.isArray(data) ? data : data.characters || [];
  } catch (e) {
    error.value = `角色列表加载失败：${e.message}`;
  }
});
</script>
<style scoped>
.page {
  max-width: 1220px;
  margin: auto;
}
.header-cta-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.row {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr));
  gap: 20px;
  margin-top: 20px;
}
.bridge-section {
  margin-top: 32px;
  padding-top: 22px;
  border-top: 1px solid var(--va-border);
}
.bridge-head h2 {
  margin: 4px 0 0;
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--va-ink-primary);
}
.series-card {
  display: flex;
  flex-direction: column;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
}
.series-card:hover {
  transform: translateY(-2px);
  border-color: var(--va-brand-border) !important;
  box-shadow: var(--va-shadow-md);
}
.draft-bridge-card {
  border-color: #fcd34d;
  background: linear-gradient(180deg, #fffbeb 0%, #ffffff 42%);
}
.standalone-task-card {
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 42%);
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
}
.card-head h2 {
  font-size: 1.08rem;
  font-weight: 800;
  margin: 0;
  line-height: 1.35;
}
.series-brief {
  margin: 0 0 12px;
  color: var(--va-ink-secondary);
  font-size: 0.875rem;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-block-size: 4.1em;
}
.series-specs {
  display: flex;
  flex-wrap: wrap;
  flex-wrap: balance;
  gap: 6px;
  margin-bottom: 16px;
}
.series-specs span {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--va-surface-subtle);
  border: 1px solid var(--va-border);
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--va-ink-secondary);
}
.series-specs span.spec-highlight {
  background: #dcfce7;
  border-color: #86efac;
  color: #166534;
}
.series-specs span.spec-linked {
  background: #dbeafe;
  border-color: #93c5fd;
  color: #1e40af;
}
.card-footer-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 12px;
  border-top: 1px solid var(--va-surface-muted);
}
.footer-btn-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.row > * {
  flex: 1;
  min-width: 180px;
}
.hint {
  font-size: 0.8125rem;
  color: var(--va-ink-muted);
}
.check-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.el-select {
  width: 100%;
}
.range-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preset-row {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
  flex-wrap: balance;
}
.preset-chip {
  padding: 4px 11px;
  border-radius: 999px;
  border: 1px solid var(--va-border-strong);
  background: var(--va-surface-subtle);
  color: var(--va-ink-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
}
.preset-chip:hover {
  border-color: var(--va-brand);
  color: var(--va-brand);
  background: var(--va-brand-soft);
}
.preset-chip.active {
  background: var(--va-brand);
  border-color: var(--va-brand);
  color: #ffffff;
}
.craft-import-bar {
  margin-bottom: 14px;
  padding: 10px 12px;
  background: var(--va-surface-subtle);
  border: 1px dashed var(--va-border-strong);
  border-radius: var(--va-radius-md);
}
.craft-import-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--va-ink-secondary);
  margin-bottom: 6px;
}
.craft-draft-chips {
  display: flex;
  flex-wrap: wrap;
  flex-wrap: balance;
  gap: 6px;
}
.craft-imported-note {
  margin: 8px 0 0;
  font-size: 0.75rem;
  color: #15803d;
}
</style>
