import { createResourceClient } from "./resourceClient";
const client = createResourceClient("/api/characters");
const path = (id) => `/${encodeURIComponent(id)}`;
export const charactersApi = {
  list: (includeArchived = false) =>
    client.get("", { params: includeArchived ? { include_archived: true } : {} }),
  create: (data) => client.post("", data),
  update: (id, data) => client.put(path(id), data),
  reference: (id, image_base64, view = "front") =>
    client.post(`${path(id)}/reference`, { image_base64, view }),
  confirm: (id) => client.post(`${path(id)}/confirm`),
  portrait: (id, view = "all", style = "3d", include_turntable = false, prompt = "") =>
    client.post(`${path(id)}/portrait`, { view, style, include_turntable, prompt }),
  turntable: (id, duration = 5) =>
    client.post(`${path(id)}/turntable`, { duration }),
  voicePreview: (id, text) =>
    client.post(`${path(id)}/voice-preview`, { text }),
  archive: (id) => client.delete(path(id)),
  restore: (id) => client.post(`${path(id)}/restore`),
};
