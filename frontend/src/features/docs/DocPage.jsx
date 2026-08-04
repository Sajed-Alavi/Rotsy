import { Link, useParams } from 'react-router';
import Icon from '../../components/Icon.jsx';
import { Markdown, outline } from './markdown.jsx';
import { DOCS, DOC_SECTIONS, getDoc } from './registry.js';

/**
 * One documentation page, plus its in-page outline and prev/next links.
 *
 * With no slug this renders the landing index instead of redirecting, so /docs
 * is a real destination rather than a bounce to whichever page happens to sort
 * first.
 */
export default function DocPage() {
  const { slug } = useParams();

  if (!slug) return <DocsIndex />;

  const doc = getDoc(slug);
  if (!doc) {
    return (
      <div className="p-8">
        <h1 className="mb-2 text-base font-medium text-slate-900 dark:text-slate-100">Page not found</h1>
        <p className="mb-4 font-mono text-[11px] text-slate-500">No documentation page called "{slug}".</p>
        <Link to="/docs" className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          All documentation
        </Link>
      </div>
    );
  }

  const index = DOCS.findIndex((d) => d.slug === slug);
  const prev = index > 0 ? DOCS[index - 1] : null;
  const next = index < DOCS.length - 1 ? DOCS[index + 1] : null;
  const headings = outline(doc.source);

  return (
    <div className="flex">
      <article className="min-w-0 flex-1 p-8">
        <Markdown source={doc.source} />

        <nav className="mt-10 flex max-w-3xl items-stretch justify-between gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
          {prev ? (
            <Link to={`/docs/${prev.slug}`} className="group flex-1 border border-slate-200 p-3 hover:border-sky-300 dark:border-slate-800 dark:hover:border-sky-700">
              <span className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
                <Icon name="chevron" size={11} className="rotate-180" /> Previous
              </span>
              <span className="mt-1 block text-sm text-slate-700 group-hover:text-sky-700 dark:text-slate-300 dark:group-hover:text-sky-300">{prev.title}</span>
            </Link>
          ) : <div className="flex-1" />}
          {next ? (
            <Link to={`/docs/${next.slug}`} className="group flex-1 border border-slate-200 p-3 text-right hover:border-sky-300 dark:border-slate-800 dark:hover:border-sky-700">
              <span className="flex items-center justify-end gap-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
                Next <Icon name="chevron" size={11} />
              </span>
              <span className="mt-1 block text-sm text-slate-700 group-hover:text-sky-700 dark:text-slate-300 dark:group-hover:text-sky-300">{next.title}</span>
            </Link>
          ) : <div className="flex-1" />}
        </nav>
      </article>

      {headings.length > 2 && (
        <aside className="hidden w-52 shrink-0 p-8 pl-0 xl:block">
          <div className="sticky top-8">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">On this page</div>
            {headings.map((h) => (
              <a
                key={h.id}
                href={`#${h.id}`}
                className={`block py-0.5 text-[12px] text-slate-500 hover:text-sky-600 dark:text-slate-400 dark:hover:text-sky-400 ${h.level === 3 ? 'pl-3' : ''}`}
              >
                {h.text}
              </a>
            ))}
          </div>
        </aside>
      )}
    </div>
  );
}

function DocsIndex() {
  return (
    <div className="p-8">
      <h1 className="mb-1 text-xl font-medium text-slate-900 dark:text-slate-100">Rotsy documentation</h1>
      <p className="mb-8 max-w-2xl text-sm text-slate-600 dark:text-slate-400">
        Everything this tool does, from first login to air-gapped deployment. The sections below are
        ordered as a learning path — read them in order the first time, or jump straight to what you
        need.
      </p>

      {DOC_SECTIONS.map((section) => (
        <section key={section.key} className="mb-8">
          <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">{section.label}</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {section.docs.map((doc) => (
              <Link
                key={doc.slug}
                to={`/docs/${doc.slug}`}
                className="group border border-slate-200 p-3 transition-colors hover:border-sky-300 dark:border-slate-800 dark:hover:border-sky-700"
              >
                <span className="block text-sm text-slate-800 group-hover:text-sky-700 dark:text-slate-200 dark:group-hover:text-sky-300">
                  {doc.title}
                </span>
                {doc.summary && (
                  <span className="mt-1 block font-mono text-[10px] leading-relaxed text-slate-500 dark:text-slate-500">
                    {doc.summary}
                  </span>
                )}
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
