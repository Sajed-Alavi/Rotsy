import { api } from '../../lib/api.js';

export const accessApi = {
  tokens: () => api.get('/access/tokens'),
  createToken: (body) => api.post('/access/tokens', body),
  revokeToken: (id) => api.delete(`/access/tokens/${id}`),

  webhooks: () => api.get('/access/webhooks'),

  anonymous: () => api.get('/access/anonymous'),
  grantAnonymous: (repo, repoFormat) => api.post('/access/anonymous/grant', { repo, repo_format: repoFormat }),
  revokeAnonymous: (repo) => api.post('/access/anonymous/revoke', { repo }),

  /** Permission keys, for the token scope picker. Reused from the roles API. */
  permissions: () => api.get('/roles/permissions'),
  repos: () => api.get('/storage/repos'),
};
