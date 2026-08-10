import { useMemo, useState } from 'react';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import Tabs from '../../../components/Tabs.jsx';
import { formatNumber } from '../../../lib/format.js';
import { scanApi } from '../api.js';
import RepoRows from '../components/RepoRows.jsx';
import TagReportsPanel from '../components/TagReportsPanel.jsx';
import { useResource, useStatus } from '../../../lib/useResource.js';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'scanned', label: 'Scanned' },
  { key: 'baseline', label: 'Baseline' },
  { key: 'queued', label: 'Queued' },
  { key: 'failed', label: 'Failed' },
];

/** Split "name:tag" on the rightmost ':' — the only separator that's actually
 * a tag delimiter, since image names themselves never contain a colon here
 * (unlike a full registry ref, `image` on this endpoint is just "name:tag"). */
function splitImageTag(image) {
  const idx = image.lastIndexOf(':');
  if (idx === -1) return { name: image, tag: '' };
  return { name: image.slice(0, idx), tag: image.slice(idx + 1) };
}

function sumSeverity(rows) {
  return rows.reduce(
    (acc, r) => {
      acc.critical += r.critical || 0;
      acc.high += r.high || 0;
      acc.medium += r.medium || 0;
      acc.low += r.low || 0;
      return acc;
    },
    { critical: 0, high: 0, medium: 0, low: 0 },
  );
}

/** Group the flat (repo, "name:tag") rows into repo → image → tag, with
 * severity counts summed at each level. Pure client-side transform — the
 * dataset backing this page is small, so no server-side aggregation is
 * needed for repo/image rollups. */
function buildTree(images) {
  const repos = new Map();
  for (const row of images) {
    const { name, tag } = splitImageTag(row.image);
    if (!repos.has(row.repo)) repos.set(row.repo, new Map());
    const imagesInRepo = repos.get(row.repo);
    if (!imagesInRepo.has(name)) imagesInRepo.set(name, []);
    imagesInRepo.get(name).push({ ...row, tag });
  }

  return [...repos.entries()]
    .map(([repo, imagesInRepo]) => {
      const images = [...imagesInRepo.entries()]
        .map(([name, tags]) => ({
          name,
          // { numeric: true } compares embedded digit runs by value, not by
          // character, so "9" sorts before "41" instead of after it — plain
          // localeCompare treated tags as opaque strings ("4", "40", "41",
          // "5", ...).
          tags: [...tags].sort((a, b) => a.tag.localeCompare(b.tag, undefined, { numeric: true })),
          counts: sumSeverity(tags),
        }))
        .sort((a, b) => a.name.localeCompare(b.name));
      return { repo, images, counts: sumSeverity(images.flatMap((i) => i.tags)) };
    })
    .sort((a, b) => a.repo.localeCompare(b.repo));
}

/**
 * The image ledger, as a repo → image → tag tree.
 *
 * Previously a flat table with one row per (repo, image:tag) pair, mixing
 * every repository together. `GET /scan/images` already returns exactly that
 * flat shape — the tree here is purely a client-side grouping of it, modeled
 * on BrowsePage.jsx's `ImagesView`/`FragmentRows` expand/collapse pattern
 * (`Set`-based expanded state, chevron-toggle rows) extended one level
 * deeper. Selecting a tag opens `TagReportsPanel` below the tree, which shows
 * that tag's report history and, from there, the existing `ReportDetailModal`.
 */
export default function ImagesPage() {
  const { data: images, loading, reload } = useResource(() => scanApi.images(), []);
  const { status, say, fail, clear } = useStatus();
  const [scanning, setScanning] = useState({});
  const [filter, setFilter] = useState('all');
  const [expandedRepos, setExpandedRepos] = useState(() => new Set());
  const [expandedImages, setExpandedImages] = useState(() => new Set());
  const [selectedTag, setSelectedTag] = useState(null); // { repo, imageName, tag }

  const scan = async (repo, image) => {
    const key = `${repo}/${image}`;
    setScanning((s) => ({ ...s, [key]: true }));
    try {
      const r = await scanApi.scanImage(repo, image);
      say(`Scan queued for ${key} — job ${r.job_id.slice(0, 8)}.`, 'ok');
      setTimeout(reload, 4000);
    } catch (e) {
      fail(`could not queue a scan for ${key}: ${e.message}`);
    } finally {
      setScanning((s) => ({ ...s, [key]: false }));
    }
  };

  const rows = filter === 'all' ? images : images.filter((i) => i.state === filter);
  const counts = FILTERS.reduce((acc, f) => {
    acc[f.key] = f.key === 'all' ? images.length : images.filter((i) => i.state === f.key).length;
    return acc;
  }, {});

  const tree = useMemo(() => buildTree(rows), [rows]);

  const toggleRepo = (repo) => setExpandedRepos((prev) => {
    const next = new Set(prev);
    next.has(repo) ? next.delete(repo) : next.add(repo);
    return next;
  });
  const toggleImage = (key) => setExpandedImages((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });
  const selectTag = (repo, imageName, tag) => setSelectedTag((prev) => (
    prev?.repo === repo && prev?.imageName === imageName && prev?.tag === tag
      ? null
      : { repo, imageName, tag }
  ));

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title={`Images · ${formatNumber(images.length)}`}
        hint="repo → image → tag · scan runs on push, or when you click scan"
        flush
        actions={<Tabs items={FILTERS.map((f) => ({ ...f, badge: counts[f.key] }))} value={filter} onChange={setFilter} />}
      >
        <div className="overflow-x-auto border border-slate-200 dark:border-slate-800">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="w-8 px-3 py-2" />
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Repo / image / tag</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">State</th>
                <th className="px-3 py-2 text-center font-mono text-[10px] uppercase tracking-wider text-slate-500">C/H/M/L</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Last scan</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
              </tr>
            </thead>
            <tbody>
              {!loading && tree.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-10 text-center font-mono text-xs text-slate-400 dark:text-slate-600">
                    {filter === 'all' ? 'no images known yet — enable a repository under Targets' : `no images in state "${filter}"`}
                  </td>
                </tr>
              )}
              {loading && (
                <tr><td colSpan={6} className="px-3 py-10 text-center font-mono text-xs text-slate-400">loading…</td></tr>
              )}
              {tree.map((repoNode) => (
                <RepoRows
                  key={repoNode.repo}
                  repoNode={repoNode}
                  expandedRepos={expandedRepos}
                  expandedImages={expandedImages}
                  onToggleRepo={toggleRepo}
                  onToggleImage={toggleImage}
                  selectedTag={selectedTag}
                  onSelectTag={selectTag}
                  scanning={scanning}
                  onScan={scan}
                />
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {selectedTag && (
        <TagReportsPanel
          repo={selectedTag.repo}
          imageName={selectedTag.imageName}
          tag={selectedTag.tag}
          onClose={() => setSelectedTag(null)}
        />
      )}
    </>
  );
}
