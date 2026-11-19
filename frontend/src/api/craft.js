import { createResourceClient } from "./resourceClient";

const client = createResourceClient("/api/craft");

export const craftApi = {
  expandCharacter: (payload) => client.post("/expand-character", payload, { timeout: 240000 }),
  decomposeNovel: (payload) => client.post("/decompose-novel", payload, { timeout: 420000 }),
  reviewScript: (payload) => client.post("/review-script", payload, { timeout: 240000 }),
  doctorScript: (payload) => client.post("/doctor-script", payload, { timeout: 420000 }),
  sceneGridPrompt: (payload) => client.post("/scene-grid-prompt", payload, { timeout: 240000 }),
  propSheetPrompt: (payload) => client.post("/prop-sheet-prompt", payload, { timeout: 240000 }),
  sanitizeVideoPrompt: (payload) => client.post("/sanitize-video-prompt", payload, { timeout: 240000 }),
  renderAssetImage: (payload) => client.post("/render-asset-image", payload, { timeout: 420000 }),
};
