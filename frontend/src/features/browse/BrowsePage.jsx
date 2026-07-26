import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes, formatDateTime } from '../../lib/format.js';

/**
 * Repository browser: lists every asset in a repo with full metadata and lets
 * the user download any file. Download is proxied through the backend so the
 * browser never needs Nexus credentials.
 */
export default function BrowsePage() {
  const [repos, setRepos] = useState([]);
  const [repo, setRepo] = useState('');
  const [loadingRepos, setLoadingRepos] = useState(true);

  const [items, setItems] = useState([]);
  const [token, setToken] = useState(null);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [downloading, setDownloading] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get('/storage/repos');
        setRepos(data ?? []);
        if (data?.length) setRepo(data[0].name);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoadingRepos(false);
      }
    })();
  }, []);

  const loadAssets = async (repoName, continuationToken = null) => {
    if (!repoName) return;
    setLoadingAssets(true);
    setError('');
    try {
      const res = await api.get(
        `/repositories/${encodeURIComponent(repoName)}/assets${continuationToken ? `?continuationToken=${encodeURIComponent(continuationToken)}` : ''}`,
      );
      if (continuationToken) {
        setItems((prev) => [...prev, ...(res.items ?? [])]);
      } else {
        setItems(res.items ?? []);
      }
      setToken(res.continuationToken ?? null);
    } catch (err) {
      setError(err.message);
      setItems([]);
      setToken(null);
    } finally {
      setLoadingAssets(false);
    }
  };

  useEffect(() => {
    if (repo) loadAssets(repo);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo]);

  const download = async (asset) => {
    setDownloading(asset.id);
    setError('');
    try {
      // Fetch as blob with credentials (cookie auth), then trigger a browser
      // download with the right filename.
      const base = import.meta.env.VITE_API_BASE_URL || '/api';
      const url = `${base}/repositories/${encodeURIComponent(repo)}/assets/download?path=${encodeURIComponent(asset.path)}`;
      const resp = await fetch(url, { credentials: 'include' });
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`Download failed (${resp.status}) ${txt.slice(0, 120)}`);
      }
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = asset.path.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objUrl);
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(null);
    }
  };

  const copyDownloadUrl = async (asset) => {
    // Build a direct URL the user can share. Note: it still requires auth,
    // so this is mainly for reference / curl with cookies.
    const base = import.meta.env.VITE_API_BASE_URL || '/api';
    const url = `${window.location.origin}${base}/repositories/${encodeURIComponent(repo)}/assets/download?path=${encodeURIComponent(asset.path)}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      /* clipboard may be unavailable */
    }
  };

  const fmtType = (t) => (t || '').split(';')[0] || '—';
  const selectedRepo = repos.find((r) => r.name === repo);

  return (
    <div className="p-6">
      <div className="mb-5">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Browse</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          list every asset with metadata · download any file
        </p>
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3 border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/40">
        <div>
          <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Repository</label>
          <select
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            disabled={loadingRepos}
            className="min-w-[16rem] border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
          >
            {loadingRepos && <option>loading…</option>}
            {!loadingRepos && !repos.length && <option value="">no repositories</option>}
            {repos.map((r) => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
        </div>
        {selectedRepo && (
          <div className="flex items-center gap-2 font-mono text-[10px] text-slate-400 dark:text-slate-600">
            <Badge tone="info">{selectedRepo.format}</Badge>
            <span>{selectedRepo.type}</span>
          </div>
        )}
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">
          <Icon name="alert" size={14} /> {error}
        </div>
      )}

      <div className="border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Path</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Size</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Uploader</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Modified</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
            </tr>
          </thead>
          <tbody>
            {!loadingAssets && items.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-10 text-center font-mono text-xs text-slate-400 dark:text-slate-600">
                  no assets in this repository
                </td>
              </tr>
            ) : (
              items.map((a) => (
                <tr key={a.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                  <td className="px-3 py-2 font-mono text-xs text-slate-800 dark:text-slate-200">
                    <span className="inline-flex items-center gap-1.5 align-middle">
                      <Icon name="file" size={12} className="text-slate-400 dark:text-slate-600" />
                      {a.path}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{fmtType(a.contentType)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500 dark:text-slate-400">{formatBytes(a.fileSize || 0)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{a.uploader || '—'}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(a.lastModified)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => copyDownloadUrl(a)}
                        title="Copy direct URL"
                        className="flex h-6 w-6 items-center justify-center border border-slate-200 text-slate-400 transition-colors hover:text-slate-700 dark:border-slate-700 dark:text-slate-500 dark:hover:text-slate-200"
                      >
                        <Icon name="copy" size={12} />
                      </button>
                      <button
                        onClick={() => download(a)}
                        disabled={downloading === a.id}
                        title="Download"
                        className="flex h-6 w-6 items-center justify-center border border-sky-200 text-sky-600 transition-colors hover:bg-sky-50 disabled:opacity-50 dark:border-sky-800 dark:text-sky-400 dark:hover:bg-sky-950/40"
                      >
                        <Icon name="download" size={12} className={downloading === a.id ? 'animate-pulse' : ''} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {token && (
        <div className="mt-3 flex justify-center">
          <button
            onClick={() => loadAssets(repo, token)}
            disabled={loadingAssets}
            className="border border-slate-300 px-4 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {loadingAssets ? '···' : 'Load more'}
          </button>
        </div>
      )}
      {loadingAssets && items.length > 0 && (
        <div className="mt-2 text-center font-mono text-[10px] text-slate-400 dark:text-slate-600">loading more…</div>
      )}
    </div>
  );
}
