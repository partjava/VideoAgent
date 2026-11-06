<template>
  <el-dialog
    :model-value="visible"
    :title="character ? `3D 人形与三视图空间检视台 · ${character.name}` : '3D 角色检视台'"
    width="min(1040px, 96vw)"
    @update:model-value="$emit('update:visible', $event)"
  >
    <div v-if="character" class="stage-layout">
      <!-- Left: 360° Interactive 3D Viewport + Spatial 3-View Ring -->
      <div class="viewport-column">
        <div class="viewport-toolbar">
          <div class="camera-presets">
            <span class="toolbar-label">机位：</span>
            <el-button size="small" :type="activePreset === 'closeup' ? 'primary' : 'default'" @click="applyCamera('closeup', 0, 5, 1.42)">平视特写</el-button>
            <el-button size="small" :type="activePreset === 'front' ? 'primary' : 'default'" @click="applyCamera('front', 0, 12, 1.0)">0° 正面</el-button>
            <el-button size="small" :type="activePreset === 'side45' ? 'primary' : 'default'" @click="applyCamera('side45', 45, 12, 1.0)">45° 侧面</el-button>
            <el-button size="small" :type="activePreset === 'side90' ? 'primary' : 'default'" @click="applyCamera('side90', 90, 12, 1.0)">90° 侧面</el-button>
            <el-button size="small" :type="activePreset === 'full' ? 'primary' : 'default'" @click="applyCamera('full', 15, 10, 0.84)">远景全身</el-button>
            <el-button size="small" :type="activePreset === 'high' ? 'primary' : 'default'" @click="applyCamera('high', 20, 38, 1.0)">俯拍</el-button>
            <el-button size="small" :type="activePreset === 'low' ? 'primary' : 'default'" @click="applyCamera('low', 20, -20, 1.05)">仰拍</el-button>
            <el-button size="small" :type="autoRotate ? 'success' : 'default'" @click="autoRotate = !autoRotate">
              {{ autoRotate ? '停止环绕' : '360° 自动环绕' }}
            </el-button>
          </div>
          <div class="pose-presets">
            <span class="toolbar-label">姿态：</span>
            <el-radio-group v-model="pose" size="small">
              <el-radio-button value="stand">标准站姿</el-radio-button>
              <el-radio-button value="apose">A-Pose</el-radio-button>
              <el-radio-button value="walk">走姿</el-radio-button>
              <el-radio-button value="akimbo">叉腰</el-radio-button>
              <el-radio-button value="drama">戏剧特写</el-radio-button>
            </el-radio-group>
          </div>
        </div>

        <div
          class="canvas-stage"
          @mousedown="startDrag"
          @mousemove="onDrag"
          @mouseup="stopDrag"
          @mouseleave="stopDrag"
          @wheel.prevent="onWheel"
        >
          <canvas ref="canvasRef" width="560" height="390" class="mannequin-canvas" />

          <!-- 3D Spatial Reference Projection Overlay -->
          <div v-if="showProjection && activeReferenceUrl" class="spatial-projection" :style="projectionStyle">
            <img :src="activeReferenceUrl" :alt="activeAngleLabel" />
            <span class="projection-badge">{{ activeAngleLabel }}贴图投影</span>
          </div>

          <div class="stage-hud">
            <span>水平角：{{ normalizedYaw }}° · 俯仰角：{{ Math.round(pitch) }}° · 缩放：{{ Math.round(zoom * 100) }}%</span>
            <el-checkbox v-model="showProjection" size="small">叠加定妆照空间贴图</el-checkbox>
          </div>
          <div class="drag-hint">按住鼠标左键左右/上下拖拽 360° 旋转（同步联动右侧 360° 视频与下方三视图） · 滚轮缩放</div>
        </div>

        <!-- 3D Spatial Ring Panel: Synchronized Highlight for 0° Front / 90° Side / Full Body -->
        <div class="spatial-ring-bar">
          <div
            v-for="card in spatialCards"
            :key="card.key"
            class="spatial-card"
            :class="{ active: activeViewKey === card.key }"
            @click="applyCamera(card.preset, card.yaw, 12, card.zoom)"
          >
            <img v-if="card.url" :src="card.url" :alt="card.title" />
            <div v-else class="spatial-card-empty">未生成</div>
            <div class="spatial-card-caption">
              <span>{{ card.title }}</span>
              <el-tag v-if="activeViewKey === card.key" size="small" type="primary" effect="dark">当前视角</el-tag>
            </div>
          </div>
        </div>
      </div>

      <!-- Right: AI 3D Generation & Drag-to-Rotate 360° Turntable Video -->
      <div class="control-column">
        <div class="panel-block">
          <h4>1. 一键生成三视图 + 360° 旋转视角视频</h4>
          <p class="panel-desc">
            AI 先按标准正交视角依次生成<strong>正面、90° 侧面、全身</strong>三张图（自动锁定同一张脸和服装），随后<strong>顺便自动生成 360° 原地旋转视角视频</strong>。
          </p>
          <div class="style-row">
            <span class="field-label">渲染画风：</span>
            <el-radio-group v-model="selectedStyle" size="small">
              <el-radio-button value="3d">3D 建模渲染</el-radio-button>
              <el-radio-button value="realistic">真人写实</el-radio-button>
              <el-radio-button value="anime">动漫赛璐珞</el-radio-button>
            </el-radio-group>
          </div>
          <div class="sync-video-switch">
            <el-checkbox v-model="autoGenerateTurntable" size="small">
              顺便生成 360° 旋转视角视频（不勾选则仅生成 3 张图片，省视频额度）
            </el-checkbox>
          </div>
          <div class="gen-btn-row">
            <el-button
              type="primary"
              :loading="busyAction === 'all'"
              :disabled="!!busyAction"
              @click="$emit('generate-portrait', { view: 'all', style: selectedStyle, include_turntable: autoGenerateTurntable })"
            >
              {{ autoGenerateTurntable ? '一键生成三视图 + 360° 视角视频' : '一键生成三视图（仅生图）' }}
            </el-button>
          </div>
          <div class="gen-sub-row">
            <el-button
              size="small"
              :loading="busyAction === 'front'"
              :disabled="!!busyAction"
              @click="$emit('generate-portrait', { view: 'front', style: selectedStyle, include_turntable: false })"
            >重绘正面</el-button>
            <el-button
              size="small"
              :loading="busyAction === 'profile'"
              :disabled="!!busyAction"
              @click="$emit('generate-portrait', { view: 'profile', style: selectedStyle, include_turntable: false })"
            >重绘侧面</el-button>
            <el-button
              size="small"
              :loading="busyAction === 'whole'"
              :disabled="!!busyAction"
              @click="$emit('generate-portrait', { view: 'whole', style: selectedStyle, include_turntable: false })"
            >重绘全身</el-button>
          </div>
        </div>

        <div class="panel-block">
          <div class="turntable-header">
            <h4>2. 360° 旋转视角视频（支持鼠标拖拽旋转查看）</h4>
            <el-tag v-if="turntableUrl" size="small" type="success">可拖拽旋转</el-tag>
          </div>
          <p class="panel-desc">
            在下方视频画面或左侧 3D 人形上<strong>按住鼠标左右拖拽</strong>，即可实时旋转查看人物 0°~360° 任意角度！
          </p>
          <div
            v-if="turntableUrl"
            class="turntable-player"
            @mousedown="startVideoDrag"
            @mousemove="onVideoDrag"
            @mouseup="stopVideoDrag"
            @mouseleave="stopVideoDrag"
          >
            <video
              ref="videoRef"
              :src="turntableUrl"
              loop
              muted
              playsinline
              class="turntable-video"
              @loadedmetadata="syncVideoToYaw"
            />
            <div class="video-drag-overlay">
              <span>当前旋转角：{{ normalizedYaw }}°（左右拖拽画面旋转人物）</span>
              <el-button size="small" link type="primary" @click.stop="toggleVideoPlay">
                {{ videoPlaying ? '暂停自动旋转' : '自动旋转播放' }}
              </el-button>
            </div>
          </div>
          <div v-else class="turntable-empty">
            尚未生成 360° 旋转视角视频
          </div>
          <el-button
            type="success"
            plain
            style="width: 100%; margin-top: 10px"
            :loading="busyAction === 'turntable'"
            :disabled="!!busyAction || !hasAnyReference"
            @click="$emit('generate-turntable')"
          >
            {{ turntableUrl ? '单独重新生成 360° 旋转视频' : '单独生成 360° 旋转视角视频' }}
          </el-button>
        </div>

        <div class="panel-block meta-summary">
          <h4>角色特征锚点</h4>
          <p><strong>外貌：</strong>{{ character.face_dna || '未填写' }}</p>
          <p><strong>服装：</strong>{{ character.signature_clothing || '未填写' }}</p>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps({
  visible: { type: Boolean, default: false },
  character: { type: Object, default: null },
  busyAction: { type: String, default: "" },
  assetUrl: { type: Function, required: true },
});

defineEmits(["update:visible", "generate-portrait", "generate-turntable"]);

const canvasRef = ref(null);
const videoRef = ref(null);
const yaw = ref(20);
const pitch = ref(12);
const zoom = ref(1.0);
const activePreset = ref("front");
const autoRotate = ref(false);
const pose = ref("stand");
const showProjection = ref(true);
const selectedStyle = ref("3d");
const autoGenerateTurntable = ref(false);
const dragging = ref(false);
const videoDragging = ref(false);
const videoPlaying = ref(false);
const lastMouse = ref({ x: 0, y: 0 });
let rafId = null;

const normalizedYaw = computed(() => {
  const deg = Math.round(yaw.value) % 360;
  return deg < 0 ? deg + 360 : deg;
});

function applyCamera(presetName, targetYaw, targetPitch = 12, targetZoom = 1.0) {
  autoRotate.value = false;
  activePreset.value = presetName;
  yaw.value = targetYaw;
  pitch.value = targetPitch;
  zoom.value = targetZoom;
  if (presetName === "closeup") {
    pose.value = "drama";
  }
  syncVideoToYaw();
}

const storedRef = (view) =>
  props.character?.reference_images?.[view] ||
  (view === "front" ? props.character?.reference_path : null);

const hasAnyReference = computed(() =>
  Boolean(storedRef("front") || storedRef("profile") || storedRef("whole")),
);

const turntableUrl = computed(() =>
  props.character?.turntable_video
    ? props.assetUrl(props.character.turntable_video)
    : "",
);

const spatialCards = computed(() => [
  {
    key: "front",
    title: "0° 正面定妆照",
    url: storedRef("front") ? props.assetUrl(storedRef("front")) : "",
    preset: "front",
    yaw: 0,
    zoom: 1.0,
  },
  {
    key: "profile",
    title: "90° 侧面定妆照",
    url: storedRef("profile") ? props.assetUrl(storedRef("profile")) : "",
    preset: "side90",
    yaw: 90,
    zoom: 1.0,
  },
  {
    key: "whole",
    title: "全身定妆照",
    url: storedRef("whole") ? props.assetUrl(storedRef("whole")) : "",
    preset: "full",
    yaw: 180,
    zoom: 0.85,
  },
]);

const activeViewKey = computed(() => {
  const deg = normalizedYaw.value;
  if ((deg >= 50 && deg <= 130) || (deg >= 230 && deg <= 310)) {
    return "profile";
  }
  if (deg > 130 && deg < 230) {
    return "whole";
  }
  return "front";
});

const activeReferenceUrl = computed(() => {
  const raw =
    storedRef(activeViewKey.value) ||
    storedRef("front") ||
    storedRef("whole") ||
    storedRef("profile");
  return raw ? props.assetUrl(raw) : "";
});

const activeAngleLabel = computed(() => {
  return { front: "0° 正面", profile: "90° 侧面", whole: "全身" }[activeViewKey.value] || "角色";
});

const projectionStyle = computed(() => {
  const rad = (yaw.value * Math.PI) / 180;
  const scaleX = Math.max(0.58, Math.abs(Math.cos(rad)) * 0.42 + 0.58);
  return {
    transform: `scale(${zoom.value}) perspective(600px) rotateY(${Math.sin(rad) * 18}deg) scaleX(${scaleX.toFixed(3)})`,
  };
});

function syncVideoToYaw() {
  const v = videoRef.value;
  if (!v || !Number.isFinite(v.duration) || v.duration <= 0) return;
  if (!videoPlaying.value) {
    v.pause();
    v.currentTime = ((normalizedYaw.value % 360) / 360) * v.duration;
  }
}

function toggleVideoPlay() {
  const v = videoRef.value;
  if (!v) return;
  if (v.paused) {
    v.play();
    videoPlaying.value = true;
  } else {
    v.pause();
    videoPlaying.value = false;
  }
}

function startDrag(e) {
  dragging.value = true;
  autoRotate.value = false;
  videoPlaying.value = false;
  lastMouse.value = { x: e.clientX, y: e.clientY };
}

function onDrag(e) {
  if (!dragging.value) return;
  const dx = e.clientX - lastMouse.value.x;
  const dy = e.clientY - lastMouse.value.y;
  yaw.value += dx * 0.85;
  pitch.value = Math.max(-28, Math.min(45, pitch.value + dy * 0.4));
  lastMouse.value = { x: e.clientX, y: e.clientY };
  syncVideoToYaw();
}

function stopDrag() {
  dragging.value = false;
}

function startVideoDrag(e) {
  videoDragging.value = true;
  autoRotate.value = false;
  videoPlaying.value = false;
  lastMouse.value = { x: e.clientX, y: e.clientY };
}

function onVideoDrag(e) {
  if (!videoDragging.value) return;
  const dx = e.clientX - lastMouse.value.x;
  yaw.value += dx * 1.1;
  lastMouse.value = { x: e.clientX, y: e.clientY };
  syncVideoToYaw();
}

function stopVideoDrag() {
  videoDragging.value = false;
}

function onWheel(e) {
  const delta = e.deltaY > 0 ? -0.08 : 0.08;
  zoom.value = Math.max(0.65, Math.min(1.65, Number((zoom.value + delta).toFixed(2))));
}

function projectPoint([x, y, z], yawRad, pitchRad, scale, cx, cy) {
  const cosY = Math.cos(yawRad);
  const sinY = Math.sin(yawRad);
  const rx = x * cosY + z * sinY;
  const rz = -x * sinY + z * cosY;
  const cosP = Math.cos(pitchRad);
  const sinP = Math.sin(pitchRad);
  const ry = y * cosP - rz * sinP;
  const rz2 = y * sinP + rz * cosP;
  const fov = 420;
  const dist = 360 + rz2;
  const pScale = (fov / Math.max(120, dist)) * scale;
  return {
    x: cx + rx * pScale,
    y: cy - ry * pScale,
    z: rz2,
    s: pScale,
  };
}

function drawCapsule(ctx, p1, p2, r1, r2, baseColor, lightFactor) {
  const grad = ctx.createLinearGradient(p1.x - r1, p1.y, p2.x + r2, p2.y);
  grad.addColorStop(0, baseColor.light);
  grad.addColorStop(0.55, baseColor.mid);
  grad.addColorStop(1, baseColor.dark);
  ctx.strokeStyle = grad;
  ctx.lineCap = "round";
  ctx.lineWidth = Math.max(2, (r1 + r2) * 0.5);
  ctx.beginPath();
  ctx.moveTo(p1.x, p1.y);
  ctx.lineTo(p2.x, p2.y);
  ctx.stroke();

  ctx.fillStyle = lightFactor > 0 ? baseColor.light : baseColor.mid;
  ctx.beginPath();
  ctx.arc(p1.x, p1.y, Math.max(2, r1 * 0.48), 0, Math.PI * 2);
  ctx.fill();
}

function renderScene() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const bg = ctx.createRadialGradient(w / 2, h * 0.42, 30, w / 2, h * 0.5, w * 0.7);
  bg.addColorStop(0, "#1e293b");
  bg.addColorStop(1, "#0f172a");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  const yawRad = (yaw.value * Math.PI) / 180;
  const pitchRad = (pitch.value * Math.PI) / 180;
  const cx = w / 2;
  const cy = pose.value === "drama" ? h * 0.72 : h * 0.54;
  const sc = (pose.value === "drama" ? zoom.value * 1.48 : zoom.value * 1.15);

  const floorY = -112;
  for (const radius of [55, 95, 130]) {
    ctx.beginPath();
    for (let deg = 0; deg <= 360; deg += 6) {
      const a = (deg * Math.PI) / 180;
      const pt = projectPoint([Math.sin(a) * radius, floorY, Math.cos(a) * radius], yawRad, pitchRad, sc, cx, cy);
      if (deg === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
    ctx.strokeStyle = radius === 95 ? "rgba(56, 189, 248, 0.45)" : "rgba(148, 163, 184, 0.2)";
    ctx.lineWidth = radius === 95 ? 2 : 1;
    ctx.stroke();
  }

  const markers = [
    { deg: 0, label: "0° 正面" },
    { deg: 90, label: "90° 侧面" },
    { deg: 180, label: "180° 背面" },
    { deg: 270, label: "270° 侧面" },
  ];
  for (const m of markers) {
    const a = (m.deg * Math.PI) / 180;
    const pIn = projectPoint([Math.sin(a) * 40, floorY, Math.cos(a) * 40], yawRad, pitchRad, sc, cx, cy);
    const pOut = projectPoint([Math.sin(a) * 142, floorY, Math.cos(a) * 142], yawRad, pitchRad, sc, cx, cy);
    ctx.strokeStyle = m.deg === 0 ? "rgba(56, 189, 248, 0.65)" : "rgba(148, 163, 184, 0.25)";
    ctx.lineWidth = m.deg === 0 ? 2 : 1;
    ctx.beginPath();
    ctx.moveTo(pIn.x, pIn.y);
    ctx.lineTo(pOut.x, pOut.y);
    ctx.stroke();

    ctx.fillStyle = m.deg === 0 ? "#38bdf8" : "#94a3b8";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(m.label, pOut.x, pOut.y + 4);
  }

  const isApose = pose.value === "apose";
  const isAkimbo = pose.value === "akimbo";
  const isDrama = pose.value === "drama";
  const walkSwing = pose.value === "walk" ? 18 : 0;

  const lElbowPos = isAkimbo
    ? [-34, 32, -4]
    : isDrama
      ? [-24, 34, 14]
      : [-(isApose ? 36 : 22), 30, -walkSwing * 0.5];
  const rElbowPos = isAkimbo
    ? [34, 32, -4]
    : isDrama
      ? [22, 42, 18]
      : [isApose ? 36 : 22, 30, walkSwing * 0.5];
  const lWristPos = isAkimbo
    ? [-18, 14, 2]
    : isDrama
      ? [-14, 52, 20]
      : [-(isApose ? 46 : 25), 2, -walkSwing];
  const rWristPos = isAkimbo
    ? [18, 14, 2]
    : isDrama
      ? [8, 68, 22]
      : [isApose ? 46 : 25, 2, walkSwing];

  const joints = {
    headTop: [0, 96, 0],
    headCenter: [0, 82, 0],
    nose: [0, 81, 13],
    neck: [0, 66, 0],
    chest: [0, 42, 0],
    waist: [0, 14, 0],
    pelvis: [0, -6, 0],
    lShoulder: [-22, 58, 0],
    rShoulder: [22, 58, 0],
    lElbow: lElbowPos,
    rElbow: rElbowPos,
    lWrist: lWristPos,
    rWrist: rWristPos,
    lHip: [-13, -8, 0],
    rHip: [13, -8, 0],
    lKnee: [-14, -56, walkSwing],
    rKnee: [14, -56, -walkSwing],
    lAnkle: [-14, -106, walkSwing * 1.2],
    rAnkle: [14, -106, -walkSwing * 1.2],
    lToe: [-14, -110, walkSwing * 1.2 + 12],
    rToe: [14, -110, -walkSwing * 1.2 + 12],
  };

  const proj = {};
  for (const [k, v] of Object.entries(joints)) {
    proj[k] = projectPoint(v, yawRad, pitchRad, sc, cx, cy);
  }

  const palette = {
    light: "#93c5fd",
    mid: "#3b82f6",
    dark: "#1d4ed8",
  };
  const torsoPalette = {
    light: "#e2e8f0",
    mid: "#94a3b8",
    dark: "#475569",
  };

  const segments = [
    { a: "lHip", b: "lKnee", r1: 15, r2: 12, col: palette },
    { a: "lKnee", b: "lAnkle", r1: 12, r2: 9, col: palette },
    { a: "lAnkle", b: "lToe", r1: 9, r2: 7, col: torsoPalette },
    { a: "rHip", b: "rKnee", r1: 15, r2: 12, col: palette },
    { a: "rKnee", b: "rAnkle", r1: 12, r2: 9, col: palette },
    { a: "rAnkle", b: "rToe", r1: 9, r2: 7, col: torsoPalette },
    { a: "pelvis", b: "waist", r1: 24, r2: 20, col: torsoPalette },
    { a: "waist", b: "chest", r1: 21, r2: 28, col: torsoPalette },
    { a: "chest", b: "neck", r1: 18, r2: 11, col: torsoPalette },
    { a: "lShoulder", b: "rShoulder", r1: 14, r2: 14, col: torsoPalette },
    { a: "lShoulder", b: "lElbow", r1: 12, r2: 10, col: palette },
    { a: "lElbow", b: "lWrist", r1: 10, r2: 8, col: palette },
    { a: "rShoulder", b: "rElbow", r1: 12, r2: 10, col: palette },
    { a: "rElbow", b: "rWrist", r1: 10, r2: 8, col: palette },
  ];

  segments.sort((s1, s2) => {
    const z1 = (proj[s1.a].z + proj[s1.b].z) * 0.5;
    const z2 = (proj[s2.a].z + proj[s2.b].z) * 0.5;
    return z2 - z1;
  });

  for (const seg of segments) {
    const p1 = proj[seg.a];
    const p2 = proj[seg.b];
    drawCapsule(ctx, p1, p2, seg.r1 * sc, seg.r2 * sc, seg.col, -p1.z);
  }

  const hc = proj.headCenter;
  const headRadius = Math.max(10, 15.5 * sc);
  const headGrad = ctx.createRadialGradient(
    hc.x - headRadius * 0.3,
    hc.y - headRadius * 0.3,
    headRadius * 0.15,
    hc.x,
    hc.y,
    headRadius,
  );
  headGrad.addColorStop(0, "#f8fafc");
  headGrad.addColorStop(0.65, "#93c5fd");
  headGrad.addColorStop(1, "#1e3a8a");
  ctx.fillStyle = headGrad;
  ctx.beginPath();
  ctx.arc(hc.x, hc.y, headRadius, 0, Math.PI * 2);
  ctx.fill();

  const nosePt = proj.nose;
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(hc.x, hc.y);
  ctx.lineTo(nosePt.x, nosePt.y);
  ctx.stroke();
}

function tick() {
  if (props.visible) {
    if (autoRotate.value && !dragging.value && !videoDragging.value) {
      yaw.value = (yaw.value + 0.5) % 360;
      syncVideoToYaw();
    }
    renderScene();
  }
  rafId = requestAnimationFrame(tick);
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      setTimeout(() => {
        renderScene();
        syncVideoToYaw();
      }, 50);
    }
  },
);

onMounted(() => {
  rafId = requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId);
});
</script>

<style scoped>
.stage-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(300px, 1fr);
  gap: 20px;
}
.viewport-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.camera-presets,
.pose-presets {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.toolbar-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}
.camera-presets .el-button {
  margin: 0;
}
.canvas-stage {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #1e293b;
  background: #0f172a;
  cursor: grab;
  user-select: none;
}
.canvas-stage:active {
  cursor: grabbing;
}
.mannequin-canvas {
  width: 100%;
  height: 390px;
  display: block;
}
.spatial-projection {
  position: absolute;
  top: 18px;
  right: 16px;
  width: 145px;
  height: 200px;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid rgba(56, 189, 248, 0.8);
  background: rgba(15, 23, 42, 0.84);
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.45);
  pointer-events: none;
  transition: transform 0.08s ease-out;
}
.spatial-projection img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.projection-badge {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: rgba(15, 23, 42, 0.88);
  color: #38bdf8;
  font-size: 11px;
  text-align: center;
  padding: 3px 6px;
}
.stage-hud {
  position: absolute;
  bottom: 26px;
  left: 12px;
  right: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #cbd5e1;
  font-size: 12px;
  background: rgba(15, 23, 42, 0.75);
  padding: 4px 10px;
  border-radius: 6px;
}
.stage-hud :deep(.el-checkbox__label) {
  color: #e2e8f0;
}
.drag-hint {
  position: absolute;
  bottom: 5px;
  left: 0;
  right: 0;
  text-align: center;
  color: #64748b;
  font-size: 11px;
}
.spatial-ring-bar {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.spatial-card {
  border: 2px solid #e2e8f0;
  border-radius: 10px;
  padding: 6px;
  background: #f8fafc;
  cursor: pointer;
  transition: all 0.18s ease;
}
.spatial-card.active {
  border-color: #2563eb;
  background: #eff6ff;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.16);
  transform: translateY(-2px);
}
.spatial-card img,
.spatial-card-empty {
  width: 100%;
  height: 92px;
  object-fit: contain;
  background: #e2e8f0;
  border-radius: 6px;
  display: grid;
  place-items: center;
  color: #64748b;
  font-size: 12px;
}
.spatial-card-caption {
  margin-top: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #334155;
  font-weight: 500;
}
.control-column {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.panel-block {
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
}
.panel-block h4 {
  margin: 0 0 6px;
  font-size: 15px;
  color: #0f172a;
}
.turntable-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.turntable-header h4 {
  margin: 0;
}
.panel-desc {
  margin: 0 0 10px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
}
.style-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.sync-video-switch {
  margin-bottom: 10px;
}
.field-label {
  font-size: 12px;
  color: #475569;
}
.gen-btn-row .el-button {
  width: 100%;
}
.gen-sub-row {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.gen-sub-row .el-button {
  flex: 1;
  margin: 0;
}
.turntable-player {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  background: #0f172a;
  cursor: ew-resize;
  user-select: none;
}
.turntable-video {
  width: 100%;
  max-height: 220px;
  display: block;
  pointer-events: none;
}
.video-drag-overlay {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  background: rgba(15, 23, 42, 0.88);
  color: #e2e8f0;
  font-size: 12px;
}
.turntable-empty {
  height: 90px;
  display: grid;
  place-items: center;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: #94a3b8;
  font-size: 13px;
}
.meta-summary p {
  margin: 4px 0;
  font-size: 12px;
  color: #475569;
}
@media (max-width: 820px) {
  .stage-layout {
    grid-template-columns: 1fr;
  }
}
</style>
