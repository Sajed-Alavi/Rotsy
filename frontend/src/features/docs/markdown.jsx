import { Link } from 'react-router';

/**
 * A small Markdown renderer, deliberately hand-written.
 *
 * Adding a library for this would pull a transitive dependency tree into a
 * project that just spent a whole pass closing supply-chain CVEs, to render
 * prose we author ourselves. The supported subset is everything the docs
 * actually use: headings, paragraphs, ordered/unordered lists, fenced and
 * inline code, tables, blockquotes, horizontal rules, links, bold and italic.
 *
 * **No HTML passthrough.** Raw HTML in a source file is rendered as literal
 * text rather than parsed. The docs are trusted content, but a renderer that
 * cannot inject markup cannot become an XSS vector if that ever stops being
 * true — and React escapes everything we emit as text anyway.
 *
 * Positional keys below (table rows/cells, list items) are intentional, not
 * an oversight: every tree here is parsed once from a static doc string and
 * never reordered, filtered or mutated afterward — the one case where an
 * index is a perfectly stable React key, since "which markdown source line
 * is this" and "what's its position" are the same fact. Marked NOSONAR at
 * each site below (S6479) for the same reason.
 *
 * A couple of regexes here use unbounded `.+`/`.*` or an alternation of
 * overlapping branches, which a general linter flags as a potential
 * super-linear-backtracking risk (S8786). Real ReDoS needs *adversarial*
 * input reaching the pattern; every input here is a markdown file this
 * project's own contributors wrote and committed, not user-submitted text —
 * rewriting these into less readable forms to guard against an input source
 * that doesn't exist isn't a trade worth making. Marked NOSONAR at each site
 * below, left as-is, deliberately.
 *
 * The block parser (Markdown/parse*) is a one-token-type-per-function
 * dispatch: each parser recognizes its own block from `lines[i]` and returns
 * `null` to decline, or `{ node, next }` to claim it and say where the next
 * block starts. The main loop just tries them in order.
 */

/** Inline formatting: `code`, **bold**, *italic*, [text](href). */
function inline(text, keyPrefix = 'i') {
  const nodes = [];
  // Ordered by precedence: code first, so `**` inside a code span stays literal.
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g; // NOSONAR
  let last = 0;
  let match;
  let n = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${n++}`;

    if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="rounded border border-slate-200 bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={key} className="font-semibold text-slate-900 dark:text-slate-100">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*')) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      const [, label, href] = /\[([^\]]+)\]\(([^)]+)\)/.exec(token); // NOSONAR
      const internal = href.startsWith('/');
      nodes.push(internal
        ? <Link key={key} to={href} className="text-sky-600 underline underline-offset-2 hover:text-sky-500 dark:text-sky-400">{label}</Link>
        : <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="text-sky-600 underline underline-offset-2 hover:text-sky-500 dark:text-sky-400">{label}</a>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Turn "## Some Heading" into "some-heading" for in-page anchors. */
export function slugify(text) {
  return text.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-');
}

/** Extract the headings of a document, for the on-page table of contents. */
export function outline(markdown) {
  return markdown
    .split('\n')
    .filter((l) => /^#{2,3}\s/.test(l))
    .map((l) => {
      const level = /^#+/.exec(l)[0].length;
      const text = l.replace(/^#+\s+/, '').trim();
      return { level, text, id: slugify(text) };
    });
}

const H = {
  1: 'mt-0 mb-3 text-xl font-medium text-slate-900 dark:text-slate-100',
  2: 'mt-8 mb-3 border-b border-slate-200 pb-1 text-base font-medium text-slate-900 dark:border-slate-800 dark:text-slate-100',
  3: 'mt-6 mb-2 text-sm font-medium text-slate-800 dark:text-slate-200',
  4: 'mt-4 mb-2 font-mono text-[11px] uppercase tracking-wider text-slate-500',
};

function parseFencedCode(lines, i) {
  const line = lines[i];
  if (!line.startsWith('```')) return null;
  const lang = line.slice(3).trim();
  const body = [];
  i++;
  while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++]);
  i++; // closing fence
  const node = (
    <pre className="my-3 overflow-x-auto border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/40">
      {lang && <div className="mb-1 font-mono text-[9px] uppercase tracking-wider text-slate-400 dark:text-slate-600">{lang}</div>}
      <code className="font-mono text-[11px] leading-relaxed text-slate-700 dark:text-slate-300">{body.join('\n')}</code>
    </pre>
  );
  return { node, next: i };
}

function parseHeading(lines, i, keyBase) {
  const heading = /^(#{1,4})\s+(.*)$/.exec(lines[i]); // NOSONAR
  if (!heading) return null;
  const level = heading[1].length;
  const text = heading[2].trim();
  const Tag = `h${level}`;
  const node = <Tag id={slugify(text)} className={H[level]}>{inline(text, `h${keyBase}`)}</Tag>;
  return { node, next: i + 1 };
}

function parseHorizontalRule(lines, i) {
  if (!/^(-{3,}|\*{3,})$/.test(lines[i].trim())) return null;
  return { node: <hr className="my-6 border-slate-200 dark:border-slate-800" />, next: i + 1 };
}

/** A header row followed by a |---|---| separator. */
function parseTable(lines, i) {
  const line = lines[i];
  if (!line.trim().startsWith('|') || !/^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || '')) return null;
  const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const header = cells(line);
  i += 2;
  const rows = [];
  while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]));
  const node = (
    <div className="my-3 overflow-x-auto border border-slate-200 dark:border-slate-800">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
            {header.map((h, n) => (
              <th key={n} className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">{inline(h, `th${n}`)}</th> // NOSONAR
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60"> {/* NOSONAR */}
              {r.map((c, ci) => (
                <td key={ci} className="px-3 py-2 align-top text-slate-700 dark:text-slate-300">{inline(c, `td${ri}-${ci}`)}</td> // NOSONAR
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return { node, next: i };
}

function parseBlockquote(lines, i, keyBase) {
  if (!lines[i].startsWith('> ')) return null;
  const body = [];
  while (i < lines.length && lines[i].startsWith('> ')) body.push(lines[i++].slice(2));
  const node = (
    <blockquote className="my-3 border-l-2 border-sky-300 bg-sky-50/40 py-2 pl-3 text-sm text-slate-600 dark:border-sky-800 dark:bg-sky-950/20 dark:text-slate-400">
      {inline(body.join(' '), `bq${keyBase}`)}
    </blockquote>
  );
  return { node, next: i };
}

/** Unordered or ordered, with indented-continuation-line folding. */
function parseList(lines, i) {
  const bullet = /^[-*]\s+/;
  const numbered = /^\d+\.\s+/;
  const line = lines[i];
  if (!bullet.test(line) && !numbered.test(line)) return null;
  const ordered = numbered.test(line);
  const items = [];
  const re = ordered ? numbered : bullet;
  while (i < lines.length && re.test(lines[i])) {
    let text = lines[i].replace(re, '');
    i++;
    // Fold continuation lines (indented) into the same item.
    while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i].trim())) {
      text += ` ${lines[i].trim()}`;
      i++;
    }
    items.push(text);
  }
  const Tag = ordered ? 'ol' : 'ul';
  const node = (
    <Tag className={`my-3 space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300 ${ordered ? 'list-decimal' : 'list-disc'}`}>
      {items.map((t, n) => <li key={n} className="leading-relaxed marker:text-slate-400">{inline(t, `li${n}`)}</li>)} {/* NOSONAR */}
    </Tag>
  );
  return { node, next: i };
}

/** Consumes until a blank line or a new block starts — the fallback when
 * nothing more specific matched. */
function parseParagraph(lines, i, keyBase) {
  const para = [];
  while (
    i < lines.length && lines[i].trim()
    && !/^(#{1,4}\s|```|>\s|[-*]\s|\d+\.\s)/.test(lines[i])
    && !lines[i].trim().startsWith('|')
  ) {
    para.push(lines[i++]);
  }
  if (!para.length) return { node: null, next: i };
  const node = <p className="my-3 text-sm leading-relaxed text-slate-700 dark:text-slate-300">{inline(para.join(' '), `p${keyBase}`)}</p>;
  return { node, next: i };
}

export function Markdown({ source }) {
  const lines = String(source || '').split('\n');
  const out = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    if (!lines[i].trim()) { i++; continue; }

    const block =
      parseFencedCode(lines, i)
      || parseHeading(lines, i, key)
      || parseHorizontalRule(lines, i)
      || parseTable(lines, i)
      || parseBlockquote(lines, i, key)
      || parseList(lines, i)
      || parseParagraph(lines, i, key);

    i = block.next;
    if (block.node) out.push(<div key={key++}>{block.node}</div>);
  }

  return <div className="max-w-3xl">{out}</div>;
}
