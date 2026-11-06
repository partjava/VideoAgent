import { createResourceClient } from "./resourceClient";
const client = createResourceClient("/api/series");
const path = (id) => `/${encodeURIComponent(id)}`;
export const seriesApi = {
  list: () => client.get(""),
  create: (data) => client.post("", data),
  get: (id) => client.get(path(id)),
  outline: (id) => client.post(`${path(id)}/outline`),
  saveOutline: (id, episodes) =>
    client.put(`${path(id)}/outline`, { episodes }),
  confirm: (id) => client.post(`${path(id)}/confirm`),
  run: (id) => client.post(`${path(id)}/run`),
  stop: (id) => client.post(`${path(id)}/stop`),
  approveShoot: (id, taskId) =>
    client.post(
      `${path(id)}/approve-shoot?task_id=${encodeURIComponent(taskId)}`,
    ),
  dependencyDiff: (id, num) =>
    client.get(`${path(id)}/episodes/${num}/dependency-diff`),
  snapshot: (id, snapshot) =>
    client.get(`${path(id)}/snapshots/${encodeURIComponent(snapshot)}`),
  confirmSnapshot: (id, snapshot, state) =>
    client.post(
      `${path(id)}/snapshots/${encodeURIComponent(snapshot)}/confirm`,
      { state },
    ),
  regenerate: (id, num) =>
    client.post(`${path(id)}/episodes/${num}/regenerate`),
  acceptDependency: (id, num) =>
    client.post(`${path(id)}/episodes/${num}/accept-dependency`),
  publishNotes: (id, num, platform) =>
    client.post(`${path(id)}/episodes/${num}/publish-notes`, { platform }),
  publishCover: (id, num, options) =>
    client.post(`${path(id)}/episodes/${num}/cover`, options),
  compile: (id) =>
    client.post(`${path(id)}/compile`, null, {
      responseType: "blob",
      timeout: 300000,
    }),
  archive: (id) => client.delete(path(id)),
  downloadUrl: (id) => `/api/series${path(id)}/download.zip`,
};
