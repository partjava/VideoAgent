<template>
  <BasicLayout>
    <div class="page" v-loading="loading">
      <!-- ==================== 一级页面：多人物紧凑卡片列表 ==================== -->
      <template v-if="!selectedCharacter">
        <div class="heading">
          <div>
            <h1>角色资产库</h1>
            <p>集中管理所有剧集人物。点击任意人物「查看详情」可进入三视图定妆照、配音试听与 3D 空间检视台。</p>
          </div>
          <div class="toolbar">
            <el-input
              v-model="keyword"
              clearable
              placeholder="搜索角色名 / 外貌 / 服装"
              style="width: 220px"
            />
            <el-switch
              v-model="showArchived"
              active-text="显示已归档"
              @change="load"
            />
            <el-button type="primary" @click="edit()">新建角色</el-button>
          </div>
        </div>

        <el-alert
          v-if="error"
          :title="error"
          type="error"
          show-icon
          :closable="false"
          style="margin-bottom: 16px"
        />

        <el-empty
          v-if="!loading && !filteredCharacters.length"
          :description="keyword ? '没有匹配搜索条件的角色' : '还没有角色，点击右上角「新建角色」创建你的第一位人物'"
        />

        <div class="compact-grid">
          <el-card
            v-for="(character, index) in filteredCharacters"
            :key="id(character)"
            shadow="hover"
            class="compact-card"
            :class="{ 'archived-card': character.archived }"
          >
            <div class="compact-card-top">
              <span class="person-index">人物 {{ index + 1 }}</span>
              <div class="tag-group">
                <el-tag v-if="character.archived" size="small" type="info">已归档</el-tag>
                <el-tag size="small" :type="character.confirmed ? 'success' : 'warning'">
                  {{ character.confirmed ? "已确认" : "待确认" }} · v{{ character.revision || 1 }}
                </el-tag>
              </div>
            </div>

            <!-- 正面定妆照区域（有图显示正面图，无图显示空白占位） -->
            <div class="front-cover-box" @click="openDetail(character)">
              <img
                v-if="storedReference(character, 'front')"
                class="front-cover-img"
                :src="assetUrl(storedReference(character, 'front'))"
                :alt="`${character.name} · 正面定妆照`"
              />
              <div v-else class="front-cover-placeholder">
                <span class="empty-avatar-icon">👤</span>
                <span>暂无正面定妆照</span>
              </div>
              <span class="view-count-badge">
                已备 {{ countViews(character) }}/3 视图
              </span>
            </div>

            <!-- 基本信息摘要 -->
            <div class="compact-info" @click="openDetail(character)">
              <h2 class="compact-name">{{ character.name }}</h2>
              <p class="compact-dna">
                {{ character.face_dna || "尚未填写外貌特征" }}
              </p>
              <p class="compact-meta">
                {{ character.signature_clothing || "尚未填写标志服装" }}
              </p>
              <span class="voice-pill">🎙 {{ character.tts_voice || "默认声音" }}</span>
            </div>

            <!-- 卡片底部快捷按钮 -->
            <div class="compact-actions">
              <template v-if="!character.archived">
                <el-button
                  size="small"
                  :loading="generatingKey === `${id(character)}:front`"
                  :disabled="busy"
                  @click.stop="generatePortrait(character, 'front', 'realistic', false, false)"
                >
                  {{ storedReference(character, "front") ? "重绘正面" : "生成正面图" }}
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click.stop="openDetail(character)"
                >
                  详细信息 →
                </el-button>
              </template>
              <template v-else>
                <el-button
                  size="small"
                  type="success"
                  plain
                  :disabled="busy"
                  @click.stop="restore(character)"
                >
                  恢复角色
                </el-button>
                <el-button
                  size="small"
                  @click.stop="openDetail(character)"
                >
                  详细信息
                </el-button>
              </template>
            </div>
          </el-card>
        </div>
      </template>

      <!-- ==================== 二级页面：单个人物完整详情页 ==================== -->
      <template v-else>
        <div class="detail-topbar">
          <div class="detail-nav-left">
            <el-button @click="selectedCharacterId = ''">← 返回角色列表</el-button>
            <span class="breadcrumb-text">角色资产库 / {{ selectedCharacter.name }}</span>
          </div>
          <div class="detail-nav-right">
            <el-button
              size="small"
              :disabled="currentDetailIndex <= 0"
              @click=" switchAdjacentCharacter(-1)"
            >
              上一人物
            </el-button>
            <el-button
              size="small"
              :disabled="currentDetailIndex < 0 || currentDetailIndex >= filteredCharacters.length - 1"
              @click="switchAdjacentCharacter(1)"
            >
              下一人物
            </el-button>
          </div>
        </div>

        <el-alert
          v-if="error"
          :title="error"
          type="error"
          show-icon
          :closable="false"
          style="margin-bottom: 16px"
        />

        <el-card shadow="never" class="detail-card" :class="{ 'archived-card': selectedCharacter.archived }">
          <div class="heading">
            <div>
              <h2>{{ selectedCharacter.name }}</h2>
              <span class="muted">角色 ID：{{ id(selectedCharacter) }}</span>
            </div>
            <div class="tag-group">
              <el-tag v-if="selectedCharacter.archived" type="info">已归档</el-tag>
              <el-tag :type="selectedCharacter.confirmed ? 'success' : 'warning'">
                {{ selectedCharacter.confirmed ? "已确认" : "待确认" }} · v{{ selectedCharacter.revision || 1 }}
              </el-tag>
            </div>
          </div>

          <!-- 正面 / 侧面 / 全身 三视图大图 -->
          <div class="reference-grid">
            <figure v-for="view in views" :key="view.key">
              <img
                v-if="storedReference(selectedCharacter, view.key)"
                class="portrait detail-portrait"
                :src="assetUrl(storedReference(selectedCharacter, view.key))"
                :alt="`${selectedCharacter.name} · ${view.label}`"
              />
              <div v-else class="portrait detail-portrait placeholder">未上传</div>
              <figcaption class="view-caption">
                <span>{{ view.label }}</span>
                <el-button
                  v-if="!selectedCharacter.archived"
                  size="small"
                  link
                  type="primary"
                  :loading="generatingKey === `${id(selectedCharacter)}:${view.key}`"
                  :disabled="busy"
                  @click="generatePortrait(selectedCharacter, view.key, 'realistic', false, false)"
                >
                  {{ storedReference(selectedCharacter, view.key) ? `重绘${view.label}` : `AI生成${view.label}` }}
                </el-button>
              </figcaption>
            </figure>
          </div>

          <div class="detail-description">
            <p><strong>外貌特征：</strong>{{ selectedCharacter.face_dna || "尚未填写外貌" }}</p>
            <p class="muted">
              <strong>标志服装：</strong>{{ selectedCharacter.signature_clothing || "尚未填写服装" }}
              &nbsp;·&nbsp;
              <strong>配音参数：</strong>{{ selectedCharacter.tts_voice || "默认声音" }}（语速 {{ selectedCharacter.rate || "+0%" }} / 音调 {{ selectedCharacter.pitch || "+0Hz" }}）
            </p>
          </div>

          <div class="actions">
            <template v-if="!selectedCharacter.archived">
              <el-button @click="edit(selectedCharacter)">编辑设定与上传图片</el-button>
              <el-button
                type="primary"
                plain
                :loading="generatingKey === `${id(selectedCharacter)}:all`"
                :disabled="busy"
                @click="generatePortrait(selectedCharacter, 'all', '3d', withTurntableVideo, true)"
              >
                {{ withTurntableVideo ? "一键生成三视图+360°视频" : "一键生成三视图（仅生图）" }}
              </el-button>
              <el-checkbox
                v-model="withTurntableVideo"
                size="small"
                class="turntable-opt-check"
              >
                顺便生成360°视频
              </el-checkbox>
              <el-button
                type="success"
                plain
                :disabled="busy"
                @click="open3DStage(selectedCharacter)"
              >
                3D 空间检视台
              </el-button>
              <el-button :disabled="busy" @click="previewVoice(selectedCharacter)">
                试听声音
              </el-button>
              <el-button
                v-if="!selectedCharacter.confirmed"
                type="primary"
                :disabled="busy || !storedReference(selectedCharacter, 'front')"
                @click="act(() => charactersApi.confirm(id(selectedCharacter)))"
              >
                确认角色
              </el-button>
              <el-button type="danger" link @click="archive(selectedCharacter)">
                归档
              </el-button>
            </template>
            <template v-else>
              <el-button
                type="success"
                plain
                :disabled="busy"
                @click="restore(selectedCharacter)"
              >
                恢复角色
              </el-button>
            </template>
          </div>
        </el-card>
      </template>

      <audio v-if="audioUrl" :src="audioUrl" controls autoplay class="audio" />

      <!-- 新建 / 编辑角色弹窗 -->
      <el-dialog
        v-model="dialog"
        :title="editing ? '编辑角色 · 保存为待确认新版本' : '新建角色'"
        width="min(640px, 94vw)"
      >
        <div class="template-bar">
          <span class="template-label">常用人设模板（点击一键填入）：</span>
          <div class="template-tags">
            <el-button
              v-for="tpl in presetTemplates"
              :key="tpl.label"
              size="small"
              round
              @click="applyTemplate(tpl)"
            >
              {{ tpl.label }}
            </el-button>
          </div>
        </div>
        <el-form label-position="top" @submit.prevent="save">
          <el-form-item label="角色名">
            <el-input v-model="form.name" maxlength="80" />
          </el-form-item>
          <el-form-item label="外貌特征">
            <el-input
              v-model="form.face_dna"
              type="textarea"
              :rows="3"
              placeholder="年龄、五官、发型和辨识特征"
            />
          </el-form-item>
          <el-form-item label="标志服装">
            <el-input v-model="form.signature_clothing" />
          </el-form-item>
          <el-form-item label="声音">
            <el-input
              v-model="form.tts_voice"
              placeholder="例如 zh-CN-XiaoxiaoNeural"
            />
          </el-form-item>
          <div class="row">
            <el-form-item label="语速">
              <el-input v-model="form.rate" placeholder="+0%" />
            </el-form-item>
            <el-form-item label="音调">
              <el-input v-model="form.pitch" placeholder="+0Hz" />
            </el-form-item>
          </div>
          <el-form-item
            v-for="view in views"
            :key="`${editSession}-${view.key}`"
            :label="`${view.label}参考图（可选本地上传，也可保存后用 AI 自动生成）`"
          >
            <input
              type="file"
              accept="image/png,image/jpeg"
              :disabled="busy"
              @change="readImage($event, view.key)"
            />
            <img
              v-if="pendingReferences[view.key] || storedReference(editing, view.key)"
              class="upload-preview"
              :src="pendingReferences[view.key] || assetUrl(storedReference(editing, view.key))"
              :alt="`${view.label}参考图预览`"
            />
          </el-form-item>
          <p class="muted">
            每张图支持 PNG / JPEG，最多 10 MB。也可以不上传图片直接保存角色，随后在列表或详情页一键生成定妆照。
          </p>
        </el-form>
        <template #footer>
          <el-button @click="dialog = false">取消</el-button>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="!form.name.trim() || readingImages > 0"
            @click="save"
          >
            保存角色
          </el-button>
        </template>
      </el-dialog>

      <!-- 3D 空间检视台弹窗 -->
      <Character3DStage
        v-model:visible="stageVisible"
        :character="stageCharacter"
        :busy-action="stageBusyAction"
        :asset-url="assetUrl"
        @generate-portrait="handleStagePortrait"
        @generate-turntable="handleStageTurntable"
      />
    </div>
  </BasicLayout>
</template>
<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import BasicLayout from "../layouts/BasicLayout.vue";
import Character3DStage from "../components/Character3DStage.vue";
import { charactersApi } from "../api/characters";
const characters = ref([]),
  loading = ref(false),
  busy = ref(false),
  generatingKey = ref(""),
  error = ref(""),
  dialog = ref(false),
  editing = ref(null),
  editSession = ref(0),
  readingImages = ref(0),
  pendingReferences = ref({}),
  audioUrl = ref(""),
  keyword = ref(""),
  showArchived = ref(false),
  selectedCharacterId = ref(""),
  stageVisible = ref(false),
  stageCharacterId = ref(""),
  stageBusyAction = ref(""),
  withTurntableVideo = ref(false);

const id = (c) => c?.character_id || c?.id;

const filteredCharacters = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return characters.value;
  return characters.value.filter((c) =>
    [c.name, c.face_dna, c.signature_clothing]
      .filter(Boolean)
      .some((txt) => String(txt).toLowerCase().includes(q)),
  );
});

const selectedCharacter = computed(
  () => characters.value.find((c) => id(c) === selectedCharacterId.value) || null,
);

const currentDetailIndex = computed(() =>
  filteredCharacters.value.findIndex((c) => id(c) === selectedCharacterId.value),
);

const stageCharacter = computed(
  () => characters.value.find((c) => id(c) === stageCharacterId.value) || null,
);

const views = [
  { key: "front", label: "正面" },
  { key: "profile", label: "侧面" },
  { key: "whole", label: "全身" },
];

const presetTemplates = [
  {
    label: "冷峻男主 · 沈默",
    name: "沈默",
    face_dna:
      "28岁左右年轻男性，冷白皮，轮廓分明的立体五官，狭长深邃的黑眸，黑色碎发略微遮住眉骨，左眼尾有一颗极浅的泪痣，神情冷静克制。",
    signature_clothing:
      "深炭灰色立领长款风衣，内搭纯黑高领毛衣，左手腕佩戴一块银色复古机械表。",
    tts_voice: "zh-CN-YunjianNeural",
    rate: "-5%",
    pitch: "-2Hz",
  },
  {
    label: "干练女主 · 林知夏",
    name: "林知夏",
    face_dna:
      "24岁左右年轻女性，鹅蛋脸，眉眼清冷明艳，琥珀色瞳孔，乌黑微卷齐肩短发，鼻梁高挺，眼神敏锐果断。",
    signature_clothing:
      "米白色修身西装外套，内搭浅蓝丝质衬衫，佩戴极简银质耳环，斜挎黑色小方包。",
    tts_voice: "zh-CN-XiaoxiaoNeural",
    rate: "+0%",
    pitch: "+0Hz",
  },
  {
    label: "商界总裁 · 陆景深",
    name: "陆景深",
    face_dna:
      "30岁成熟男性，剑眉星目，下颌线凌厉，背头造型干净利落，佩戴金丝无框眼镜，眼神深不可测。",
    signature_clothing:
      "深藏青色高定三件套西装，酒红色暗纹领带，袖口带有银色蓝宝石袖扣。",
    tts_voice: "zh-CN-YunyangNeural",
    rate: "-3%",
    pitch: "-3Hz",
  },
  {
    label: "古风剑客 · 谢无尘",
    name: "谢无尘",
    face_dna:
      "22岁古风清俊青年，眉若远山，眼眸清澈冷冽，乌黑长发以白玉冠高束，额前留两缕碎发。",
    signature_clothing:
      "月白色广袖暗云纹锦袍，腰束玄色窄边玉带，腰侧悬挂古朴青铜长剑。",
    tts_voice: "zh-CN-YunxiNeural",
    rate: "-4%",
    pitch: "-2Hz",
  },
  {
    label: "元气少女 · 苏小棠",
    name: "苏小棠",
    face_dna:
      "19岁甜美少女，圆润杏眼灵动有神，自然微粉脸颊，栗色蓬松高马尾配空气刘海，笑容灿烂。",
    signature_clothing:
      "浅鹅黄色针织开衫，内搭白色学院风衬衫与百褶格纹短裙，背着奶油色帆布包。",
    tts_voice: "zh-CN-XiaoyiNeural",
    rate: "+5%",
    pitch: "+2Hz",
  },
  {
    label: "热血少年 · 陈星河",
    name: "陈星河",
    face_dna:
      "18岁阳光少年，小麦色健康肤色，浓眉大眼，黑色清爽刺猬头短发，左眉骨有一道细小浅痕，充满朝气。",
    signature_clothing:
      "红黑撞色连帽机能冲锋衣，内搭白色圆领T恤，黑色工装长裤。",
    tts_voice: "zh-CN-YunxiNeural",
    rate: "+4%",
    pitch: "+0Hz",
  },
];

const form = reactive({
  name: "",
  face_dna: "",
  signature_clothing: "",
  tts_voice: "zh-CN-YunyangNeural",
  rate: "+0%",
  pitch: "+0Hz",
});

function applyTemplate(tpl) {
  Object.assign(form, {
    name: tpl.name,
    face_dna: tpl.face_dna,
    signature_clothing: tpl.signature_clothing,
    tts_voice: tpl.tts_voice,
    rate: tpl.rate,
    pitch: tpl.pitch,
  });
}

const storedReference = (character, view) =>
  character?.reference_images?.[view] ||
  (view === "front" ? character?.reference_path : null);

function countViews(character) {
  return views.filter((v) => Boolean(storedReference(character, v.key))).length;
}

function openDetail(character) {
  error.value = "";
  selectedCharacterId.value = id(character);
}

function switchAdjacentCharacter(delta) {
  const nextIdx = currentDetailIndex.value + delta;
  const target = filteredCharacters.value[nextIdx];
  if (target) {
    selectedCharacterId.value = id(target);
  }
}

const assetUrl = (value) =>
  !value
    ? ""
    : value.startsWith("http") || value.startsWith("data:")
      ? value
      : `/${value.replace(/^\/+/, "").replace(/^backend\//, "")}`;

async function load() {
  loading.value = true;
  try {
    const data = await charactersApi.list(showArchived.value);
    characters.value = Array.isArray(data) ? data : data.characters || [];
    if (
      selectedCharacterId.value &&
      !characters.value.some((c) => id(c) === selectedCharacterId.value)
    ) {
      selectedCharacterId.value = "";
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function edit(character) {
  editing.value = character || null;
  editSession.value += 1;
  pendingReferences.value = {};
  Object.assign(
    form,
    {
      name: "",
      face_dna: "",
      signature_clothing: "",
      tts_voice: "zh-CN-YunyangNeural",
      rate: "+0%",
      pitch: "+0Hz",
    },
    character
      ? Object.fromEntries(
          Object.keys(form).map((key) => [key, character[key] ?? form[key]]),
        )
      : {},
  );
  dialog.value = true;
}

function readImage(event, view) {
  const file = event.target.files?.[0];
  delete pendingReferences.value[view];
  if (!file) return;
  if (
    !["image/png", "image/jpeg"].includes(file.type) ||
    file.size > 10 * 1024 * 1024
  ) {
    ElMessage.error("请选择 10 MB 以内的 PNG 或 JPEG 图片");
    event.target.value = "";
    return;
  }
  const input = event.target;
  const session = editSession.value;
  const reader = new FileReader();
  readingImages.value += 1;
  reader.onload = () => {
    if (editSession.value === session && input.files?.[0] === file) {
      pendingReferences.value[view] = reader.result;
    }
  };
  reader.onerror = () => ElMessage.error("图片读取失败");
  reader.onloadend = () => {
    readingImages.value -= 1;
  };
  reader.readAsDataURL(file);
}

async function act(action) {
  busy.value = true;
  error.value = "";
  try {
    await action();
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function generatePortrait(
  character,
  view = "all",
  style = "3d",
  includeTurntable = false,
  openStageAfter = false,
) {
  const cid = id(character);
  generatingKey.value = `${cid}:${view}`;
  await act(async () => {
    const res = await charactersApi.portrait(cid, view, style, includeTurntable);
    if (view === "all") {
      if (openStageAfter) {
        stageCharacterId.value = cid;
        stageVisible.value = true;
      }
      if (res?.turntable_warning) {
        ElMessage.warning(
          `已生成三视图定妆照（360°视角视频暂未生成：${res.turntable_warning}）`,
        );
      } else {
        ElMessage.success(
          includeTurntable
            ? `已为「${character.name}」生成正面、侧面、全身三视图及 360° 旋转视角视频！`
            : `已为「${character.name}」生成正面、侧面与全身三视图定妆照（未消耗视频额度）`,
        );
      }
    } else {
      ElMessage.success(
        `已生成「${character.name}」的${views.find((v) => v.key === view)?.label || ""}定妆照`,
      );
    }
  });
  generatingKey.value = "";
}

function open3DStage(character) {
  stageCharacterId.value = id(character);
  stageVisible.value = true;
}

async function handleStagePortrait({ view, style, include_turntable = false }) {
  if (!stageCharacter.value) return;
  stageBusyAction.value = view;
  await generatePortrait(stageCharacter.value, view, style, include_turntable, false);
  stageBusyAction.value = "";
}

async function handleStageTurntable() {
  if (!stageCharacter.value) return;
  const cid = id(stageCharacter.value);
  stageBusyAction.value = "turntable";
  await act(async () => {
    await charactersApi.turntable(cid, 5);
    ElMessage.success(`已为「${stageCharacter.value?.name || ""}」生成 360° 3D 旋转展示视频`);
  });
  stageBusyAction.value = "";
}

async function save() {
  if (busy.value || readingImages.value || !form.name.trim()) return;
  await act(async () => {
    const changed = Object.entries(form).some(
      ([key, value]) => value.trim() !== editing.value?.[key],
    );
    if (!editing.value) {
      editing.value = await charactersApi.create({ ...form });
    } else if (changed) {
      editing.value = await charactersApi.update(id(editing.value), {
        ...form,
      });
    }
    for (const { key } of views) {
      if (pendingReferences.value[key]) {
        editing.value = await charactersApi.reference(
          id(editing.value),
          pendingReferences.value[key],
          key,
        );
        delete pendingReferences.value[key];
      }
    }
    dialog.value = false;
  });
}

async function previewVoice(character) {
  await act(async () => {
    const data = await charactersApi.voicePreview(
      id(character),
      "你好，我已经准备好开始我们的故事了。",
    );
    const url =
      data.audio_url || data.audio_path || data.asset_path || data.url;
    if (!url) throw new Error("试听音频暂未就绪");
    audioUrl.value = assetUrl(url);
  });
}

async function archive(character) {
  try {
    await ElMessageBox.confirm(
      `归档“${character.name}”？归档后可随时开启「显示已归档」恢复，且已创建项目的角色版本会保留。`,
      "归档角色",
      { type: "warning" },
    );
    const cid = id(character);
    await act(() => charactersApi.archive(cid));
    if (selectedCharacterId.value === cid && !showArchived.value) {
      selectedCharacterId.value = "";
    }
  } catch (e) {
    if (e instanceof Error) error.value = e.message;
  }
}

async function restore(character) {
  await act(async () => {
    await charactersApi.restore(id(character));
    ElMessage.success(`已恢复角色「${character.name}」`);
  });
}

onMounted(load);
</script>
<style scoped>
.page {
  max-width: 1200px;
  margin: auto;
}
.heading,
.actions,
.row,
.toolbar,
.tag-group {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.heading {
  justify-content: space-between;
  margin-bottom: 20px;
}
h1 {
  margin: 0 0 8px;
  font-size: 28px;
}
h2 {
  font-size: 19px;
  margin: 0;
}
p {
  line-height: 1.6;
  color: #64748b;
}

/* ==================== 一级页面：紧凑人物卡片网格 ==================== */
.compact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(255px, 1fr));
  gap: 20px;
  margin-top: 16px;
}
.compact-card {
  border-radius: 14px;
  border: 1px solid #e2e8f0;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.compact-card:hover {
  transform: translateY(-3px);
}
.compact-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.person-index {
  font-size: 12px;
  font-weight: 700;
  color: #2563eb;
  background: #eff6ff;
  padding: 2px 10px;
  border-radius: 999px;
}
.front-cover-box {
  position: relative;
  width: 100%;
  height: 220px;
  border-radius: 10px;
  overflow: hidden;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  cursor: pointer;
}
.front-cover-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.front-cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #94a3b8;
  font-size: 13px;
}
.empty-avatar-icon {
  font-size: 36px;
  opacity: 0.55;
}
.view-count-badge {
  position: absolute;
  bottom: 8px;
  right: 8px;
  background: rgba(15, 23, 42, 0.72);
  color: #f8fafc;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
}
.compact-info {
  margin-top: 12px;
  cursor: pointer;
}
.compact-name {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 6px;
}
.compact-dna {
  margin: 0 0 4px;
  font-size: 13px;
  color: #475569;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 40px;
}
.compact-meta {
  margin: 0 0 8px;
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.voice-pill {
  display: inline-block;
  font-size: 11px;
  color: #475569;
  background: #f1f5f9;
  padding: 2px 8px;
  border-radius: 6px;
}
.compact-actions {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #f1f5f9;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.compact-actions .el-button {
  flex: 1;
  margin: 0;
}

/* ==================== 二级页面：人物详情视图 ==================== */
.detail-topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.detail-nav-left,
.detail-nav-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.breadcrumb-text {
  font-size: 15px;
  font-weight: 600;
  color: #334155;
}
.detail-card {
  border-radius: 14px;
  border: 1px solid #e2e8f0;
}
.detail-portrait {
  height: 210px !important;
}
.detail-description {
  margin-top: 16px;
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
}
.detail-description p {
  margin: 6px 0;
}

.archived-card {
  opacity: 0.78;
  background: #f8fafc;
}
.template-bar {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.template-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}
.template-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.template-tags .el-button {
  margin: 0;
}
.reference-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.reference-grid figure {
  margin: 0;
  text-align: center;
}
.view-caption {
  margin-top: 6px;
  color: #64748b;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex-wrap: wrap;
}
.portrait {
  width: 100%;
  height: 136px;
  object-fit: contain;
  background: #f1f5f9;
  border-radius: 8px;
}
.placeholder {
  display: grid;
  place-items: center;
  color: #64748b;
  font-size: 13px;
}
.muted {
  font-size: 13px;
}
.actions {
  margin-top: 20px;
}
.row > * {
  flex: 1;
}
.upload-preview {
  max-width: 100%;
  max-height: 160px;
  display: block;
  margin-top: 12px;
}
.audio {
  width: 100%;
  margin-top: 20px;
}
</style>
