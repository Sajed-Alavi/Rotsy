import { api, API_BASE } from '../../lib/api.js';

/**
 * Every /modules/sonar/* call the Code Quality section makes, in one place —
 * same reasoning as features/scan/api.js: one place for a backend route
 * change to land instead of URLs built inline across several components.
 *
 * Code Quality is global (like Vulnerability Scanning) rather than scoped to
 * one Project — repositories/runs/findings here span every synced GitHub/
 * GitLab repository regardless of which Project it's grouped under.
 */
export const codeQualityApi = {
  /** Every repository mapped to a Project, across every Project — the repo picker's pool. */
  repositories: () => api.get('/modules/sonar/repositories'),

  /** Every branch on one repository, live from GitHub/GitLab (no cache). */
  branches: (sourceModule, repositoryId) => api.get(`/modules/${sourceModule}/repositories/${repositoryId}/branches`),

  /** Provision (if needed) and run analysis for one repository + branch. */
  analyze: (sourceModule, repositoryId, branch) =>
    api.post('/modules/sonar/analyze', { source_module: sourceModule, repository_id: repositoryId, branch }),

  /** Global run history. */
  analysisRuns: () => api.get('/modules/sonar/analysis-runs'),
  analysisRun: (id) => api.get(`/modules/sonar/analysis-runs/${id}`),
  qualityGate: (runId) => api.get(`/modules/sonar/analysis-runs/${runId}/quality-gate`),
  issuesForRun: (runId, params) => api.get(`/modules/sonar/analysis-runs/${runId}/issues?${params.toString()}`),
  hotspotsForRun: (runId, params) => api.get(`/modules/sonar/analysis-runs/${runId}/hotspots?${params.toString()}`),
  reportUrl: (runId) => `${API_BASE}/modules/sonar/analysis-runs/${runId}/report.pdf`,

  /** Findings, for the global Findings page — each repo's latest successful run only. */
  findings: (params) => api.get(`/modules/sonar/issues?${params.toString()}`),
};
