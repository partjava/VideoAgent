import axios from "axios";

export function createResourceClient(baseURL) {
  const client = axios.create({ baseURL, timeout: 120000 });
  client.interceptors.response.use(
    (response) => {
      const body = response.data;
      if (body?.status === "error" || body?.status === "failed")
        throw new Error(body.message || "操作失败");
      return body?.status === "success" ? body.data : body;
    },
    async (error) => {
      let data = error.response?.data;
      if (typeof Blob !== "undefined" && data instanceof Blob) {
        try {
          data = JSON.parse(await data.text());
        } catch {
          data = null;
        }
      }
      const detail = data?.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item.msg).join("；")
            : data?.message || error.message;
      return Promise.reject(new Error(message || "请求失败，请稍后重试"));
    },
  );
  return client;
}
