import { api } from '../../lib/api.js';

/**
 * Every /scan/* call the feature makes, in one place.
 *
 * The page previously built these URLs inline across six components, so the
 * endpoint list was only discoverable by reading the whole file. Keeping them
 * here means a backend route change has exactly one place to land.
 */
export const scanApi = {
  summary: () => api.get('/scan/summary'),

  targets: () => api.get('/scan/targets'),
  createTarget: (body) => api.post('/scan/targets', body),
  updateTarget: (id, body) => api.patch(`/scan/targets/${id}`, body),
  deleteTarget: (id) => api.delete(`/scan/targets/${id}`),

  images: (limit = 200) => api.get(`/scan/images?limit=${limit}`),
  scanImage: (repo, image) => api.post('/scan/image', { repo, image }),

  reports: (limit = 50) => api.get(`/scan/reports?limit=${limit}`),
  report: (id) => api.get(`/scan/reports/${id}`),
  deleteReport: (id) => api.delete(`/scan/reports/${id}`),
  deleteAllReports: () => api.delete('/scan/reports'),

  dbStatus: () => api.get('/scan/db-status'),
  offlineStatus: () => api.get('/scan/db-offline'),
  /** `force` re-downloads a database the backend would otherwise skip as current. */
  updateDb: (force = false) => api.post(`/scan/db-update${force ? '?force=true' : ''}`),
  importDb: () => api.post('/scan/db-import'),
  /** The in-flight (or most recent) DB job, so the UI can reattach after a reload. */
  dbJob: () => api.get('/scan/db-job'),

  /** Findings, for both the page-level list and one report's modal. */
  findings: (endpoint, params) => api.get(`${endpoint}?${params.toString()}`),
  findingsEndpoint: {
    all: '/scan/vulnerabilities',
    forReport: (id) => `/scan/reports/${id}/vulnerabilities`,
  },

  /** Docker repositories, for the target picker. */
  dockerRepos: () => api.get('/storage/repos?format=docker'),
};
