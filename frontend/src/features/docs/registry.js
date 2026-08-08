/**
 * The documentation tree.
 *
 * Pages are plain Markdown under `src/docs/`, pulled in at build time by Vite's
 * `import.meta.glob` with `?raw`. Authoring a new page is dropping a `.md` file
 * into the right numbered folder — no registry edit, no route to add. The
 * numeric prefixes exist only to order the sidebar and are stripped for display
 * and for URLs.
 *
 * Ordering is by path, so `01-getting-started/01-what-is-rotsy.md` sorts ahead
 * of `02-core-concepts/…`, and the reading order is the learning path.
 */
const FILES = import.meta.glob('../../docs/**/*.md', { query: '?raw', import: 'default', eager: true });

const CATEGORY_LABELS = {
  'getting-started': 'Getting Started',
  'core-concepts': 'Core Concepts',
  guides: 'Guides',
  'vulnerability-db': 'Vulnerability Databases',
  administration: 'Administration',
  reference: 'Reference',
  workflows: 'Real-World Workflows',
};

/** "../../docs/01-getting-started/02-first-login.md" -> parts we can use. */
function parse(path) {
  const rel = path.replace('../../docs/', '').replace(/\.md$/, '');
  const [rawCategory, rawName] = rel.split('/');
  const category = rawCategory.replace(/^\d+-/, '');
  const slug = (rawName || rawCategory).replace(/^\d+-/, '');
  return { category, slug, sortKey: rel };
}

/** First `# Heading` in the file, falling back to a de-slugged filename. */
function titleOf(source, slug) {
  // `.+` here is a linter-flagged "potential" super-linear pattern, but
  // `source` is always a markdown file from this repo, never user input —
  // there's no adversarial input path for a ReDoS concern to apply to.
  const match = source.match(/^#\s+(.+)$/m); // NOSONAR
  if (match) return match[1].trim();
  return slug.replaceAll('-', ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/** First non-heading, non-empty line — used as the sidebar/landing subtitle. */
function summaryOf(source) {
  const line = source
    .split('\n')
    .find((l) => l.trim() && !l.startsWith('#') && !l.startsWith('>') && !l.startsWith('```'));
  return line ? line.trim().replace(/[*`[\]]/g, '').slice(0, 160) : '';
}

export const DOCS = Object.entries(FILES)
  .map(([path, source]) => {
    const { category, slug, sortKey } = parse(path);
    return { slug, category, sortKey, source, title: titleOf(source, slug), summary: summaryOf(source) };
  })
  .sort((a, b) => a.sortKey.localeCompare(b.sortKey));

/** Docs grouped into ordered categories, for the sidebar. */
export const DOC_SECTIONS = DOCS.reduce((acc, doc) => {
  let section = acc.find((s) => s.key === doc.category);
  if (!section) {
    section = { key: doc.category, label: CATEGORY_LABELS[doc.category] || doc.category, docs: [] };
    acc.push(section);
  }
  section.docs.push(doc);
  return acc;
}, []);

export const getDoc = (slug) => DOCS.find((d) => d.slug === slug);
