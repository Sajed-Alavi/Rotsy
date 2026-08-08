import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import SeverityCounts from '../../../components/SeverityCounts.jsx';
import { relativeTime } from '../../../lib/format.js';

const STATE_TONE = { scanned: 'ok', queued: 'info', failed: 'bad', baseline: 'neutral' };

/**
 * Repo → image → tag rows for the Images tree.
 *
 * Mirrors BrowsePage.jsx's `ImagesView`/`FragmentRows` pattern (chevron-toggle
 * rows, `Set`-based expanded state, parent + child rows emitted into the same
 * `<tbody>`) extended one level deeper: a repo row expands to image-name rows,
 * which expand to the tag rows that were previously the whole (flat) table.
 * Clicking a tag row selects it, which drives `TagReportsPanel` below the
 * table — it does not expand further, since a tag's report history is a
 * differently-shaped list (fetched separately), not more rows of this tree.
 */
export default function RepoRows({
  repoNode, expandedRepos, expandedImages, onToggleRepo, onToggleImage,
  selectedTag, onSelectTag, scanning, onScan,
}) {
  const open = expandedRepos.has(repoNode.repo);
  return (
    <>
      <tr
        onClick={() => onToggleRepo(repoNode.repo)}
        className="cursor-pointer border-b border-slate-100 bg-white hover:bg-slate-50 dark:border-slate-800/60 dark:bg-slate-950 dark:hover:bg-slate-800/30"
      >
        <td className="px-3 py-2 text-slate-400 dark:text-slate-500">
          <Icon name="chevron" size={13} className={open ? 'rotate-90' : ''} />
        </td>
        <td className="px-3 py-2">
          <span className="inline-flex items-center gap-1.5 font-mono text-xs font-medium text-slate-800 dark:text-slate-200">
            <Icon name="database" size={13} className="text-slate-400 dark:text-slate-600" />
            {repoNode.repo}
            <span className="font-normal text-slate-400 dark:text-slate-600">
              · {repoNode.images.length} image{repoNode.images.length === 1 ? '' : 's'}
            </span>
          </span>
        </td>
        <td className="px-3 py-2" />
        <td className="px-3 py-2 text-center"><SeverityCounts counts={repoNode.counts} /></td>
        <td className="px-3 py-2" />
        <td className="px-3 py-2" />
      </tr>

      {open && repoNode.images.map((imageNode) => (
        <ImageRows
          key={imageNode.name}
          repo={repoNode.repo}
          imageNode={imageNode}
          expandedImages={expandedImages}
          onToggleImage={onToggleImage}
          selectedTag={selectedTag}
          onSelectTag={onSelectTag}
          scanning={scanning}
          onScan={onScan}
        />
      ))}
    </>
  );
}

/** One image-name row plus, when expanded, its tag rows. */
function ImageRows({ repo, imageNode, expandedImages, onToggleImage, selectedTag, onSelectTag, scanning, onScan }) {
  const key = `${repo}::${imageNode.name}`;
  const open = expandedImages.has(key);
  return (
    <>
      <tr
        onClick={() => onToggleImage(key)}
        className="cursor-pointer border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/20"
      >
        <td className="px-3 py-2 pl-6 text-slate-400 dark:text-slate-500">
          <Icon name="chevron" size={12} className={open ? 'rotate-90' : ''} />
        </td>
        <td className="py-2 pl-2 pr-3">
          <span className="inline-flex items-center gap-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">
            <Icon name="folder" size={12} className="text-amber-500" />
            {imageNode.name}
            <span className="text-slate-400 dark:text-slate-600">
              · {imageNode.tags.length} tag{imageNode.tags.length === 1 ? '' : 's'}
            </span>
          </span>
        </td>
        <td className="px-3 py-2" />
        <td className="px-3 py-2 text-center"><SeverityCounts counts={imageNode.counts} /></td>
        <td className="px-3 py-2" />
        <td className="px-3 py-2" />
      </tr>

      {open && imageNode.tags.map((row) => (
        <TagRow
          key={row.id ?? `${repo}/${row.image}`}
          repo={repo}
          imageName={imageNode.name}
          row={row}
          selected={
            selectedTag?.repo === repo &&
            selectedTag?.imageName === imageNode.name &&
            selectedTag?.tag === row.tag
          }
          onSelectTag={onSelectTag}
          scanning={scanning}
          onScan={onScan}
        />
      ))}
    </>
  );
}

/** Leaf row — today's per-(repo, image:tag) row: state badge, severity counts,
 * scan button. Clicking it (outside the button) selects it for the report
 * history panel; clicking the button still queues a scan without selecting. */
function TagRow({ repo, imageName, row, selected, onSelectTag, scanning, onScan }) {
  const scanKey = `${repo}/${row.image}`;
  const busy = !!scanning[scanKey] || row.state === 'queued';
  const failure = (row.reports || []).find((r) => r.status === 'failed' && r.error);
  let scanLabel = 'scan';
  if (busy) scanLabel = '···';
  else if (row.scan_count > 0) scanLabel = 'rescan';

  return (
    <tr
      onClick={() => onSelectTag(repo, imageName, row.tag)}
      className={`cursor-pointer border-b border-slate-50 dark:border-slate-800/40 ${
        selected
          ? 'bg-sky-50 dark:bg-sky-950/30'
          : 'bg-slate-50/50 hover:bg-slate-100/60 dark:bg-slate-900/30 dark:hover:bg-slate-800/30'
      }`}
    >
      <td className="px-3 py-1.5" />
      <td className="py-1.5 pl-11 pr-3">
        <span className="inline-flex items-center gap-1.5 font-mono text-xs text-slate-600 dark:text-slate-400">
          <Icon name="file" size={11} className="text-slate-400 dark:text-slate-600" />
          {row.tag || '(untagged)'}
        </span>
        {failure && (
          <span className="block pl-5 text-[10px] text-rose-600 dark:text-rose-400" title={failure.error}>
            {failure.scanner}: {failure.error}
          </span>
        )}
      </td>
      <td className="px-3 py-1.5">
        <Badge
          tone={STATE_TONE[row.state] || 'neutral'}
          title={row.state === 'baseline' ? 'Present before scanning was enabled — not auto-scanned' : `source: ${row.source}`}
        >
          {row.state}
        </Badge>
      </td>
      <td className="px-3 py-1.5 text-center"><SeverityCounts counts={row.state === 'scanned' ? row : null} /></td>
      <td className="px-3 py-1.5 font-mono text-xs text-slate-400 dark:text-slate-600">
        {row.last_scan_at ? relativeTime(row.last_scan_at) : 'never'}
      </td>
      <td className="px-3 py-1.5 text-right">
        <button
          onClick={(e) => { e.stopPropagation(); onScan(repo, row.image); }}
          disabled={busy}
          className="border border-sky-300 bg-sky-50 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
        >
          {scanLabel}
        </button>
      </td>
    </tr>
  );
}
