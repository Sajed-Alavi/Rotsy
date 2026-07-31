import { useState } from 'react';
import { NavLink, Outlet } from 'react-router';
import Icon from '../../components/Icon.jsx';
import { DOC_SECTIONS, DOCS } from './registry.js';

/**
 * Documentation shell: a searchable contents rail plus the current page.
 *
 * The rail is always present rather than being a landing page you navigate away
 * from — moving between related topics is the common case when you are learning
 * something, and back-and-forth through a menu makes that tedious.
 */
export default function DocsLayout() {
  const [query, setQuery] = useState('');

  const q = query.trim().toLowerCase();
  const sections = q
    ? DOC_SECTIONS.map((s) => ({
      ...s,
      docs: s.docs.filter((d) =>
        d.title.toLowerCase().includes(q)
        || d.summary.toLowerCase().includes(q)
        || d.source.toLowerCase().includes(q)),
    })).filter((s) => s.docs.length)
    : DOC_SECTIONS;

  return (
    <div className="flex h-full">
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-slate-200 p-4 dark:border-slate-800">
        <div className="mb-3">
          <h1 className="text-sm font-medium text-slate-900 dark:text-slate-100">Documentation</h1>
          <p className="mt-0.5 font-mono text-[10px] text-slate-500">{DOCS.length} pages · beginner to advanced</p>
        </div>

        <div className="relative mb-4">
          <Icon name="search" size={13} className="absolute left-2 top-2 text-slate-400 dark:text-slate-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="search docs…"
            className="w-full border border-slate-300 bg-white py-1.5 pl-7 pr-2 font-mono text-[11px] text-slate-800 focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          />
        </div>

        {sections.length === 0 && (
          <p className="font-mono text-[11px] text-slate-400 dark:text-slate-600">nothing matches "{query}"</p>
        )}

        {sections.map((section) => (
          <div key={section.key} className="mb-4">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
              {section.label}
            </div>
            {section.docs.map((doc) => (
              <NavLink
                key={doc.slug}
                to={`/docs/${doc.slug}`}
                className={({ isActive }) =>
                  `block border-l py-1 pl-2 text-[13px] transition-colors ${
                    isActive
                      ? 'border-sky-500 text-sky-700 dark:text-sky-300'
                      : 'border-slate-200 text-slate-600 hover:border-slate-400 hover:text-slate-900 dark:border-slate-800 dark:text-slate-400 dark:hover:text-slate-100'
                  }`
                }
              >
                {doc.title}
              </NavLink>
            ))}
          </div>
        ))}
      </aside>

      <div className="flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
