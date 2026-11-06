import { createResourceClient } from './resourceClient'

const client = createResourceClient('/api/usage')
const path = (scopeType, scopeId) => `/${encodeURIComponent(scopeType)}/${encodeURIComponent(scopeId)}`

export const usageApi = {
  get: (scopeType, scopeId) => client.get(path(scopeType, scopeId)),
  setLimit: (scopeType, scopeId, limit) => client.put(path(scopeType, scopeId), { limit }),
}
