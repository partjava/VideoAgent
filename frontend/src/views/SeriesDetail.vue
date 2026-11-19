<template>
  <BasicLayout>
    <div class="page" v-loading="loading">
      <el-button link @click="router.push('/series')">← 返回连载列表</el-button>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="notice"
      />
      <template v-if="project">
        <div class="heading va-workspace-header">
          <div>
            <span class="va-eyebrow">SERIES WORKBENCH · 连载工作台</span>
            <h1>{{ project.title }}</h1>
            <div class="spec-pills">
              <span>{{ project.total_episodes }} 集</span>
              <span>每集 {{ durationText(project) }}</span>
              <span>{{ modeText(project.generation_mode) }}</span>
              <span>{{ project.style }}</span>
              <span>{{ project.ratio }}</span>
              <span v-if="project.pre_shoot_review" class="accent">已开启每集开拍前预审</span>
            </div>
          </div>
          <el-tag size="large" :type="running ? 'primary' : 'info'">{{
            statusText(project.status)
          }}</el-tag>
        </div>
        <el-card shadow="never" class="section">
          <p>{{ project.brief }}</p>
          <div class="actions">
            <el-button
              v-if="!outlineConfirmed"
              type="primary"
              :loading="busy"
              @click="generateOutline"
              >{{
                episodes.length ? "重新生成大纲" : "生成分集大纲"
              }}</el-button
            ><el-button
              v-if="
                outlineConfirmed &&
                !running &&
                !pendingSnapshot &&
                !pendingShootEpisode &&
                project.status !== 'completed'
              "
              type="primary"
              :loading="busy"
              @click="action(() => seriesApi.run(projectId))"
              >{{ hasStarted ? "继续生成下一集" : "开始生成第一集" }}</el-button
            ><el-button
              v-if="running"
              type="warning"
              :loading="busy"
              @click="action(() => seriesApi.stop(projectId))"
              >停止生成</el-button
            ><el-button @click="refresh" :disabled="busy">刷新状态</el-button
            ><el-button
              v-if="episodes.length"
              :disabled="published.length !== episodes.length"
              :loading="busy"
              @click="compile"
              >合成长片</el-button
            ><el-button
              v-if="episodes.length"
              :disabled="published.length !== episodes.length"
              tag="a"
              :href="seriesApi.downloadUrl(projectId)"
              target="_blank"
              >下载整季 ZIP</el-button
            >
          </div>
          <p v-if="running" class="hint">
            正在制作本集，状态会自动更新。停止后可以继续。
          </p>
          <el-progress
            v-if="episodes.length"
            :percentage="Math.round((published.length / episodes.length) * 100)"
            :format="() => `${published.length} / ${episodes.length} 集已完成`"
          />
        </el-card>

        <el-alert
          v-if="project.last_error || project.error"
          :title="String(project.last_error || project.error)"
          type="error"
          :closable="false"
          show-icon
          class="notice"
        />

        <el-alert
          v-if="pendingShootEpisode"
          :title="`第 ${pendingShootEpisode.episode_num} 集首帧与配音已生成完毕，正在等待开拍前预审。`"
          type="info"
          :closable="false"
          show-icon
          class="notice"
        >
          <div class="alert-actions">
            <el-button
              type="primary"
              plain
              @click="router.push(`/progress/${pendingShootEpisode.task_id}`)"
            >
              进入分镜预审与单镜试拍
            </el-button>
            <el-button
              type="primary"
              :loading="busy"
              @click="approveEpisodeShoot(pendingShootEpisode)"
            >
              确认本集开拍 →
            </el-button>
          </div>
        </el-alert>

        <el-alert
          v-if="pendingSnapshot"
          title="本集已完成，请检查剧情延续信息并确认，再继续下一集。"
          type="warning"
          :closable="false"
          show-icon
          class="notice"
        >
          <el-button
            type="primary"
            :disabled="busy"
            @click="openSnapshot(pendingSnapshot)"
            >查看并确认剧情延续</el-button
          >
        </el-alert>

        <el-card shadow="never" class="section" v-if="episodes.length">
          <div class="heading">
            <h2>分集大纲</h2>
            <el-tag v-if="outlineConfirmed" type="success">已确认</el-tag
            ><span v-else class="hint">可修改每集内容，保存后确认开拍</span>
          </div>
          <div
            v-for="episode in outline"
            :key="episode.episode_num"
            class="outline-row"
          >
            <h3>第 {{ episode.episode_num }} 集</h3>
            <div class="outline-grid">
              <el-form-item label="标题"
                ><el-input
                  v-model="episode.title"
                  :disabled="outlineConfirmed"
                  @input="dirty = true" /></el-form-item
              ><el-form-item label="开场悬念"
                ><el-input
                  v-model="episode.opening_hook"
                  type="textarea"
                  :disabled="outlineConfirmed"
                  @input="dirty = true" /></el-form-item
              ><el-form-item label="核心事件"
                ><el-input
                  v-model="episode.core_event"
                  type="textarea"
                  :disabled="outlineConfirmed"
                  @input="dirty = true" /></el-form-item
              ><el-form-item label="结尾悬念"
                ><el-input
                  v-model="episode.ending_hook"
                  type="textarea"
                  :disabled="outlineConfirmed"
                  @input="dirty = true"
              /></el-form-item>
            </div>
          </div>
          <div v-if="!outlineConfirmed" class="actions">
            <el-button :loading="busy" @click="saveOutline">保存大纲</el-button
            ><el-button type="primary" :loading="busy" @click="confirmOutline"
              >确认大纲，准备开拍</el-button
            ><span v-if="dirty" class="hint">有尚未保存的修改</span>
          </div>
        </el-card>

        <el-card shadow="never" class="section" v-if="episodes.length">
          <h2>逐集制作</h2>
          <div
            v-for="episode in episodes"
            :key="episode.episode_num"
            class="episode"
          >
            <div>
              <h3>第 {{ episode.episode_num }} 集 · {{ episode.title }}</h3>
              <el-tag :type="episode.status === 'failed' ? 'danger' : episode.status === 'awaiting_shoot_review' ? 'warning' : 'info'">{{
                statusText(episode.status)
              }}</el-tag>
              <p v-if="episode.error || episode.last_error" class="failure">
                {{ episode.error || episode.last_error }}
              </p>
              <p
                v-if="
                  episode.dependency_changed ||
                  episode.requires_dependency_ack ||
                  ['dependency_changed', 'stale', 'needs_review'].includes(episode.status)
                "
                class="hint"
              >
                前集内容已更新，可先查看前后集状态差异，再决定沿用或重生成本集。
              </p>
            </div>
            <div class="actions">
              <el-button
                v-if="episode.task_id && episode.status !== 'success'"
                @click="router.push(`/progress/${episode.task_id}`)"
              >
                {{ episode.status === 'awaiting_shoot_review' ? '分镜预审与试拍' : '查看制作进度' }}
              </el-button>
              <el-button
                v-if="episode.status === 'awaiting_shoot_review' && episode.task_id"
                type="primary"
                :disabled="busy || running"
                @click="approveEpisodeShoot(episode)"
              >
                确认本集开拍
              </el-button>
              <el-button
                v-if="episode.task_id"
                @click="router.push(`/preview/${episode.task_id}`)"
                >预览与配音编辑</el-button
              >
              <EpisodeMaterials
                v-if="episode.status === 'success'"
                :key="episode.task_id"
                :series-id="projectId"
                :episode-num="episode.episode_num"
              />
              <el-button
                v-if="episode.output_snapshot_id"
                @click="openSnapshot(episode.output_snapshot_id)"
                >剧情延续</el-button
              >
              <el-button
                v-if="
                  episode.episode_num >= 2 &&
                  (episode.dependency_changed ||
                    episode.requires_dependency_ack ||
                    ['dependency_changed', 'stale', 'needs_review'].includes(episode.status) ||
                    episode.input_snapshot_id)
                "
                :disabled="busy"
                @click="openDependencyDiff(episode)"
              >
                查看前后集差异
              </el-button>
              <el-button
                v-if="
                  episode.dependency_changed ||
                  episode.requires_dependency_ack ||
                  ['dependency_changed', 'stale', 'needs_review'].includes(
                    episode.status,
                  )
                "
                type="primary"
                plain
                :disabled="busy || running"
                @click="
                  action(() =>
                    seriesApi.acceptDependency(projectId, episode.episode_num),
                  )
                "
                >接受前集更新</el-button>
              <el-button
                v-if="!episode.task_id || episode.status === 'failed'"
                type="primary"
                plain
                :disabled="busy || running"
                @click="openBindTaskDialog(episode)"
              >
                🔗 关联已生成单集
              </el-button>
              <el-button
                v-if="episode.task_id || episode.status === 'failed'"
                :disabled="busy || running"
                @click="regenerate(episode)"
                >重新生成本集</el-button
              >
            </div>
          </div>
        </el-card>

        <!-- 跨集四本连续性台账 -->
        <el-card shadow="never" class="section" v-if="project.continuity_ledgers">
          <div class="heading">
            <div>
              <h2>📒 跨集四本连续性台账（防止跨集战损复位、道具瞬移、角色未卜先知穿帮）</h2>
              <p class="hint">
                实时追踪全季「物理与服装台账、信息认知差台账、关键道具归属台账、人物恩怨弧线台账」。
              </p>
            </div>
            <el-button size="small" type="primary" plain @click="openLedgerEditor">
              编辑连续性台账
            </el-button>
          </div>
          <div class="ledger-four-grid">
            <div class="ledger-card">
              <h4>① 物理状态与战损服装台账</h4>
              <div
                v-for="(item, idx) in project.continuity_ledgers.physical_ledger || []"
                :key="'p' + idx"
                class="ledger-row"
              >
                <strong>{{ item.character }}</strong> · {{ item.location }}
                <div class="hint">{{ item.costume_state }}｜{{ item.injury_or_mark }}</div>
              </div>
            </div>
            <div class="ledger-card">
              <h4>② 信息认知与秘密差台账（防未卜先知）</h4>
              <div
                v-for="(item, idx) in project.continuity_ledgers.knowledge_ledger || []"
                :key="'k' + idx"
                class="ledger-row"
              >
                <strong>{{ item.character }}</strong>
                <div class="hint">已知：{{ item.known_facts }}</div>
                <div class="hint">盲区：{{ item.hidden_blindspots }}</div>
              </div>
            </div>
            <div class="ledger-card">
              <h4>③ 关键道具归属与状态台账</h4>
              <div
                v-for="(item, idx) in project.continuity_ledgers.prop_ledger || []"
                :key="'pr' + idx"
                class="ledger-row"
              >
                <strong>{{ item.prop_name }}</strong>（持有人：{{ item.holder }}）
                <div class="hint">{{ item.condition }} · {{ item.narrative_role }}</div>
              </div>
            </div>
            <div class="ledger-card">
              <h4>④ 恩怨筹码与关系弧线台账</h4>
              <div
                v-for="(item, idx) in project.continuity_ledgers.relationship_ledger || []"
                :key="'r' + idx"
                class="ledger-row"
              >
                <strong>{{ item.pair }}</strong> · {{ item.tension_level }}
                <div class="hint">{{ item.unsettled_debt }}</div>
              </div>
            </div>
          </div>
        </el-card>

        <GenerationUsage
          v-if="projectId"
          scope-type="series"
          :scope-id="String(projectId)"
          :refresh-key="`${project?.status || ''}-${project?.updated_at || ''}`"
          :active="running"
        />
      </template>

      <el-dialog
        v-model="bindEpisodeDialog"
        :title="`关联已生成单集到第 ${bindTargetEpisode?.episode_num || 1} 集`"
        width="min(560px, 94vw)"
      >
        <p class="hint" style="margin-top: 0">
          可将已在单集任务中生成好的视频或分镜任务直接并入本集，并自动提取本集结尾剧情延续状态。
        </p>
        <el-select
          v-model="selectedBindTaskId"
          placeholder="请选择一个独立单集任务"
          style="width: 100%"
        >
          <el-option
            v-for="t in bindableTasks"
            :key="t.task_id"
            :label="`${t.status === 'success' ? '【已成片】' : '【制作中】'}${(t.topic || t.user_input || t.task_id).slice(0, 38)}（${t.task_id}）`"
            :value="t.task_id"
          />
        </el-select>
        <template #footer>
          <el-button @click="bindEpisodeDialog = false">取消</el-button>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="!selectedBindTaskId"
            @click="confirmBindEpisodeTask"
          >
            确认关联到本集
          </el-button>
        </template>
      </el-dialog>

      <el-dialog
        v-model="ledgerDialog"
        title="编辑跨集四本连续性台账"
        width="min(760px, 94vw)"
      >
        <p class="hint">修改后将同步更新本季后续剧集的跨集状态约束。</p>
        <el-input v-model="ledgerJson" type="textarea" :rows="14" />
        <template #footer>
          <el-button @click="ledgerDialog = false">取消</el-button>
          <el-button type="primary" :loading="busy" @click="saveLedgerChanges">
            保存四本台账
          </el-button>
        </template>
      </el-dialog>

      <el-dialog
        v-model="snapshotDialog"
        title="确认剧情延续"
        width="min(720px, 94vw)"
      >
        <el-alert
          v-if="snapshot?.source_kind === 'planned'"
          title="这是根据大纲和分镜推测的计划状态，尚未自动观察实际画面。请结合成片人工检查。"
          type="warning"
          :closable="false"
          show-icon
        />
        <p>
          检查本集结束时的角色状态、地点和未解悬念。确认后，后续集数将接着这里展开。
        </p>
        <el-alert
          v-if="snapshot?.confirmed || snapshot?.status === 'published'"
          title="这份剧情延续已确认"
          type="success"
          :closable="false"
        />
        <div v-if="snapshot" class="snapshot-summary">
          <img
            v-if="snapshot.source_frame_path"
            class="snapshot-frame"
            :src="'/' + snapshot.source_frame_path.replace(/^backend\//, '')"
            alt="本集成片末帧"
          />
          <div v-for="(value, key) in snapshotState" :key="key">
            <strong>{{ stateLabels[key] || key }}</strong>
            <pre>{{
              typeof value === "string" ? value : JSON.stringify(value, null, 2)
            }}</pre>
          </div>
        </div>
        <details v-if="snapshot?.observation">
          <summary>采样画面观察</summary>
          <pre>{{ JSON.stringify(snapshot.observation, null, 2) }}</pre>
        </details>
        <el-collapse
          ><el-collapse-item title="高级详情：编辑剧情状态" name="state"
            ><p class="hint">仅在需要修正剧情时修改，保留原有字段结构。</p>
            <el-input
              v-model="stateJson"
              type="textarea"
              :rows="12" /></el-collapse-item
        ></el-collapse>
        <template #footer
          ><el-button @click="snapshotDialog = false">关闭</el-button
          ><el-button
            type="primary"
            :loading="busy"
            :disabled="snapshot?.confirmed || snapshot?.status === 'published'"
            @click="confirmSnapshot"
            >确认剧情延续</el-button
          ></template
        >
      </el-dialog>

      <el-dialog
        v-model="diffDialog"
        :title="diffData ? `第 ${diffData.episode_num} 集 · 前集剧情状态差异对比` : '前后集差异对比'"
        width="min(760px, 94vw)"
      >
        <template v-if="diffData">
          <p class="hint">
            对比本集生成时使用的旧快照（<code>{{ diffData.from_snapshot }}</code>）与上一集最新确认的快照（<code>{{ diffData.to_snapshot }}</code>）。若差异不影响本集镜头，可直接接受更新避免重跑整集。
          </p>
          <el-empty
            v-if="!diffData.changes?.length"
            description="前后两次快照状态完全一致，无需重跑本集"
            :image-size="72"
          />
          <div v-else class="diff-list">
            <div
              v-for="(change, idx) in diffData.changes"
              :key="idx"
              class="diff-item"
            >
              <div class="diff-item-header">
                <strong>{{ formatDiffField(change.field) }}</strong>
                <el-tag
                  size="small"
                  :type="change.kind === 'added' ? 'success' : change.kind === 'removed' ? 'danger' : 'warning'"
                >
                  {{ ({ added: '新增', removed: '移除', changed: '修改' })[change.kind] || change.kind }}
                </el-tag>
              </div>
              <div class="diff-columns">
                <div class="diff-col old-col">
                  <span class="diff-col-label">本集原依据状态</span>
                  <pre>{{ formatDiffValue(change.before) }}</pre>
                </div>
                <div class="diff-col new-col">
                  <span class="diff-col-label">上一集最新确认状态</span>
                  <pre>{{ formatDiffValue(change.after) }}</pre>
                </div>
              </div>
            </div>
          </div>
        </template>
        <template #footer>
          <el-button @click="diffDialog = false">关闭</el-button>
          <el-button
            v-if="diffEpisode"
            :loading="busy"
            :disabled="running"
            @click="regenerateFromDiff"
          >
            按新状态重新生成本集
          </el-button>
          <el-button
            v-if="diffEpisode"
            type="primary"
            :loading="busy"
            :disabled="running"
            @click="acceptDependencyFromDiff"
          >
            沿用当前单集（接受更新）
          </el-button>
        </template>
      </el-dialog>
    </div>
  </BasicLayout>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import BasicLayout from "../layouts/BasicLayout.vue";
import EpisodeMaterials from "../components/EpisodeMaterials.vue";
import GenerationUsage from "../components/GenerationUsage.vue";
import { seriesApi } from "../api/series";
import { videoApi } from "../api/video";
import { statusText } from "../utils/seriesStatus";
import { durationText, modeText } from "../utils/seriesDuration";
const route = useRoute(),
  router = useRouter(),
  projectId = route.params.id;
const project = ref(null),
  loading = ref(false),
  busy = ref(false),
  error = ref(""),
  outline = ref([]),
  dirty = ref(false),
  snapshotDialog = ref(false),
  snapshot = ref(null),
  snapshotId = ref(""),
  stateJson = ref(""),
  diffDialog = ref(false),
  diffData = ref(null),
  diffEpisode = ref(null),
  ledgerDialog = ref(false),
  ledgerJson = ref(""),
  bindEpisodeDialog = ref(false),
  bindTargetEpisode = ref(null),
  bindableTasks = ref([]),
  selectedBindTaskId = ref("");
let timer,
  fetching = false,
  disposed = false;
const episodes = computed(() => project.value?.episodes || []);
const running = computed(() =>
  ["running", "generating", "queued", "editing"].includes(
    project.value?.status,
  ),
);
const outlineConfirmed = computed(
  () =>
    !!(
      project.value?.outline_confirmed ||
      project.value?.confirmed ||
      [
        "ready",
        "confirmed",
        "running",
        "generating",
        "awaiting_shoot_review",
        "needs_confirmation",
        "awaiting_confirmation",
        "paused",
        "stopped",
        "completed",
        "failed",
      ].includes(project.value?.status)
    ),
);
const published = computed(() =>
  episodes.value.filter((e) =>
    ["published", "completed", "success"].includes(e.status),
  ),
);
const hasStarted = computed(() => episodes.value.some((e) => e.task_id));
const pendingShootEpisode = computed(() =>
  episodes.value.find((e) => e.status === "awaiting_shoot_review"),
);
const pendingSnapshot = computed(
  () =>
    project.value?.pending_snapshot_id ||
    episodes.value.find((e) =>
      ["needs_confirmation", "awaiting_confirmation"].includes(e.status),
    )?.output_snapshot_id ||
    (["needs_confirmation", "awaiting_confirmation"].includes(
      project.value?.status,
    )
      ? [...episodes.value].reverse().find((e) => e.output_snapshot_id)
          ?.output_snapshot_id
      : null),
);
const snapshotState = computed(
  () => snapshot.value?.state || snapshot.value?.continuity_state || {},
);
const stateLabels = {
  characters: "角色状态",
  character_states: "角色状态",
  location: "当前地点",
  time: "故事时间",
  unresolved_hooks: "未解悬念",
  open_threads: "未解悬念",
  summary: "本集回顾",
  events: "发生的事件",
  props: "关键物品",
  relationships: "人物关系",
  ending_hook: "结尾悬念",
  core_event: "核心事件",
  last_scene_start_state: "最后一幕开始状态",
  last_scene_end_state: "最后一幕结束状态",
  note: "确认说明",
};
function formatDiffField(field) {
  if (!field) return "状态项";
  return field
    .split(".")
    .map((part) => stateLabels[part] || part)
    .join(" · ");
}
function formatDiffValue(val) {
  if (val === null || val === undefined) return "（无）";
  return typeof val === "string" ? val : JSON.stringify(val, null, 2);
}
function schedule() {
  clearTimeout(timer);
  if (!disposed && running.value) timer = setTimeout(refresh, 3000);
}
async function refresh() {
  if (fetching || disposed) return;
  fetching = true;
  try {
    const data = await seriesApi.get(projectId);
    if (disposed) return;
    project.value = data;
    if (!dirty.value)
      outline.value = (data.episodes || []).map((e) => ({
        episode_num: e.episode_num,
        title: e.title || "",
        opening_hook: e.opening_hook || "",
        core_event: e.core_event || "",
        ending_hook: e.ending_hook || "",
      }));
  } catch (e) {
    error.value = e.message;
  } finally {
    fetching = false;
    schedule();
  }
}
async function action(fn) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await fn();
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function approveEpisodeShoot(episode) {
  if (!episode?.task_id) return;
  await action(() => seriesApi.approveShoot(projectId, episode.task_id));
}
async function openBindTaskDialog(episode) {
  bindTargetEpisode.value = episode;
  selectedBindTaskId.value = "";
  try {
    const data = await videoApi.listTasks();
    const list = Array.isArray(data) ? data : data.tasks || [];
    bindableTasks.value = list.filter((t) => t && t.task_id && !t.series_id);
    if (bindableTasks.value.length) {
      const cleanTitle = String(project.value?.title || "").replace(/[《》\s]/g, "");
      const matched = cleanTitle
        ? bindableTasks.value.find((t) =>
            String(`${t.topic || ""} ${t.user_input || ""}`).includes(cleanTitle),
          )
        : null;
      selectedBindTaskId.value = (matched || bindableTasks.value[0]).task_id;
    }
  } catch {
    bindableTasks.value = [];
  }
  bindEpisodeDialog.value = true;
}
async function confirmBindEpisodeTask() {
  if (!bindTargetEpisode.value || !selectedBindTaskId.value) return;
  const epNum = bindTargetEpisode.value.episode_num;
  const taskId = selectedBindTaskId.value;
  await action(async () => {
    await seriesApi.bindEpisodeTask(projectId, epNum, taskId);
    bindEpisodeDialog.value = false;
  });
}
function openLedgerEditor() {
  ledgerJson.value = JSON.stringify(
    project.value?.continuity_ledgers || {},
    null,
    2,
  );
  ledgerDialog.value = true;
}
async function saveLedgerChanges() {
  let parsed;
  try {
    parsed = JSON.parse(ledgerJson.value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error();
    }
  } catch {
    error.value = "台账 JSON 格式有误，请检查括号与引号";
    return;
  }
  await action(async () => {
    await seriesApi.saveLedgers(projectId, parsed);
    ledgerDialog.value = false;
  });
}
async function openDependencyDiff(episode) {
  await action(async () => {
    diffData.value = await seriesApi.dependencyDiff(
      projectId,
      episode.episode_num,
    );
    diffEpisode.value = episode;
    diffDialog.value = true;
  });
}
async function acceptDependencyFromDiff() {
  if (!diffEpisode.value) return;
  const num = diffEpisode.value.episode_num;
  await action(async () => {
    await seriesApi.acceptDependency(projectId, num);
    diffDialog.value = false;
  });
}
async function regenerateFromDiff() {
  if (!diffEpisode.value) return;
  const ep = diffEpisode.value;
  diffDialog.value = false;
  await regenerate(ep);
}
async function generateOutline() {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        "重新生成会替换未保存的大纲，是否继续？",
        "重新生成大纲",
      );
    } catch {
      return;
    }
  }
  await action(async () => {
    await seriesApi.outline(projectId);
    dirty.value = false;
  });
}
async function saveOutline() {
  await action(async () => {
    await seriesApi.saveOutline(projectId, outline.value);
    dirty.value = false;
  });
}
async function confirmOutline() {
  await action(async () => {
    await seriesApi.saveOutline(projectId, outline.value);
    dirty.value = false;
    await seriesApi.confirm(projectId);
  });
}
async function openSnapshot(id) {
  await action(async () => {
    const data = await seriesApi.snapshot(projectId, id);
    snapshot.value = data;
    snapshotId.value = id;
    stateJson.value = JSON.stringify(
      data.state || data.continuity_state || {},
      null,
      2,
    );
    snapshotDialog.value = true;
  });
}
async function confirmSnapshot() {
  let state;
  try {
    state = JSON.parse(stateJson.value);
    if (!state || Array.isArray(state) || typeof state !== "object")
      throw new Error();
  } catch {
    error.value = "剧情状态格式不正确，请填写完整的对象内容";
    return;
  }
  await action(async () => {
    await seriesApi.confirmSnapshot(projectId, snapshotId.value, state);
    snapshotDialog.value = false;
  });
}
async function compile() {
  await action(async () => {
    const blob = await seriesApi.compile(projectId);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${project.value.title}.mp4`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
async function regenerate(episode) {
  try {
    await ElMessageBox.confirm(
      `重新生成第 ${episode.episode_num} 集？后续集数可能需要重新确认剧情依赖。`,
      "重新生成本集",
    );
    await action(() => seriesApi.regenerate(projectId, episode.episode_num));
  } catch {}
}
onMounted(async () => {
  loading.value = true;
  await refresh();
  loading.value = false;
});
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
});
</script>
<style scoped>
.page {
  max-width: 1250px;
  margin: auto;
}
.heading,
.actions,
.episode {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.heading {
  margin: 12px 0 20px;
}
.spec-pills {
  display: flex;
  flex-wrap: wrap;
  flex-wrap: balance;
  gap: 6px;
  margin-top: 10px;
}
.spec-pills span {
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--va-surface-subtle);
  border: 1px solid var(--va-border);
  color: var(--va-ink-secondary);
  font-size: 0.75rem;
  font-weight: 600;
}
.spec-pills span.accent {
  background: var(--va-brand-soft);
  border-color: var(--va-brand-border);
  color: var(--va-brand-hover);
}
h1 {
  font-size: 28px;
  margin: 0;
}
h2 {
  font-size: 20px;
  margin: 0 0 18px;
}
h3 {
  font-size: 16px;
  margin: 0 0 12px;
}
p {
  color: #64748b;
  line-height: 1.65;
}
.section,
.notice {
  margin-top: 20px;
}
.alert-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 10px;
}
.actions {
  justify-content: flex-start;
  margin: 16px 0;
}
.hint {
  font-size: 13px;
  color: #64748b;
}
.outline-row {
  border-top: 1px solid #e2e8f0;
  padding: 20px 0;
}
.outline-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 20px;
}
.outline-grid :deep(.el-form-item) {
  display: block;
  margin-bottom: 0;
}
.episode {
  padding: 20px 0;
  border-top: 1px solid #e2e8f0;
}
.failure {
  color: #dc2626;
}
.snapshot-summary {
  max-height: 360px;
  overflow: auto;
  margin: 20px 0;
}
.snapshot-summary pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #f8fafc;
  padding: 12px;
  border-radius: 6px;
  font: inherit;
  color: #475569;
}
.snapshot-frame {
  display: block;
  max-width: 100%;
  max-height: 260px;
  object-fit: contain;
}
.diff-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 420px;
  overflow-y: auto;
  margin-top: 14px;
}
.diff-item {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px 14px;
  background: #f8fafc;
}
.diff-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.diff-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.diff-col {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
}
.diff-col.old-col {
  border-left: 3px solid #94a3b8;
}
.diff-col.new-col {
  border-left: 3px solid #2563eb;
}
.diff-col-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
}
.diff-col pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13px;
  color: #1e293b;
}
.el-progress {
  margin-top: 24px;
}
.el-progress :deep(.el-progress__text) {
  min-width: 140px;
}
.ledger-four-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
  margin-top: 12px;
}
.ledger-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px 14px;
}
.ledger-card h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #1e293b;
}
.ledger-row {
  padding: 6px 0;
  border-top: 1px dashed #cbd5e1;
  font-size: 13px;
}
.ledger-row:first-of-type {
  border-top: none;
}
@media (max-width: 650px) {
  .outline-grid,
  .diff-columns {
    grid-template-columns: 1fr;
  }
  .episode {
    align-items: flex-start;
  }
  .actions .el-button {
    margin-left: 0;
  }
}
</style>
