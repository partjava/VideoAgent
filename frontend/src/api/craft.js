import { createResourceClient } from "./resourceClient";

const client = createResourceClient("/api/craft");

export const craftApi = {
  expandCharacter: (payload) => client.post("/expand-character", payload),
  decomposeNovel: (payload) => client.post("/decompose-novel", payload, { timeout: 180000 }),
  reviewScript: (payload) => client.post("/review-script", payload),
  doctorScript: (payload) => client.post("/doctor-script", payload, { timeout: 180000 }),
  sceneGridPrompt: (payload) => client.post("/scene-grid-prompt", payload),
  propSheetPrompt: (payload) => client.post("/prop-sheet-prompt", payload),
  sanitizeVideoPrompt: (payload) => client.post("/sanitize-video-prompt", payload),
  renderAssetImage: (payload) => client.post("/render-asset-image", payload, { timeout: 180000 }),
};
