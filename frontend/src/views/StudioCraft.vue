<template>
  <BasicLayout>
    <div class="craft-page">
      <div class="craft-hero">
        <div>
          <span class="craft-badge">SHORT-DRAMA STUDIO · AI 短剧全流程导演工坊</span>
          <h1>AI 短剧全流程导演与审稿工坊</h1>
          <p>
            由你配置的语言模型与生图模型实时驱动：长文/小说 AI 原创拆剧（中文/英文双模式）、AI 剧本五维审稿与深度重写、AI 场景 3×3 九宫格与道具四视图生成及直接出图、视频动作链 AI 升级与安全清洗。
          </p>
        </div>
      </div>

      <el-alert
        v-if="pageError"
        :title="pageError"
        type="error"
        show-icon
        closable
        @close="pageError = ''"
        style="margin-bottom: 16px"
      />

      <el-tabs v-model="activeTab" type="border-card" class="craft-tabs">
        <!-- ==================== TAB 1: 小说/长文 AI 拆剧 ==================== -->
        <el-tab-pane label="📖 小说/创意 AI 拆剧（含出海双模式）" name="novel">
          <div class="tab-header-bar">
            <div>
              <h2>上游 IP / 小说 / 故事创意实时拆解</h2>
              <p class="muted">
                由语言模型实时分析你的原文并原创输出：可拍性五维评估、人物设定卡（支持一键入库角色资产）、场景九宫格与道具四视图、分集付费卡点大纲（支持一键建连载）、首集四挡剧本与跨集四本连续性台账。
              </p>
            </div>
          </div>

          <el-form label-position="top" class="craft-form">
            <div class="form-row">
              <el-form-item label="剧集名称（留空则由 AI 自动拟定）" class="flex-2">
                <el-input v-model="novelForm.title" placeholder="输入剧名，或留空由 AI 根据内容拟定" />
              </el-form-item>
              <el-form-item label="剧本市场模式" class="flex-1">
                <el-radio-group v-model="novelForm.market_mode">
                  <el-radio-button value="cn">🇨🇳 国内中文短剧</el-radio-button>
                  <el-radio-button value="en">🌍 出海英文短剧</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="视觉风格" class="flex-1">
                <el-radio-group v-model="novelForm.style">
                  <el-radio-button value="短剧">真人短剧</el-radio-button>
                  <el-radio-button value="漫剧">精品漫剧</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="规划集数" class="flex-1">
                <el-select v-model="novelForm.total_episodes">
                  <el-option :value="3" label="3 集精悍版" />
                  <el-option :value="5" label="5 集标准版" />
                  <el-option :value="10" label="10 集连载版" />
                </el-select>
              </el-form-item>
              <el-form-item label="单集时长范围" class="flex-1">
                <el-select v-model="craftRangePreset" @change="applyCraftRange">
                  <el-option value="30-60" label="30–60 秒（快节奏）" />
                  <el-option value="45-90" label="45–90 秒（标准短剧）" />
                  <el-option value="60-120" label="60–120 秒（完整冲突）" />
                </el-select>
              </el-form-item>
            </div>

            <el-form-item label="输入你的小说正文、故事梗概或一句短剧创意">
              <el-input
                v-model="novelForm.novel_text"
                type="textarea"
                :rows="7"
                placeholder="请粘贴小说章节、长故事大纲或核心短剧创意，AI 将严格基于你的文本实时拆解生成全部制作资产..."
              />
            </el-form-item>

            <div class="action-bar">
              <el-button
                type="primary"
                size="large"
                :loading="decomposing"
                :disabled="!novelForm.novel_text.trim()"
                @click="handleDecomposeNovel"
              >
                {{ decomposing ? "🤖 AI 正在深度拆解并创作剧本与资产..." : "🚀 调用 AI 实时拆解并生成全套开发资产" }}
              </el-button>
              <el-button
                v-if="novelResult"
                size="large"
                plain
                @click="clearCurrentDecomposition"
              >
                清空当前结果（保留历史草稿）
              </el-button>
            </div>
          </el-form>

          <!-- 最近拆剧草稿（切换页面 / 刷新浏览器自动保留） -->
          <div v-if="recentDecompositions.length" class="draft-history-bar">
            <span class="draft-history-label">💾 自动保存的拆剧记录（切页不丢失）：</span>
            <button
              v-for="draft in recentDecompositions"
              :key="draft.id"
              type="button"
              class="draft-chip"
              :class="{ active: activeDraftId === draft.id }"
              @click="restoreDraft(draft.id)"
            >
              {{ draft.title || "未命名拆剧" }} · {{ draft.episodes_count || 5 }}集
            </button>
          </div>

          <!-- AI 拆剧结果展示 -->
          <div v-if="novelResult" class="result-container">
            <!-- 下一步直通拍片引导栏 -->
            <el-card shadow="never" class="sub-card next-step-banner">
              <div class="card-title-row" style="margin-bottom: 8px">
                <div>
                  <h3>🎬 已生成可拍全案：《{{ novelResult.title }}》—— 下一步去拍视频</h3>
                  <p class="muted">
                    拆剧结果已自动保存到本机草稿，切换去「角色资产」或「创建视频」再回来也不会消失。你可以直接用第 1 集拍单集视频，或一键建连载追剧。
                  </p>
                </div>
                <div class="btn-group">
                  <el-button
                    type="success"
                    plain
                    :loading="importingChars"
                    @click="importCharactersToLibrary"
                  >
                    {{ importedCharacters.length ? `✅ 已入库 ${importedCharacters.length} 位角色（再入库）` : `✨ 1. 先入库角色 (${novelResult.characters?.length || 0}人)` }}
                  </el-button>
                  <el-button
                    v-if="importedCharacters.length"
                    plain
                    @click="router.push('/characters')"
                  >
                    去角色库看定妆照 →
                  </el-button>
                  <el-button
                    type="primary"
                    @click="sendFirstEpisodeToVideoCreate()"
                  >
                    🚀 2A. 用第 1 集直接生成视频 →
                  </el-button>
                  <el-button
                    type="warning"
                    plain
                    :loading="creatingSeries"
                    @click="createSeriesFromDecomposition"
                  >
                    📺 2B. 创建多集连载项目 →
                  </el-button>
                </div>
              </div>
            </el-card>

            <!-- 1. 可拍性诊断 -->
            <el-card shadow="never" class="sub-card">
              <div class="card-title-row">
                <h3>① AI 可拍性五维评估 · {{ novelResult.title }}</h3>
                <el-tag type="success" size="large">
                  {{ novelResult.shootability_report?.overall_feasibility }}
                </el-tag>
              </div>
              <div class="metric-grid">
                <div
                  v-for="m in novelResult.shootability_report?.metrics || []"
                  :key="m.name"
                  class="metric-box"
                >
                  <div class="metric-top">
                    <span>{{ m.name }}</span>
                    <strong>{{ m.score }} 分</strong>
                  </div>
                  <p>{{ m.comment }}</p>
                </div>
              </div>
            </el-card>

            <!-- 2. 提取的角色资产卡（一键入库） -->
            <el-card shadow="never" class="sub-card">
              <div class="card-title-row">
                <div>
                  <h3>② AI 生成的角色资产卡（含十项五官锚点、完整鞋履与匹配音色）</h3>
                  <p class="muted">确认无误后可一键批量入库到「角色资产库」，直接生成三视图与四格定妆板。</p>
                </div>
                <el-button
                  type="success"
                  :loading="importingChars"
                  @click="importCharactersToLibrary"
                >
                  ✨ 一键全部入库到角色资产库（{{ novelResult.characters?.length || 0 }} 人）
                </el-button>
              </div>
              <div class="char-extract-grid">
                <div
                  v-for="(c, idx) in novelResult.characters || []"
                  :key="idx"
                  class="char-extract-item"
                >
                  <div class="char-extract-head">
                    <strong>{{ c.name }}</strong>
                    <el-tag size="small">{{ c.role }}</el-tag>
                  </div>
                  <p><strong>外貌锚点：</strong>{{ c.face_dna }}</p>
                  <p><strong>标志服装（含鞋）：</strong>{{ c.signature_clothing }}</p>
                  <span class="voice-chip">🎙 {{ c.tts_voice }} ({{ c.rate }} / {{ c.pitch }})</span>
                </div>
              </div>
            </el-card>

            <!-- 3. 分集付费卡点大纲（一键建连载） -->
            <el-card shadow="never" class="sub-card">
              <div class="card-title-row">
                <div>
                  <h3>③ 分集付费卡点大纲（15秒换挡结构）</h3>
                  <p class="muted">可一键创建为连载短剧项目，并自动带入本剧的「跨集四本连续性台账」。</p>
                </div>
                <el-button
                  type="primary"
                  :loading="creatingSeries"
                  @click="createSeriesFromDecomposition"
                >
                  🎬 一键创建为连载短剧项目 →
                </el-button>
              </div>
              <div class="episode-extract-list">
                <div
                  v-for="ep in novelResult.episodes_outline || []"
                  :key="ep.episode_num"
                  class="episode-extract-row"
                  :class="{ 'paywall-ep': ep.paywall_card }"
                >
                  <div class="ep-head">
                    <strong>{{ ep.title }}</strong>
                    <el-tag v-if="ep.paywall_card" type="danger" effect="dark" size="small">
                      💰 付费卡点集 · {{ ep.paywall_reason }}
                    </el-tag>
                  </div>
                  <p><strong>0-3s 开场钩子：</strong>{{ ep.opening_hook }}</p>
                  <p><strong>15-85s 核心交锋与当集结算：</strong>{{ ep.core_event }}</p>
                  <p><strong>85-110s 断章钩子：</strong>{{ ep.ending_hook }}</p>
                </div>
              </div>
            </el-card>

            <!-- 4. 首集剧本 & 场景道具提示词 -->
            <div class="two-col-grid">
              <el-card shadow="never" class="sub-card">
                <div class="card-title-row">
                  <h3>④ AI 创作第 1 集商业剧本（{{ novelResult.market_mode === 'en' ? '出海英文格式' : '国内中文格式' }}）</h3>
                  <div class="btn-group">
                    <el-button size="small" @click="copyText(novelResult.first_episode_script, '首集剧本')">
                      复制剧本
                    </el-button>
                    <el-button size="small" type="primary" plain @click="sendToReviewer(novelResult.first_episode_script)">
                      送入 AI 审稿校验 →
                    </el-button>
                    <el-button size="small" type="success" @click="sendFirstEpisodeToVideoCreate(novelResult.first_episode_script)">
                      🚀 去生成视频 →
                    </el-button>
                  </div>
                </div>
                <pre class="code-box">{{ novelResult.first_episode_script }}</pre>
              </el-card>

              <el-card shadow="never" class="sub-card">
                <div class="card-title-row">
                  <h3>⑤ AI 设计的场景 3×3 九宫格 & 核心道具四视图提示词</h3>
                </div>
                <div
                  v-for="(sc, sIdx) in novelResult.scenes_sheet || []"
                  :key="'s' + sIdx"
                  class="asset-prompt-block"
                >
                  <div class="asset-prompt-head">
                    <strong>🏛️ 场景九宫格：{{ sc.scene_name }}</strong>
                    <div>
                      <el-button size="small" link type="primary" @click="copyText(sc.prompt, sc.scene_name)">
                        复制 Prompt
                      </el-button>
                      <el-button
                        size="small"
                        type="success"
                        plain
                        :loading="renderingAssetKey === `novel_scene_${sIdx}`"
                        @click="renderNovelAssetImage(sc, 'scene_grids', `novel_scene_${sIdx}`)"
                      >
                        🎨 AI 直接生图
                      </el-button>
                    </div>
                  </div>
                  <pre class="code-box compact">{{ sc.prompt }}</pre>
                  <img
                    v-if="renderedUrls[`novel_scene_${sIdx}`]"
                    :src="mediaUrl(renderedUrls[`novel_scene_${sIdx}`])"
                    class="rendered-asset-img"
                    :alt="sc.scene_name"
                  />
                </div>
                <div
                  v-for="(pr, pIdx) in novelResult.props_sheet || []"
                  :key="'p' + pIdx"
                  class="asset-prompt-block"
                >
                  <div class="asset-prompt-head">
                    <strong>🗡️ 关键道具四格：{{ pr.prop_name }}</strong>
                    <div>
                      <el-button size="small" link type="primary" @click="copyText(pr.prompt, pr.prop_name)">
                        复制 Prompt
                      </el-button>
                      <el-button
                        size="small"
                        type="success"
                        plain
                        :loading="renderingAssetKey === `novel_prop_${pIdx}`"
                        @click="renderNovelAssetImage(pr, 'prop_sheets', `novel_prop_${pIdx}`)"
                      >
                        🎨 AI 直接生图
                      </el-button>
                    </div>
                  </div>
                  <pre class="code-box compact">{{ pr.prompt }}</pre>
                  <img
                    v-if="renderedUrls[`novel_prop_${pIdx}`]"
                    :src="mediaUrl(renderedUrls[`novel_prop_${pIdx}`])"
                    class="rendered-asset-img"
                    :alt="pr.prop_name"
                  />
                </div>
              </el-card>
            </div>
          </div>
        </el-tab-pane>

        <!-- ==================== TAB 2: AI 剧本审稿与深度改稿 ==================== -->
        <el-tab-pane label="⚖️ AI 剧本审稿与深度改稿" name="reviewer">
          <div class="tab-header-bar">
            <div>
              <h2>AI 剧本实时审稿与去 AI 味深度重写</h2>
              <p class="muted">
                由语言模型逐行精读你的剧本，结合客观静态指标扫描，从「15秒换挡节奏、爽点结算、台词去AI味、空间可拍性、连续性」五维精准诊断，并支持调用 AI 一键深度重写润色。
              </p>
            </div>
          </div>

          <el-input
            v-model="reviewScriptInput"
            type="textarea"
            :rows="9"
            placeholder="请粘贴你需要审查或精修的原创剧本正文..."
            style="margin-bottom: 14px"
          />

          <div class="action-bar">
            <el-button
              type="primary"
              :loading="reviewing"
              :disabled="!reviewScriptInput.trim()"
              @click="handleReviewScript"
            >
              {{ reviewing ? "🤖 AI 审稿总监正在逐行诊断..." : "🔍 调用 AI 全面审稿诊断" }}
            </el-button>
            <el-button
              type="success"
              :loading="doctoring"
              :disabled="!reviewScriptInput.trim()"
              @click="handleDoctorScript"
            >
              {{ doctoring ? "🤖 AI 正在重写去AI味与动作锚点..." : "⚡ 调用 AI 深度重写精修剧本" }}
            </el-button>
            <el-button
              v-if="reviewScriptInput.trim()"
              type="warning"
              plain
              @click="sendFirstEpisodeToVideoCreate(reviewScriptInput)"
            >
              🚀 直接用当前剧本去生成视频 →
            </el-button>
          </div>

          <el-alert
            v-if="doctorResult?.changes_made?.length"
            title="⚡ AI 已完成剧本深度重写与动作可拍性升级！"
            type="success"
            show-icon
            :closable="false"
            style="margin-top: 16px"
          >
            <ul class="doctor-change-list">
              <li v-for="(chg, idx) in doctorResult.changes_made" :key="idx">{{ chg }}</li>
            </ul>
          </el-alert>

          <div v-if="reviewReport" class="result-container">
            <el-card shadow="never" class="sub-card">
              <div class="score-banner">
                <div class="score-main">
                  <span class="score-grade" :class="'grade-' + reviewReport.grade">{{ reviewReport.grade }} 级</span>
                  <div>
                    <h3>AI 审稿总评：{{ reviewReport.overall_score }} / 100</h3>
                    <p class="muted">
                      检出 {{ reviewReport.issue_count }} 处可优化项 · 实测动作行 {{ reviewReport.metrics?.action_line_count ?? 0 }} 条 · 最长连续纯对白 {{ reviewReport.metrics?.max_consecutive_dialogue ?? 0 }} 句
                    </p>
                  </div>
                </div>
                <div class="dim-scores">
                  <div v-for="(dim, key) in reviewReport.dimensions" :key="key" class="dim-pill">
                    <span>{{ dim.label }}</span>
                    <strong>{{ dim.score }}</strong>
                  </div>
                </div>
              </div>

              <div class="gear-row">
                <div
                  v-for="(g, gIdx) in reviewReport.four_gear_timeline || []"
                  :key="gIdx"
                  class="gear-item"
                  :class="g.status"
                >
                  <div class="gear-head">
                    <strong>{{ g.gear }}</strong>
                    <el-tag size="small" :type="g.status === 'pass' ? 'success' : 'warning'">
                      {{ g.status === 'pass' ? '达标' : '待强化' }}
                    </el-tag>
                  </div>
                  <p>{{ g.target }}</p>
                </div>
              </div>
            </el-card>

            <el-card shadow="never" class="sub-card">
              <h3>📋 检出的具体问题与改写建议（{{ reviewReport.issues?.length || 0 }} 项）</h3>
              <el-empty
                v-if="!reviewReport.issues?.length"
                description="本剧本在 15 秒换挡、动作锚点密度与台词自然度方面表现优秀。"
                :image-size="68"
              />
              <div v-else class="issue-cards">
                <div
                  v-for="(iss, idx) in reviewReport.issues"
                  :key="idx"
                  class="issue-card"
                  :class="'sev-' + iss.severity"
                >
                  <div class="issue-card-head">
                    <strong>{{ iss.rule_id }}</strong>
                    <el-tag size="small" :type="iss.severity === 'high' ? 'danger' : 'warning'">
                      {{ iss.category }} · {{ iss.severity === 'high' ? '高优先级' : '建议优化' }}
                    </el-tag>
                  </div>
                  <p class="issue-evidence"><strong>原文片段：</strong><code>{{ iss.evidence }}</code></p>
                  <p><strong>问题说明：</strong>{{ iss.diagnosis }}</p>
                  <p class="issue-rx"><strong>修改建议：</strong>{{ iss.prescription }}</p>
                </div>
              </div>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- ==================== TAB 3: 场景九宫格 / 道具四视图 / 视频动作升级 ==================== -->
        <el-tab-pane label="🏛️ 场景3×3九宫格 · 道具四视图 · 视频动作AI升级" name="assets">
          <div class="two-col-grid">
            <el-card shadow="never" class="sub-card">
              <h3>🏛️ AI 场景 3×3 九宫格设定图生成器（1图=9机位纯空镜）</h3>
              <p class="muted">
                输入场景名与简述，由 AI 美术总监为你原创空间东西南北布局、物理主光色温与全部 9 个标准机位空镜提示词，支持直接调用生图模型出图。
              </p>
              <el-form label-position="top">
                <el-form-item label="场景名称" required>
                  <el-input v-model="sceneForm.scene_name" placeholder="例如：雨夜城中村废弃修车厂 / 深宫冷月偏殿" />
                </el-form-item>
                <div class="form-row">
                  <el-form-item label="空间类型" class="flex-1">
                    <el-select v-model="sceneForm.space_type">
                      <el-option value="indoor" label="室内半封闭空间" />
                      <el-option value="outdoor_building" label="室外独立建筑" />
                      <el-option value="street_nature" label="自然场地与街巷" />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="年代与美术风格（可选，留空由AI定调）" class="flex-1">
                    <el-input v-model="sceneForm.era_and_style" placeholder="例如：现代悬疑写实 / 古风CG" />
                  </el-form-item>
                </div>
                <el-form-item label="空间陈设与氛围补充（可选）">
                  <el-input v-model="sceneForm.layout_and_materials" placeholder="可简单写关键物体或留空，AI 会自动推演完整结构" />
                </el-form-item>
                <el-button
                  type="primary"
                  :loading="generatingSceneGrid"
                  :disabled="!sceneForm.scene_name.trim()"
                  @click="handleGenerateSceneGrid"
                >
                  {{ generatingSceneGrid ? "🤖 AI 正在设计九宫格空间与光影..." : "✨ 调用 AI 设计场景 3×3 九宫格提示词" }}
                </el-button>
              </el-form>
              <div v-if="sceneGridResult" class="prompt-out-box">
                <div class="asset-prompt-head">
                  <strong>生成结果（{{ sceneGridResult.scene_name }}）</strong>
                  <div>
                    <el-button size="small" type="primary" link @click="copyText(sceneGridResult.prompt, '场景九宫格提示词')">
                      一键复制
                    </el-button>
                    <el-button
                      size="small"
                      type="success"
                      plain
                      :loading="renderingAssetKey === 'tab3_scene'"
                      @click="renderNovelAssetImage(sceneGridResult, 'scene_grids', 'tab3_scene')"
                    >
                      🎨 直接 AI 生成 16:9 九宫格图
                    </el-button>
                  </div>
                </div>
                <pre class="code-box">{{ sceneGridResult.prompt }}</pre>
                <img
                  v-if="renderedUrls.tab3_scene"
                  :src="mediaUrl(renderedUrls.tab3_scene)"
                  class="rendered-asset-img"
                  :alt="sceneGridResult.scene_name"
                />
              </div>
            </el-card>

            <div>
              <el-card shadow="never" class="sub-card" style="margin-bottom: 18px">
                <h3>🗡️ AI 关键道具 16:9 四格设定图生成器（零人手/零过曝发光）</h3>
                <el-form label-position="top">
                  <div class="form-row">
                    <el-form-item label="道具名称" class="flex-1" required>
                      <el-input v-model="propForm.prop_name" placeholder="例如：生锈的黄铜怀表 / 带血痕的旧病历" />
                    </el-form-item>
                    <el-form-item label="相对成人手掌尺寸（可选）" class="flex-1">
                      <el-input v-model="propForm.size_vs_palm" placeholder="留空由 AI 自动设定精确比例" />
                    </el-form-item>
                  </div>
                  <el-form-item label="材质与细节补充（可选）">
                    <el-input v-model="propForm.material_and_color" placeholder="AI 会自动转为哑光实心物理材质并补全微距细节" />
                  </el-form-item>
                  <el-button
                    type="primary"
                    :loading="generatingPropSheet"
                    :disabled="!propForm.prop_name.trim()"
                    @click="handleGeneratePropSheet"
                  >
                    {{ generatingPropSheet ? "🤖 AI 正在设计道具四视图..." : "✨ 调用 AI 设计道具四格提示词" }}
                  </el-button>
                </el-form>
                <div v-if="propSheetResult" class="prompt-out-box">
                  <div class="asset-prompt-head">
                    <strong>道具四格 Prompt</strong>
                    <div>
                      <el-button size="small" type="primary" link @click="copyText(propSheetResult.prompt, '道具四格提示词')">
                        一键复制
                      </el-button>
                      <el-button
                        size="small"
                        type="success"
                        plain
                        :loading="renderingAssetKey === 'tab3_prop'"
                        @click="renderNovelAssetImage(propSheetResult, 'prop_sheets', 'tab3_prop')"
                      >
                        🎨 直接 AI 生成 16:9 道具图
                      </el-button>
                    </div>
                  </div>
                  <pre class="code-box compact">{{ propSheetResult.prompt }}</pre>
                  <img
                    v-if="renderedUrls.tab3_prop"
                    :src="mediaUrl(renderedUrls.tab3_prop)"
                    class="rendered-asset-img"
                    :alt="propSheetResult.prop_name"
                  />
                </div>
              </el-card>

              <el-card shadow="never" class="sub-card">
                <h3>🎬 AI 视频提示词动作链扩写与安全清洗器</h3>
                <p class="muted">
                  输入任意简短镜头描述，由 AI 动作导演扩写为 MCSLA 因果物理动作链与运镜指令，并自动完成比喻物理化、单人镜去画外人名、去反光灯泡词与安全转译（≤1400 字）。
                </p>
                <div class="form-row" style="margin-bottom: 10px">
                  <el-input v-model="videoSanitizeForm.speaker" placeholder="在场主体名（可选）" class="flex-1" />
                  <el-input v-model="videoSanitizeForm.shot_type" placeholder="景别偏好（如：中近景 MCU）" class="flex-1" />
                  <el-input v-model="videoSanitizeForm.camera_motion" placeholder="运镜偏好（如：缓慢推镜）" class="flex-1" />
                </div>
                <el-input
                  v-model="videoSanitizeForm.raw_prompt"
                  type="textarea"
                  :rows="3"
                  placeholder="输入你的镜头动作想法，例如：女主在暴雨夜翻出旧档案，手抖得厉害，抬头冷冷望向门口..."
                  style="margin-bottom: 10px"
                />
                <el-button
                  type="success"
                  :loading="upgradingVideoPrompt"
                  :disabled="!videoSanitizeForm.raw_prompt.trim()"
                  @click="handleSanitizeVideoPrompt"
                >
                  {{ upgradingVideoPrompt ? "🤖 AI 动作导演正在扩写..." : "⚡ 调用 AI 扩写为 MCSLA 视频提示词" }}
                </el-button>
                <div v-if="videoSanitizeResult" class="prompt-out-box">
                  <div class="applied-tags">
                    <el-tag
                      v-for="r in videoSanitizeResult.applied_rules"
                      :key="r"
                      size="small"
                      type="warning"
                    >
                      {{ r }}
                    </el-tag>
                    <el-tag size="small" type="info">{{ videoSanitizeResult.char_count }} / 1400 字</el-tag>
                    <el-button size="small" type="primary" link @click="copyText(videoSanitizeResult.prompt, '视频提示词')">
                      复制
                    </el-button>
                  </div>
                  <pre class="code-box compact">{{ videoSanitizeResult.prompt }}</pre>
                </div>
              </el-card>
            </div>
          </div>
        </el-tab-pane>

        <!-- ==================== TAB 4: 提示词结构规范速查 ==================== -->
        <el-tab-pane label="📚 提示词结构规范速查" name="handbook">
          <div class="handbook-grid">
            <el-card v-for="tpl in handbookTemplates" :key="tpl.title" shadow="never" class="sub-card">
              <div class="card-title-row">
                <div>
                  <h3>{{ tpl.title }}</h3>
                  <p class="muted">{{ tpl.summary }}</p>
                </div>
                <el-button size="small" type="primary" plain @click="copyText(tpl.content, tpl.title)">
                  复制结构模板
                </el-button>
              </div>
              <pre class="code-box">{{ tpl.content }}</pre>
            </el-card>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </BasicLayout>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import BasicLayout from "../layouts/BasicLayout.vue";
import { craftApi } from "../api/craft";
import { charactersApi } from "../api/characters";
import { seriesApi } from "../api/series";

const CRAFT_STORAGE_KEY = "videoagent_craft_workspace_v1";
const PREFILL_VIDEO_KEY = "videoagent_prefill_task";

const router = useRouter();
const activeTab = ref("novel");
const pageError = ref("");

const mediaUrl = (path) =>
  path ? "/" + String(path).replace(/^backend\//, "").replace(/^\/+/, "") : "";

// ── Tab 1: 小说/创意 AI 拆剧 ──
const decomposing = ref(false);
const importingChars = ref(false);
const creatingSeries = ref(false);
const renderingAssetKey = ref("");
const renderedUrls = reactive({});
const novelResult = ref(null);
const importedCharacters = ref([]);
const recentDecompositions = ref([]);
const activeDraftId = ref("");

const craftRangePreset = ref("45-90");
const novelForm = reactive({
  title: "",
  market_mode: "cn",
  style: "短剧",
  total_episodes: 5,
  episode_duration: 60,
  episode_duration_min: 45,
  episode_duration_max: 90,
  novel_text: "",
});

function applyCraftRange(preset) {
  const [lo, hi] = String(preset || "45-90").split("-").map(Number);
  if (lo && hi) {
    novelForm.episode_duration_min = lo;
    novelForm.episode_duration_max = hi;
    novelForm.episode_duration = Math.round((lo + hi) / 2);
  }
}

function persistCraftWorkspace() {
  try {
    const snapshot = {
      activeTab: activeTab.value,
      novelForm: { ...novelForm },
      novelResult: novelResult.value,
      importedCharacters: importedCharacters.value,
      renderedUrls: { ...renderedUrls },
      activeDraftId: activeDraftId.value,
      recentDecompositions: recentDecompositions.value.slice(0, 6),
      reviewScriptInput: reviewScriptInput.value,
      reviewReport: reviewReport.value,
      doctorResult: doctorResult.value,
      sceneForm: { ...sceneForm },
      sceneGridResult: sceneGridResult.value,
      propForm: { ...propForm },
      propSheetResult: propSheetResult.value,
      videoSanitizeForm: { ...videoSanitizeForm },
      videoSanitizeResult: videoSanitizeResult.value,
    };
    localStorage.setItem(CRAFT_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // 忽略浏览器存储配额限制，不影响主流程
  }
}

function saveDecompositionToHistory(result) {
  if (!result) return;
  const draftId = `craft_${Date.now()}`;
  const entry = {
    id: draftId,
    title: result.title || novelForm.title || "未命名拆剧",
    episodes_count: result.total_episodes || novelForm.total_episodes || 5,
    form: { ...novelForm },
    result,
    importedCharacters: [...importedCharacters.value],
    renderedUrls: { ...renderedUrls },
  };
  recentDecompositions.value = [entry, ...recentDecompositions.value].slice(0, 6);
  activeDraftId.value = draftId;
}

function syncActiveDraftExtras() {
  if (!activeDraftId.value) return;
  const target = recentDecompositions.value.find((d) => d.id === activeDraftId.value);
  if (target) {
    target.importedCharacters = [...importedCharacters.value];
    target.renderedUrls = { ...renderedUrls };
  }
}

function restoreDraft(draftId) {
  const draft = recentDecompositions.value.find((d) => d.id === draftId);
  if (!draft) return;
  Object.assign(novelForm, draft.form || {});
  novelResult.value = draft.result || null;
  importedCharacters.value = Array.isArray(draft.importedCharacters) ? [...draft.importedCharacters] : [];
  for (const k of Object.keys(renderedUrls)) delete renderedUrls[k];
  Object.assign(renderedUrls, draft.renderedUrls || {});
  activeDraftId.value = draft.id;
  persistCraftWorkspace();
}

function clearCurrentDecomposition() {
  novelResult.value = null;
  importedCharacters.value = [];
  activeDraftId.value = "";
  for (const k of Object.keys(renderedUrls)) delete renderedUrls[k];
  persistCraftWorkspace();
}

async function handleDecomposeNovel() {
  pageError.value = "";
  decomposing.value = true;
  try {
    const lo = Math.min(novelForm.episode_duration_min || 45, novelForm.episode_duration_max || 90);
    const hi = Math.max(novelForm.episode_duration_min || 45, novelForm.episode_duration_max || 90);
    const res = await craftApi.decomposeNovel({
      ...novelForm,
      episode_duration: Math.round((lo + hi) / 2),
      episode_duration_min: lo,
      episode_duration_max: hi,
    });
    novelResult.value = res;
    importedCharacters.value = [];
    for (const k of Object.keys(renderedUrls)) delete renderedUrls[k];
    saveDecompositionToHistory(res);
    persistCraftWorkspace();
    ElMessage.success("AI 已完成原文拆解并自动保存在本页，切换页面也不会丢失");
  } catch (e) {
    pageError.value = e.message || "AI 拆解失败，请确认已在配置中开启付费 API 与有效模型密钥";
    ElMessage.error(pageError.value);
  } finally {
    decomposing.value = false;
  }
}

async function importCharactersToLibrary() {
  if (!novelResult.value?.characters?.length) return;
  importingChars.value = true;
  try {
    const payload = novelResult.value.characters.map((c) => ({
      name: c.name,
      face_dna: c.face_dna,
      signature_clothing: c.signature_clothing,
      tts_voice: c.tts_voice || "zh-CN-YunyangNeural",
      rate: c.rate || "+0%",
      pitch: c.pitch || "+0Hz",
    }));
    const created = await charactersApi.batchCreate(payload);
    const createdList = Array.isArray(created) ? created : created?.characters || [];
    importedCharacters.value = createdList.map((item) => ({
      character_id: item.character_id || item.id,
      name: item.name,
    }));
    syncActiveDraftExtras();
    persistCraftWorkspace();
    ElMessage.success(`已将 ${payload.length} 位角色入库到「角色资产库」`);
  } catch (e) {
    ElMessage.error(e.message || "角色入库失败");
  } finally {
    importingChars.value = false;
  }
}

function sendFirstEpisodeToVideoCreate(customScript = "") {
  const title = novelResult.value?.title || novelForm.title || "工坊短剧第一集";
  const scriptBody = String(
    customScript ||
      novelResult.value?.first_episode_script ||
      novelForm.novel_text ||
      ""
  ).trim();
  if (!scriptBody) {
    ElMessage.warning("当前还没有可用于生成视频的剧本内容");
    return;
  }
  const charsSummary = (novelResult.value?.characters || [])
    .slice(0, 4)
    .map((c) => `${c.name}（${c.role || "角色"}：${c.face_dna || ""}；${c.signature_clothing || ""}）`)
    .join("\n");
  const fullBrief = [
    `【剧名】${title}`,
    charsSummary ? `【主要人物造型】\n${charsSummary}` : "",
    `【第1集拍摄剧本】\n${scriptBody}`,
  ]
    .filter(Boolean)
    .join("\n\n");
  const lo = Number(novelResult.value?.episode_duration_min || novelForm.episode_duration_min || 45);
  const hi = Number(novelResult.value?.episode_duration_max || novelForm.episode_duration_max || 90);
  const prefill = {
    title,
    user_input: fullBrief,
    style: novelResult.value?.style || novelForm.style || "短剧",
    generation_mode: "storyboard_flow",
    duration: 0,
    duration_min: lo,
    duration_max: hi,
    intro_title: title.slice(0, 40),
    character_ids: importedCharacters.value.map((c) => c.character_id).filter(Boolean),
  };
  localStorage.setItem(PREFILL_VIDEO_KEY, JSON.stringify(prefill));
  persistCraftWorkspace();
  router.push("/");
}

async function createSeriesFromDecomposition() {
  if (!novelResult.value) return;
  creatingSeries.value = true;
  try {
    let confirmedIds = [];
    const candidateIds = importedCharacters.value.map((c) => c.character_id).filter(Boolean);
    if (candidateIds.length) {
      try {
        const list = await charactersApi.list();
        const items = Array.isArray(list) ? list : list?.characters || [];
        const confirmedSet = new Set(
          items.filter((c) => c && c.confirmed && !c.archived).map((c) => c.character_id || c.id)
        );
        confirmedIds = candidateIds.filter((id) => confirmedSet.has(id));
      } catch {
        confirmedIds = [];
      }
    }
    const lo = Number(novelResult.value.episode_duration_min || novelForm.episode_duration_min || 45);
    const hi = Number(novelResult.value.episode_duration_max || novelForm.episode_duration_max || 90);
    const target = Number(novelResult.value.episode_duration || novelForm.episode_duration || Math.round((lo + hi) / 2));
    const created = await seriesApi.create({
      title: novelResult.value.title,
      brief: novelForm.novel_text,
      total_episodes: novelResult.value.total_episodes || 5,
      episode_duration: Math.min(180, Math.max(10, target)),
      episode_duration_min: Math.max(10, Math.min(lo, hi)),
      episode_duration_max: Math.min(180, Math.max(15, lo, hi)),
      generation_mode: "auto_director",
      style: novelResult.value.style || "短剧",
      ratio: "9:16",
      market_mode: novelResult.value.market_mode || "cn",
      character_ids: confirmedIds,
      auto_continue: false,
      pre_shoot_review: true,
      initial_episodes: novelResult.value.episodes_outline,
      continuity_ledgers: novelResult.value.continuity_ledgers,
      first_episode_script: novelResult.value.first_episode_script || null,
    });
    persistCraftWorkspace();
    if (candidateIds.length && !confirmedIds.length) {
      ElMessage.success("连载短剧项目已创建（角色尚未在「角色资产」确认定妆照，暂按剧本造型创建）");
    } else {
      ElMessage.success("连载短剧项目已创建并带入本剧连续性台账");
    }
    router.push(`/series/${created.series_id || created.id}`);
  } catch (e) {
    ElMessage.error(e.message || "创建连载项目失败");
  } finally {
    creatingSeries.value = false;
  }
}

function sendToReviewer(scriptText) {
  reviewScriptInput.value = scriptText;
  activeTab.value = "reviewer";
  handleReviewScript();
}

// ── Tab 2: AI 剧本审稿与深度改稿 ──
const reviewing = ref(false);
const doctoring = ref(false);
const reviewReport = ref(null);
const doctorResult = ref(null);
const reviewScriptInput = ref("");

async function handleReviewScript() {
  pageError.value = "";
  reviewing.value = true;
  try {
    reviewReport.value = await craftApi.reviewScript({
      script_text: reviewScriptInput.value,
      duration: 60,
    });
  } catch (e) {
    pageError.value = e.message || "AI 审稿失败，请检查模型配置";
    ElMessage.error(pageError.value);
  } finally {
    reviewing.value = false;
  }
}

async function handleDoctorScript() {
  pageError.value = "";
  doctoring.value = true;
  try {
    const res = await craftApi.doctorScript({
      script_text: reviewScriptInput.value,
      title: novelResult.value?.title || "精修剧本",
      duration: 60,
    });
    doctorResult.value = res;
    if (res.polished_script) {
      reviewScriptInput.value = res.polished_script;
    }
    if (res.after_review) {
      reviewReport.value = res.after_review;
    }
    ElMessage.success("AI 已完成剧本深度重写与动作可拍性升级");
  } catch (e) {
    pageError.value = e.message || "AI 改稿失败，请检查模型配置";
    ElMessage.error(pageError.value);
  } finally {
    doctoring.value = false;
  }
}

// ── Tab 3: AI 场景九宫格 / 道具四视图 / 视频动作升级 ──
const generatingSceneGrid = ref(false);
const sceneForm = reactive({
  scene_name: "",
  space_type: "indoor",
  era_and_style: "",
  layout_and_materials: "",
  lighting_and_kelvin: "",
});
const sceneGridResult = ref(null);

async function handleGenerateSceneGrid() {
  pageError.value = "";
  generatingSceneGrid.value = true;
  try {
    sceneGridResult.value = await craftApi.sceneGridPrompt({ ...sceneForm });
    ElMessage.success("AI 已设计完成 16:9 场景 3×3 九宫格提示词");
  } catch (e) {
    pageError.value = e.message || "生成场景提示词失败";
    ElMessage.error(pageError.value);
  } finally {
    generatingSceneGrid.value = false;
  }
}

const generatingPropSheet = ref(false);
const propForm = reactive({
  prop_name: "",
  size_vs_palm: "",
  material_and_color: "",
  key_detail: "",
  style: "",
});
const propSheetResult = ref(null);

async function handleGeneratePropSheet() {
  pageError.value = "";
  generatingPropSheet.value = true;
  try {
    propSheetResult.value = await craftApi.propSheetPrompt({ ...propForm });
    ElMessage.success("AI 已设计完成 16:9 道具四格设定图提示词");
  } catch (e) {
    pageError.value = e.message || "生成道具提示词失败";
    ElMessage.error(pageError.value);
  } finally {
    generatingPropSheet.value = false;
  }
}

async function renderNovelAssetImage(item, category, key) {
  if (!item?.prompt) return;
  renderingAssetKey.value = key;
  try {
    const res = await craftApi.renderAssetImage({
      prompt: item.prompt,
      negative_prompt: item.negative_prompt || "",
      category,
      ratio: "16:9",
    });
    renderedUrls[key] = res.asset_path;
    syncActiveDraftExtras();
    persistCraftWorkspace();
    ElMessage.success("生图完成！");
  } catch (e) {
    ElMessage.error(e.message || "生图失败，请检查图片模型与付费 API 开关");
  } finally {
    renderingAssetKey.value = "";
  }
}

const upgradingVideoPrompt = ref(false);
const videoSanitizeForm = reactive({
  raw_prompt: "",
  speaker: "",
  shot_type: "",
  camera_motion: "",
});
const videoSanitizeResult = ref(null);

async function handleSanitizeVideoPrompt() {
  pageError.value = "";
  upgradingVideoPrompt.value = true;
  try {
    videoSanitizeResult.value = await craftApi.sanitizeVideoPrompt({
      raw_prompt: videoSanitizeForm.raw_prompt,
      speaker: videoSanitizeForm.speaker,
      character_ids: videoSanitizeForm.speaker ? ["char_1"] : [],
      all_character_names: videoSanitizeForm.speaker ? [videoSanitizeForm.speaker] : [],
      shot_type: videoSanitizeForm.shot_type,
      camera_motion: videoSanitizeForm.camera_motion,
    });
    ElMessage.success("AI 已扩写升级为 MCSLA 物理动作链视频提示词");
  } catch (e) {
    pageError.value = e.message || "视频提示词生成失败";
    ElMessage.error(pageError.value);
  } finally {
    upgradingVideoPrompt.value = false;
  }
}

// ── Tab 4: 纯结构化模板占位规范（无固定假剧本） ──
const handbookTemplates = [
  {
    title: "① 角色 16:9 四格定妆板结构",
    summary: "正面特写 + 正/侧/背全身四格合一，含鞋履与空手垂放约束",
    content: `角色设定图，整板 16:9 横版构图，纯白无接缝背景（#FFFFFF）。
画面风格：{写实/3D/2D质感词块}，人物主体：{角色名}，真人正常 7 头身比例。
全图共 4 格，从左到右横向并排，不设独立大主图：
① 左一格：头部正面特写（占格高70%以上，唯一清晰完整展示五官区域，{10项五官锚点}，平静中性表情，底部仅配简短英文音色标签与声纹波形线）；
② 左二格：正面全身站立图（头顶至鞋底完整入画，上下各留5%白边，双手自然垂放身体两侧，空手不持道具，{服装与具体鞋履描述}）；
③ 左三格：90度正侧面全身站立图（含头含鞋，展示侧面轮廓与服装侧面剪裁）；
④ 右一格：背面全身站立图（含头含鞋，展示后背发型收束与衣摆背面）。
严禁项：严禁第5个人物、严禁Q版大头、严禁中文乱码、严禁手持道具。`,
  },
  {
    title: "② 分镜首帧图八段式结构（含参考图防变脸约束）",
    summary: "传参考图时剥离五官重描，超出 2 人转远景虚化剪影，使用戏剧光源",
    content: `[CHARACTER] {角色A}(@{角色A}参考图) — same identity, 位于画面{左/右}侧，面向{方向}；（传入参考图后严禁重描五官细节）。
[BACKGROUND] 次要路人仅以焦外虚化剪影（blurred silhouettes in shallow DoF bokeh）呈现，单镜具名清晰人脸 ≤ 2 人。
[ACTION] {首帧定格物理动作瞬间、四肢接触点与面部肌肉微表情}。
[SCENE] {前景构图遮挡 + 中景主体交互区 + 远景空间纵深锚点}。
[CAMERA] {景别 + 机位高度/角度 + 镜头焦段}。
[LIGHT] {主光源物理位置与冷暖色温 + 轮廓边缘光，剥离影棚平光}。
[TEXT] 画面中无任何字幕、气泡、UI或水印。
[STYLE] {全片统一视觉风格指纹}，{9:16 或 16:9}。`,
  },
  {
    title: "③ 多镜视频生成七段式结构（180°轴线锁 + MCSLA）",
    summary: "锁死站位轴线，单人镜不出现画外角色名，每镜句尾追加环境微锁",
    content: `【画幅风格】{9:16竖屏/16:9横屏}，{全片视觉质感}。
【场景资产】@场景_{场景名}（{空间布局与主光源方向}）。
【核心人物】@角色_{角色A}、@角色_{角色B}（保持定妆外观一致）。
【站位声明（180°轴线锁）】左{角色A}面向画右，右{角色B}面向画左，严禁跨镜头翻转轴线。
【时间轴分镜】
- 0-3s [景别·机位·单一运镜] {动作起因 -> 受力接触 -> 物理反应 -> 终态}；身后{固定背景物}与{主光色}保持不变。
  台词：【{角色A}（语气）："{不超过15字的短句}"】
【音效】{具体环境底噪与动作交互音效}。
【强制禁止项】严禁轴线翻转、严禁文学比喻（如端坐如钟）、严禁单人镜点名画外人、严禁反光道具过度发光。`,
  },
  {
    title: "④ 短剧 15 秒四挡推进与台词写作规范",
    summary: "前3秒冲突开场 + 强视觉 △ 动作密度控制 + 短句算账节奏",
    content: `1. 单集四挡推进：0~15s 抓人立事（强视觉+具体数字/死线） → 15~45s 正面交锋 → 45~85s 亮牌翻转（当集结算，反派必付代价） → 85~110s 余波断章钩子。
2. 动作锚点密度：每 2~3 句对白必插一条强视觉 △（物理结果 / 身份反差 / 物证对抗），禁用“眼神微动、微微一怔、心里一紧”等不可拍心理词。
3. 台词节奏：物证与大白话利害结论同轮直给；专业设定词当句翻译成“多少钱/判几年/谁输谁赢”；3~6字短句与8~16字算账句快慢变速；严禁书面套话。`,
  },
];

async function copyText(text, label = "内容") {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success(`已复制「${label}」到剪贴板`);
  } catch {
    ElMessage.warning("复制失败，请手动选中复制");
  }
}

onMounted(() => {
  try {
    const raw = localStorage.getItem(CRAFT_STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== "object") return;
    if (saved.activeTab) activeTab.value = saved.activeTab;
    if (saved.novelForm) Object.assign(novelForm, saved.novelForm);
    if (saved.novelResult) novelResult.value = saved.novelResult;
    if (Array.isArray(saved.importedCharacters)) importedCharacters.value = saved.importedCharacters;
    if (saved.renderedUrls) Object.assign(renderedUrls, saved.renderedUrls);
    if (saved.activeDraftId) activeDraftId.value = saved.activeDraftId;
    if (Array.isArray(saved.recentDecompositions)) recentDecompositions.value = saved.recentDecompositions;
    if (typeof saved.reviewScriptInput === "string") reviewScriptInput.value = saved.reviewScriptInput;
    if (saved.reviewReport) reviewReport.value = saved.reviewReport;
    if (saved.doctorResult) doctorResult.value = saved.doctorResult;
    if (saved.sceneForm) Object.assign(sceneForm, saved.sceneForm);
    if (saved.sceneGridResult) sceneGridResult.value = saved.sceneGridResult;
    if (saved.propForm) Object.assign(propForm, saved.propForm);
    if (saved.propSheetResult) propSheetResult.value = saved.propSheetResult;
    if (saved.videoSanitizeForm) Object.assign(videoSanitizeForm, saved.videoSanitizeForm);
    if (saved.videoSanitizeResult) videoSanitizeResult.value = saved.videoSanitizeResult;
  } catch {
    // 忽略损坏的本地草稿
  }
});

watch(
  [
    activeTab,
    novelForm,
    novelResult,
    reviewScriptInput,
    reviewReport,
    doctorResult,
    sceneForm,
    sceneGridResult,
    propForm,
    propSheetResult,
    videoSanitizeForm,
    videoSanitizeResult,
    renderedUrls,
  ],
  () => {
    persistCraftWorkspace();
  },
  { deep: true }
);
</script>

<style scoped>
.craft-page {
  max-width: 1320px;
  margin: 0 auto;
}
.craft-hero {
  background: radial-gradient(ellipse at 90% 15%, #3b82f633 0%, transparent 55%),
    linear-gradient(125deg, #0f172a, #1e293b);
  color: #f8fafc;
  padding: 28px 32px;
  border-radius: 18px;
  margin-bottom: 20px;
}
.craft-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #a3e635;
  margin-bottom: 8px;
}
.craft-hero h1 {
  margin: 0 0 8px;
  font-size: 26px;
  color: #f8fafc;
}
.craft-hero p {
  margin: 0;
  color: #cbd5e1;
  font-size: 14px;
  line-height: 1.6;
}
.craft-tabs {
  border-radius: 14px;
  overflow: hidden;
}
.tab-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.tab-header-bar h2 {
  margin: 0 0 4px;
  font-size: 20px;
}
.muted {
  color: #64748b;
  font-size: 13px;
  margin: 0;
}
.form-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.flex-1 {
  flex: 1;
  min-width: 180px;
}
.flex-2 {
  flex: 2;
  min-width: 240px;
}
.action-bar {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.result-container {
  display: flex;
  flex-direction: column;
  gap: 18px;
  margin-top: 18px;
}
.sub-card {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}
.card-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.card-title-row h3 {
  margin: 0;
  font-size: 17px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.metric-box {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px;
}
.metric-top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
.metric-top strong {
  color: #2563eb;
}
.metric-box p {
  margin: 0;
  font-size: 12px;
  color: #475569;
}
.char-extract-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}
.char-extract-item {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px;
}
.char-extract-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.char-extract-item p {
  font-size: 13px;
  color: #334155;
  margin: 4px 0;
  line-height: 1.5;
}
.voice-chip {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  background: #eff6ff;
  color: #1d4ed8;
  padding: 2px 8px;
  border-radius: 999px;
}
.episode-extract-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.episode-extract-row {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px 16px;
}
.episode-extract-row.paywall-ep {
  background: #fffbeb;
  border-color: #f59e0b;
}
.ep-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.episode-extract-row p {
  margin: 4px 0;
  font-size: 13px;
  color: #334155;
}
.two-col-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 18px;
}
.code-box {
  background: #0f172a;
  color: #e2e8f0;
  padding: 14px;
  border-radius: 10px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.65;
  max-height: 360px;
  overflow-y: auto;
  margin: 0;
}
.code-box.compact {
  max-height: 190px;
  font-size: 12px;
}
.asset-prompt-block {
  margin-bottom: 14px;
}
.asset-prompt-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.rendered-asset-img {
  display: block;
  width: 100%;
  max-height: 320px;
  object-fit: contain;
  margin-top: 10px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
}
.score-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e2e8f0;
}
.score-main {
  display: flex;
  align-items: center;
  gap: 14px;
}
.score-grade {
  font-size: 28px;
  font-weight: 800;
  padding: 10px 18px;
  border-radius: 12px;
  background: #eff6ff;
  color: #1d4ed8;
}
.score-grade.grade-S {
  background: #dcfce7;
  color: #15803d;
}
.score-grade.grade-C {
  background: #fee2e2;
  color: #b91c1c;
}
.dim-scores {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.dim-pill {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.dim-pill span {
  font-size: 12px;
  color: #64748b;
}
.dim-pill strong {
  font-size: 18px;
  color: #0f172a;
}
.gear-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.gear-item {
  padding: 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
}
.gear-item.pass {
  border-left: 4px solid #22c55e;
}
.gear-item.warn {
  border-left: 4px solid #f59e0b;
}
.gear-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.gear-item p {
  margin: 0;
  font-size: 12px;
  color: #475569;
}
.issue-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}
.issue-card {
  border: 1px solid #e2e8f0;
  border-left: 4px solid #f59e0b;
  border-radius: 10px;
  padding: 12px 16px;
  background: #fffbeb;
}
.issue-card.sev-high {
  border-left-color: #ef4444;
  background: #fef2f2;
}
.issue-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.issue-card p {
  margin: 4px 0;
  font-size: 13px;
}
.issue-rx {
  color: #15803d;
  font-weight: 600;
}
.doctor-change-list {
  margin: 6px 0 0;
  padding-left: 18px;
}
.prompt-out-box {
  margin-top: 14px;
}
.applied-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 8px;
}
.handbook-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
  gap: 16px;
}
.draft-history-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
}
.draft-history-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}
.draft-chip {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #334155;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.draft-chip:hover {
  border-color: #4f46e5;
  color: #4f46e5;
}
.draft-chip.active {
  background: #4f46e5;
  border-color: #4f46e5;
  color: #ffffff;
  font-weight: 600;
}
.next-step-banner {
  background: linear-gradient(135deg, #eef2ff 0%, #f0fdf4 100%);
  border: 1px solid #c7d2fe;
}
</style>
