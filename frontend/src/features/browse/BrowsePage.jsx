import { useEffect, useMemo, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes, formatDateTime, relativeTime } from '../../lib/format.js';
import { scanApi } from '../scan/api.js';

/**
 * Repository browser.
 *
 * Two views, because a flat asset list is the wrong shape for most questions:
 *
 *  - **Images** (default): components as image → tag, with push time, size and
 *    a delete action. A Docker repository's asset list is mostly layer blobs
 *    (`v2/myapp/blobs/sha256:…`), which tells you nothing about what images you
 *    actually have.
 *  - **Files**: the raw assets, arranged as an expandable directory tree built
 *    from their paths, with download links.
 *
 * Downloads are proxied through the backend, so the browser never needs Nexus
 * credentials.
 */
export default function BrowsePage() {
  const [repos, setRepos] = useState([]);
  const [repo, setRepo] = useState('');
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [view, setView] = useState('images');
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

  const selectedRepo = repos.find((r) => r.name === repo);

  return (
    <div className="p-6">
      <div className="mb-5">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Browse</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          images and tags · or the raw file tree · download and delete
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

        <div>
          <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">View</label>
          <div className="flex">
            {[
              ['images', selectedRepo?.format === 'docker' ? 'Images' : 'Components'],
              ['files', 'Files'],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setView(key)}
                className={`border px-3 py-1.5 font-mono text-xs uppercase tracking-wider transition-colors ${
                  view === key
                    ? 'border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300'
                    : 'border-slate-300 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800'
                } ${key === 'files' ? '-ml-px' : ''}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {selectedRepo && (
          <div className="flex items-center gap-2 pb-1.5 font-mono text-[10px] text-slate-400 dark:text-slate-600">
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

      {repo && view === 'images' && <ImagesView repo={repo} onError={setError} />}
      {repo && view === 'files' && <FilesView repo={repo} onError={setError} />}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Images view — image → tag, with delete
 * ------------------------------------------------------------------------ */
function ImagesView({ repo, onError }) {
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(() => new Set());
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [filter, setFilter] = useState('');
  const [scanning, setScanning] = useState({});
  const [scanMsg, setScanMsg] = useState('');

  const load = async () => {
    setLoading(true);
    setResult(null);
    try {
      const data = await api.get(`/repositories/${encodeURIComponent(repo)}/images`);
      setImages(data ?? []);
      setSelected(new Set());
    } catch (err) {
      onError(err.message);
      setImages([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [repo]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return images;
    return images
      .map((img) => {
        if (img.name.toLowerCase().includes(q)) return img;
        const tags = img.tags.filter((t) => t.tag.toLowerCase().includes(q));
        return tags.length ? { ...img, tags } : null;
      })
      .filter(Boolean);
  }, [images, filter]);

  const toggleTag = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const deleteSelected = async (ids, label) => {
    if (!ids.length) return;
    if (!confirm(
      `Delete ${ids.length} tag${ids.length > 1 ? 's' : ''}${label ? ` from ${label}` : ''}?\n\n` +
      'This removes them from Nexus. Disk space is only reclaimed once the ' +
      '"Compact blob store" task runs — it will be triggered automatically.',
    )) return;

    setBusy(true);
    setResult(null);
    onError('');
    try {
      const res = await api.post(`/repositories/${encodeURIComponent(repo)}/images/delete`, {
        component_ids: ids,
        compact: true,
      });
      setResult(res);
      await load();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  /** Queue a scan for one or more "name:tag" images — same endpoint the
   * Vulnerability Scanning section's Images tree uses. Fired from here too so
   * you don't have to leave Browse and re-find the image over there. */
  const scanImages = async (imageRefs, label) => {
    setScanMsg('');
    const key = imageRefs.join(',');
    setScanning((s) => ({ ...s, [key]: true }));
    try {
      await Promise.all(imageRefs.map((image) => scanApi.scanImage(repo, image)));
      setScanMsg(`Scan queued for ${label || `${imageRefs.length} tag(s)`}.`);
    } catch (err) {
      onError(`could not queue scan: ${err.message}`);
    } finally {
      setScanning((s) => ({ ...s, [key]: false }));
    }
  };

  const totalTags = images.reduce((n, img) => n + img.tag_count, 0);

  return (
    <>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
            {images.length} image{images.length === 1 ? '' : 's'} · {totalTags} tag{totalTags === 1 ? '' : 's'}
          </h2>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter image or tag…"
            className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
          />
        </div>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <button
              onClick={() => deleteSelected([...selected], '')}
              disabled={busy}
              className="border border-rose-300 bg-rose-50 px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-rose-700 hover:bg-rose-100 disabled:opacity-50 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-400"
            >
              {busy ? '···' : `Delete ${selected.size} selected`}
            </button>
          )}
          <button
            onClick={load}
            disabled={loading || busy}
            className="flex items-center gap-1.5 border border-slate-300 px-2 py-1 font-mono text-[11px] uppercase text-slate-500 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <Icon name="refresh" size={11} className={loading ? 'animate-spin' : ''} /> refresh
          </button>
        </div>
      </div>

      {result && <DeleteResult result={result} />}
      {scanMsg && (
        <div className="mb-3 border border-sky-200 bg-sky-50 px-3 py-2 font-mono text-xs text-sky-700 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-400">{scanMsg}</div>
      )}

      <div className="border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              <th className="w-8 px-3 py-2" />
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Image / tag</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Size</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Created</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
            </tr>
          </thead>
          <tbody>
            {!loading && shown.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-10 text-center font-mono text-xs text-slate-400 dark:text-slate-600">
                  {filter ? 'nothing matches that filter' : 'no images in this repository'}
                </td>
              </tr>
            )}
            {loading && (
              <tr><td colSpan={5} className="px-3 py-10 text-center font-mono text-xs text-slate-400">loading…</td></tr>
            )}
            {shown.map((img) => {
              const open = expanded.has(img.name);
              return (
                <FragmentRows
                  key={img.name}
                  img={img}
                  open={open}
                  busy={busy}
                  selected={selected}
                  scanning={scanning}
                  onToggleOpen={() => setExpanded((prev) => {
                    const next = new Set(prev);
                    next.has(img.name) ? next.delete(img.name) : next.add(img.name);
                    return next;
                  })}
                  onToggleTag={toggleTag}
                  onDeleteTag={(id, label) => deleteSelected([id], label)}
                  onDeleteImage={() => deleteSelected(img.tags.map((t) => t.component_id), img.name)}
                  onScanTag={(tag) => scanImages([`${img.name}:${tag}`], `${img.name}:${tag}`)}
                  onScanImage={() => scanImages(img.tags.map((t) => `${img.name}:${t.tag}`), img.name)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** One image row plus, when expanded, its tag rows. */
function FragmentRows({
  img, open, busy, selected, scanning, onToggleOpen, onToggleTag, onDeleteTag, onDeleteImage,
  onScanTag, onScanImage,
}) {
  const imageScanKey = img.tags.map((t) => `${img.name}:${t.tag}`).join(',');
  const imageScanning = !!scanning[imageScanKey];
  return (
    <>
      <tr className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
        <td className="px-3 py-2">
          <button onClick={onToggleOpen} className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200">
            <Icon name="chevron" size={13} className={open ? 'rotate-90' : ''} />
          </button>
        </td>
        <td className="px-3 py-2">
          <button onClick={onToggleOpen} className="inline-flex items-center gap-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">
            <Icon name="folder" size={13} className="text-amber-500" />
            {img.name}
            <span className="text-slate-400 dark:text-slate-600">· {img.tag_count} tag{img.tag_count === 1 ? '' : 's'}</span>
          </button>
        </td>
        <td className="px-3 py-2 text-right font-mono tabular-nums text-xs text-slate-700 dark:text-slate-300">{formatBytes(img.total_bytes || 0)}</td>
        <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600" title={img.last_pushed_at || ''}>
          {img.last_pushed_at ? relativeTime(img.last_pushed_at) : '—'}
        </td>
        <td className="px-3 py-2 text-right">
          <span className="inline-flex gap-1.5">
            <button
              onClick={onScanImage}
              disabled={imageScanning}
              title={`Scan all ${img.tag_count} tags of ${img.name}`}
              className="border border-sky-200 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-600 hover:bg-sky-50 disabled:opacity-50 dark:border-sky-800 dark:text-sky-400 dark:hover:bg-sky-950/40"
            >
              {imageScanning ? '···' : 'scan all'}
            </button>
            <button
              onClick={onDeleteImage}
              disabled={busy}
              title={`Delete all ${img.tag_count} tags of ${img.name}`}
              className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
            >
              delete all
            </button>
          </span>
        </td>
      </tr>

      {open && img.tags.map((t) => {
        const tagScanning = !!scanning[`${img.name}:${t.tag}`];
        return (
          <tr key={t.component_id} className="border-b border-slate-50 bg-slate-50/50 dark:border-slate-800/40 dark:bg-slate-900/30">
            <td className="px-3 py-1.5 text-center">
              <input
                type="checkbox"
                checked={selected.has(t.component_id)}
                onChange={() => onToggleTag(t.component_id)}
                className="accent-rose-500"
              />
            </td>
            <td className="py-1.5 pl-9 pr-3 font-mono text-xs text-slate-600 dark:text-slate-400">
              <span className="inline-flex items-center gap-1.5">
                <Icon name="file" size={11} className="text-slate-400 dark:text-slate-600" />
                {t.tag}
              </span>
            </td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500 dark:text-slate-400">{formatBytes(t.size_bytes || 0)}</td>
            <td className="px-3 py-1.5 font-mono text-xs text-slate-400 dark:text-slate-600" title={t.created_at || 'unknown'}>
              {t.created_at ? formatDateTime(t.created_at) : '—'}
            </td>
            <td className="px-3 py-1.5 text-right">
              <span className="inline-flex gap-1.5">
                <button
                  onClick={() => onScanTag(t.tag)}
                  disabled={tagScanning}
                  title={`Scan ${img.name}:${t.tag}`}
                  className="border border-sky-200 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-600 hover:bg-sky-50 disabled:opacity-50 dark:border-sky-800 dark:text-sky-400 dark:hover:bg-sky-950/40"
                >
                  {tagScanning ? '···' : 'scan'}
                </button>
                <button
                  onClick={() => onDeleteTag(t.component_id, `${img.name}:${t.tag}`)}
                  disabled={busy}
                  className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
                >
                  delete
                </button>
              </span>
            </td>
          </tr>
        );
      })}
    </>
  );
}

/**
 * Outcome of a delete. Each failure is shown with its reason: a partial delete
 * that reports nothing is the reason "delete didn't work" was hard to diagnose.
 */
function DeleteResult({ result }) {
  const compact = result.compact;
  return (
    <div className="mb-3 space-y-1">
      {result.deleted_count > 0 && (
        <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">
          Deleted {result.deleted_count} tag{result.deleted_count === 1 ? '' : 's'}.
          {compact && !compact.triggered && ` Disk space not reclaimed yet: ${compact.reason}`}
          {compact?.triggered && ' Blob compaction triggered.'}
        </div>
      )}
      {result.failed_count > 0 && (
        <div className="border border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-800 dark:bg-rose-950/30">
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-rose-700 dark:text-rose-400">
            {result.failed_count} delete{result.failed_count === 1 ? '' : 's'} failed
          </div>
          {result.failed.map((f) => (
            <div key={f.component_id} className="font-mono text-[11px] text-rose-700 dark:text-rose-300">{f.reason}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Files view — raw assets as a directory tree
 * ------------------------------------------------------------------------ */

/** Build a nested folder tree from flat asset paths. */
function buildTree(assets) {
  const root = { dirs: new Map(), files: [] };
  for (const asset of assets) {
    const parts = (asset.path || '').replace(/^\/+/, '').split('/');
    const filename = parts.pop();
    let node = root;
    for (const part of parts) {
      if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    }
    node.files.push({ ...asset, filename });
  }
  return root;
}

function FilesView({ repo, onError }) {
  const [items, setItems] = useState([]);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(null);
  const [open, setOpen] = useState(() => new Set());

  const load = async (continuationToken = null) => {
    setLoading(true);
    try {
      const res = await api.get(
        `/repositories/${encodeURIComponent(repo)}/assets${continuationToken ? `?continuationToken=${encodeURIComponent(continuationToken)}` : ''}`,
      );
      setItems((prev) => (continuationToken ? [...prev, ...(res.items ?? [])] : (res.items ?? [])));
      setToken(res.continuationToken ?? null);
    } catch (err) {
      onError(err.message);
      if (!continuationToken) { setItems([]); setToken(null); }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { setOpen(new Set()); load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [repo]);

  const download = async (asset) => {
    setDownloading(asset.id);
    try {
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
      onError(err.message);
    } finally {
      setDownloading(null);
    }
  };

  const tree = useMemo(() => buildTree(items), [items]);
  const toggle = (path) => setOpen((prev) => {
    const next = new Set(prev);
    next.has(path) ? next.delete(path) : next.add(path);
    return next;
  });

  return (
    <>
      <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">
        {items.length} file{items.length === 1 ? '' : 's'} loaded
      </div>
      <div className="border border-slate-200 dark:border-slate-800">
        {items.length === 0 && !loading ? (
          <div className="px-3 py-10 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no assets in this repository</div>
        ) : (
          <div className="py-1">
            <TreeNode
              node={tree}
              path=""
              depth={0}
              open={open}
              onToggle={toggle}
              onDownload={download}
              downloading={downloading}
            />
          </div>
        )}
      </div>

      {token && (
        <div className="mt-3 flex justify-center">
          <button
            onClick={() => load(token)}
            disabled={loading}
            className="border border-slate-300 px-4 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {loading ? '···' : 'Load more'}
          </button>
        </div>
      )}
    </>
  );
}

/** Recursive folder/file rows. Folders are collapsed by default below the root. */
function TreeNode({ node, path, depth, open, onToggle, onDownload, downloading }) {
  const dirs = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b));
  const files = [...node.files].sort((a, b) => a.filename.localeCompare(b.filename));

  return (
    <>
      {dirs.map(([name, child]) => {
        const full = path ? `${path}/${name}` : name;
        const isOpen = open.has(full) || depth === 0;
        const count = child.files.length + child.dirs.size;
        return (
          <div key={full}>
            <button
              onClick={() => onToggle(full)}
              className="flex w-full items-center gap-1.5 px-3 py-1 text-left font-mono text-xs text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/30"
              style={{ paddingLeft: `${12 + depth * 16}px` }}
            >
              <Icon name="chevron" size={12} className={`text-slate-400 ${isOpen ? 'rotate-90' : ''}`} />
              <Icon name="folder" size={12} className="text-amber-500" />
              {name}
              <span className="text-slate-400 dark:text-slate-600">· {count}</span>
            </button>
            {isOpen && (
              <TreeNode
                node={child}
                path={full}
                depth={depth + 1}
                open={open}
                onToggle={onToggle}
                onDownload={onDownload}
                downloading={downloading}
              />
            )}
          </div>
        );
      })}

      {files.map((file) => (
        <div
          key={file.id}
          className="flex items-center gap-2 px-3 py-1 hover:bg-slate-50 dark:hover:bg-slate-800/30"
          style={{ paddingLeft: `${28 + depth * 16}px` }}
        >
          <Icon name="file" size={12} className="shrink-0 text-slate-400 dark:text-slate-600" />
          <span className="flex-1 truncate font-mono text-xs text-slate-700 dark:text-slate-300" title={file.path}>{file.filename}</span>
          <span className="shrink-0 font-mono tabular-nums text-[11px] text-slate-500 dark:text-slate-400">{formatBytes(file.fileSize || 0)}</span>
          <span className="hidden shrink-0 font-mono text-[11px] text-slate-400 dark:text-slate-600 sm:inline">{formatDateTime(file.blobCreated || file.lastModified)}</span>
          <button
            onClick={() => onDownload(file)}
            disabled={downloading === file.id}
            title="Download"
            className="flex h-5 w-5 shrink-0 items-center justify-center border border-sky-200 text-sky-600 transition-colors hover:bg-sky-50 disabled:opacity-50 dark:border-sky-800 dark:text-sky-400 dark:hover:bg-sky-950/40"
          >
            <Icon name="download" size={11} className={downloading === file.id ? 'animate-pulse' : ''} />
          </button>
        </div>
      ))}
    </>
  );
}
