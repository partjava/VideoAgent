<template>
  <BasicLayout
    ><div class="page" v-loading="loading">
      <div class="heading">
        <div>
          <h1>连载短剧</h1>
          <p>从故事梗概到逐集成片，每一集都由你确认后继续。</p>
        </div>
        <div class="row">
          <el-button type="success" plain @click="router.push('/studio-craft')">
            📖 小说/长文一键拆剧 →
          </el-button>
          <el-button type="primary" @click="dialog = true">新建连载</el-button>
        </div>
      </div>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />
      <el-empty
        v-if="!loading && !projects.length"
        description="写下一个故事，开始你的第一部连载"
      />
      <div class="cards">
        <el-card v-for="project in projects" :key="id(project)" shadow="never"
          ><div class="heading">
            <h2>{{ project.title }}</h2>
            <el-tag>{{ statusText(project.status) }}</el-tag>
          </div>
          <p>{{ project.brief }}</p>
          <p>
            {{ project.total_episodes }} 集 · 每集
            {{ durationText(project) }} · {{ modeText(project.generation_mode) }} · {{ project.style }} ·
            {{ project.ratio }}
          </p>
          <div class="heading">
            <el-button
              type="primary"
              @click="router.push(`/series/${id(project)}`)"
              >进入工作台</el-button
            ><el-button type="danger" link @click="archive(project)"
              >归档项目</el-button
            >
          </div></el-card
        >
      </div>
      <el-dialog v-model="dialog" title="新建连载" width="min(680px, 94vw)">
        <el-form label-position="top"
          ><el-form-item label="项目名"
            ><el-input v-model="form.title" maxlength="120" /></el-form-item
          ><el-form-item label="故事梗概"
            ><el-input
              v-model="form.brief"
              type="textarea"
              :rows="4"
              placeholder="主角是谁？想要什么？会遇到什么阻碍？"
          /></el-form-item>
          <div class="row">
            <el-form-item label="总集数"
              ><el-select v-model="form.total_episodes"
                ><el-option
                  v-for="n in [3, 5, 10]"
                  :key="n"
                  :value="n"
                  :label="`${n} 集`" /></el-select></el-form-item
            ><el-form-item label="生成模式"
              ><el-select v-model="form.generation_mode"
                ><el-option value="auto_director" label="AI 自主导演（推荐 · 弹性时长）" /><el-option
                  value="storyboard_flow"
                  label="剧组单元自由流（多镜交替 · 弹性时长）" /><el-option
                  value="native_10s"
                  label="原生 10 秒整切（时长须为 10 的倍数）" /></el-select
            ></el-form-item>
          </div>
          <div class="row" v-if="form.generation_mode !== 'native_10s'">
            <el-form-item label="单集时长范围（秒）">
              <div class="range-row">
                <el-input-number v-model="form.episode_duration_min" :min="10" :max="180" :step="5" controls-position="right" />
                <span>~</span>
                <el-input-number v-model="form.episode_duration_max" :min="10" :max="180" :step="5" controls-position="right" />
              </div>
              <div class="preset-row">
                <button v-for="p in rangePresets" :key="p.label" type="button" class="preset-chip"
                  @click="form.episode_duration_min = p.min; form.episode_duration_max = p.max">{{ p.label }}</button>
              </div>
            </el-form-item>
          </div>
          <div class="row" v-else>
            <el-form-item label="单集时长（10 秒整数倍）"
              ><el-select v-model="form.episode_duration"
                ><el-option
                  v-for="n in [10, 20, 30, 40, 60, 90, 120]"
                  :key="n"
                  :value="n"
                  :label="`${n} 秒`" /></el-select
            ></el-form-item>
          </div>
          <div class="row">
            <el-form-item label="风格"
              ><el-radio-group v-model="form.style"
                ><el-radio-button label="短剧" /><el-radio-button
                  label="漫剧" /></el-radio-group></el-form-item
            ><el-form-item label="画幅"
              ><el-radio-group v-model="form.ratio"
                ><el-radio-button label="9:16" /><el-radio-button
                  label="16:9" /></el-radio-group
            ></el-form-item>
            <el-form-item label="剧本市场模式"
              ><el-radio-group v-model="form.market_mode"
                ><el-radio-button value="cn">国内中文剧</el-radio-button
                ><el-radio-button value="en">出海英文剧</el-radio-button></el-radio-group
            ></el-form-item>
          </div>
          <el-form-item label="固定角色（仅可选择已确认版本）"
            ><el-select
              v-model="form.character_ids"
              multiple
              placeholder="可选；也可先到角色资产创建"
              style="width: 100%"
              ><el-option
                v-for="character in confirmedCharacters"
                :key="character.character_id || character.id"
                :label="`${character.name} · v${character.revision || 1}`"
                :value="character.character_id || character.id" /></el-select
          ></el-form-item>
          <div class="row">
            <el-form-item label="整季生成操作额度上限（可选）"
              ><el-input
                v-model="form.generation_limit"
                type="number"
                min="1"
                max="10000"
                placeholder="留空不设上限"
            /></el-form-item>
          </div>
          <p class="hint">
            预计整季消耗：约 {{ estimatedSeriesOps.images }} 张图 + {{ estimatedSeriesOps.videos }} 段视频（共 {{ estimatedSeriesOps.total }} 次生成操作）。项目会保留所选角色的版本。
          </p>
          <div class="check-group">
            <el-checkbox v-model="form.pre_shoot_review">每集开拍前预审首帧与配音（支持单镜试拍）</el-checkbox>
            <el-checkbox v-model="form.auto_continue">画面证据满足条件时自动继续下一集</el-checkbox>
          </div>
          </el-form
        ><template #footer
          ><el-button @click="dialog = false">取消</el-button
          ><el-button
            type="primary"
            :loading="creating"
            :disabled="!form.title.trim() || !form.brief.trim()"
            @click="create"
            >创建项目</el-button
          ></template
        >
      </el-dialog>
    </div></BasicLayout
  >
</template>
<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import BasicLayout from "../layouts/BasicLayout.vue";
import { seriesApi } from "../api/series";
import { charactersApi } from "../api/characters";
import { statusText } from "../utils/seriesStatus";
import { durationText, modeText } from "../utils/seriesDuration";
const router = useRouter(),
  projects = ref([]),
  characters = ref([]),
  loading = ref(false),
  creating = ref(false),
  dialog = ref(false),
  error = ref("");
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
    const data = await seriesApi.list();
    projects.value = Array.isArray(data) ? data : data.series || [];
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
    });
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
  max-width: 1200px;
  margin: auto;
}
.heading,
.row {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.heading {
  margin-bottom: 20px;
}
h1 {
  font-size: 28px;
  margin: 0 0 8px;
}
h2 {
  font-size: 20px;
  margin: 0;
}
p {
  color: #64748b;
  line-height: 1.6;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
  gap: 20px;
  margin-top: 24px;
}
.row > * {
  flex: 1;
  min-width: 180px;
}
.hint {
  font-size: 13px;
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
}
.preset-chip {
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  font-size: 12px;
  cursor: pointer;
}
.preset-chip:hover {
  border-color: #4f46e5;
  color: #4f46e5;
}
</style>
