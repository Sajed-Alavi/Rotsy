/**
 * Access-rule editor for one role.
 *
 * Rules are persisted immediately (they need the role id, and each one is
 * independently meaningful) rather than being batched into the role's Save —
 * the same split the old image-scope list used.
 *
 * Deny rules render first with a rose edge, because that is the order the
 * engine reads them in: within a role, a deny beats an allow.
 */
import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';

const INPUT =
  'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

const ACTIONS = ['read', 'scan', 'delete'];
const ACTION_HINTS = {
  read: 'See the image, its assets and its scan reports',
  scan: 'Trigger scans and manage scanning for it',
  delete: 'Delete its tags, reports and retention output',
};

const EMPTY_RULE = { effect: 'allow', repo_pattern: '*', image_pattern: '**', actions: ['read'], description: '' };

/** Rules sorted the way they are evaluated: denies first, then by pattern. */
function ordered(rules) {
  return [...rules].sort((a, b) => {
    if (a.effect !== b.effect) return a.effect === 'deny' ? -1 : 1;
    return (a.repo_pattern + a.image_pattern).localeCompare(b.repo_pattern + b.image_pattern);
  });
}

export default function AccessRulesEditor({ roleId, repos, onChanged }) {
  const [rules, setRules] = useState([]);
  const [draft, setDraft] = useState(EMPTY_RULE);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () =>
    api.get(`/roles/${roleId}/access-rules`).then(setRules).catch((err) => setError(err.message));

  useEffect(() => { load(); }, [roleId]);

  const announce = (next) => {
    setRules(next);
    onChanged?.(next);
  };

  const add = async () => {
    setError('');
    setBusy(true);
    try {
      const created = await api.post(`/roles/${roleId}/access-rules`, draft);
      announce([...rules, created]);
      setDraft(EMPTY_RULE);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const patch = async (rule, changes) => {
    setError('');
    try {
      const updated = await api.patch(`/roles/${roleId}/access-rules/${rule.id}`, changes);
      announce(rules.map((r) => (r.id === rule.id ? updated : r)));
    } catch (err) {
      setError(err.message);
    }
  };

  const remove = async (rule) => {
    setError('');
    try {
      await api.delete(`/roles/${roleId}/access-rules/${rule.id}`);
      announce(rules.filter((r) => r.id !== rule.id));
    } catch (err) {
      setError(err.message);
    }
  };

  const toggleAction = (rule, action) => {
    const next = rule.actions.includes(action)
      ? rule.actions.filter((a) => a !== action)
      : [...rule.actions, action];
    if (next.length === 0) {
      setError('A rule must grant at least one action. Delete it instead.');
      return;
    }
    patch(rule, { actions: next });
  };

  return (
    <div className="space-y-3">
      {rules.length === 0 ? (
        <div className="border border-dashed border-slate-300 px-3 py-4 text-center font-mono text-[11px] text-slate-400 dark:border-slate-700 dark:text-slate-600">
          No rules. This role falls back to its access mode everywhere.
        </div>
      ) : (
        <ul className="space-y-1.5">
          {ordered(rules).map((rule) => (
            <li
              key={rule.id}
              className={`border px-2.5 py-2 ${
                rule.effect === 'deny'
                  ? 'border-l-2 border-l-rose-400 border-rose-200 bg-rose-50/40 dark:border-rose-900 dark:border-l-rose-500 dark:bg-rose-950/20'
                  : 'border-slate-200 dark:border-slate-800'
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  title="Switch between allow and deny"
                  onClick={() => patch(rule, { effect: rule.effect === 'allow' ? 'deny' : 'allow' })}
                >
                  <Badge tone={rule.effect === 'deny' ? 'bad' : 'ok'}>{rule.effect}</Badge>
                </button>
                <span className="font-mono text-xs text-slate-700 dark:text-slate-300">
                  {rule.repo_pattern}
                  <span className="text-slate-400 dark:text-slate-600"> / </span>
                  {rule.image_pattern}
                </span>
                <div className="ml-auto flex items-center gap-2.5">
                  {ACTIONS.map((action) => (
                    <label
                      key={action}
                      title={ACTION_HINTS[action]}
                      className="flex cursor-pointer items-center gap-1 font-mono text-[10px] uppercase text-slate-600 dark:text-slate-400"
                    >
                      <input
                        type="checkbox"
                        checked={rule.actions.includes(action)}
                        onChange={() => toggleAction(rule, action)}
                        className="accent-sky-500"
                      />
                      {action}
                    </label>
                  ))}
                  <button
                    type="button"
                    onClick={() => remove(rule)}
                    className="border border-rose-200 px-1.5 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
                  >
                    remove
                  </button>
                </div>
              </div>
              {rule.description && (
                <div className="mt-1 font-mono text-[10px] text-slate-400 dark:text-slate-600">
                  {rule.description}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
        <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
          Add a rule
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-[7rem_1fr_1fr]">
          <select
            value={draft.effect}
            onChange={(e) => setDraft({ ...draft, effect: e.target.value })}
            className={INPUT}
          >
            <option value="allow">allow</option>
            <option value="deny">deny</option>
          </select>
          <PatternField
            value={draft.repo_pattern}
            onChange={(v) => setDraft({ ...draft, repo_pattern: v })}
            placeholder="repository — * or prod-*"
            candidates={repos.map((r) => r.name)}
          />
          <PatternField
            value={draft.image_pattern}
            onChange={(v) => setDraft({ ...draft, image_pattern: v })}
            placeholder="image — abrisham* or team/**"
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {ACTIONS.map((action) => (
            <label
              key={action}
              title={ACTION_HINTS[action]}
              className="flex cursor-pointer items-center gap-1 font-mono text-[10px] uppercase text-slate-600 dark:text-slate-400"
            >
              <input
                type="checkbox"
                checked={draft.actions.includes(action)}
                onChange={() =>
                  setDraft({
                    ...draft,
                    actions: draft.actions.includes(action)
                      ? draft.actions.filter((a) => a !== action)
                      : [...draft.actions, action],
                  })
                }
                className="accent-sky-500"
              />
              {action}
            </label>
          ))}
          <input
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            placeholder="why this rule exists (optional)"
            className={`${INPUT} flex-1`}
          />
          <button
            type="button"
            onClick={add}
            disabled={busy || draft.actions.length === 0}
            className="flex items-center gap-1 whitespace-nowrap border border-slate-300 px-2.5 py-1.5 font-mono text-xs uppercase text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <Icon name="plus" size={12} /> add
          </button>
        </div>
      </div>

      {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
    </div>
  );
}

/**
 * A pattern input that, when given a candidate list, shows live which of them
 * the pattern currently matches. Wildcards are only safe to write when their
 * blast radius is visible before saving.
 */
function PatternField({ value, onChange, placeholder, candidates }) {
  const hits = candidates ? candidates.filter((name) => antMatch(value, name)) : null;
  return (
    <div>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={INPUT} />
      {hits && value && (
        <div className="mt-1 font-mono text-[10px] text-slate-400 dark:text-slate-600">
          {hits.length === 0
            ? 'matches no repository that exists today'
            : `matches ${hits.length}: ${hits.slice(0, 4).join(', ')}${hits.length > 4 ? '…' : ''}`}
        </div>
      )}
    </div>
  );
}

/**
 * Ant-style glob matching, mirroring `app.core.access_control.compile_pattern`.
 *
 * Only ever used for the preview above — every real access decision is made by
 * the backend. Kept deliberately tiny and in lockstep with the Python version:
 * `**` crosses `/`, `*` and `?` do not, and the match is anchored.
 */
export function antMatch(pattern, value) {
  if (!pattern) return false;
  const source = pattern.replace(/\*\*|[*?]|[.+^${}()|[\]\\/-]/g, (token) => {
    if (token === '**') return '[\\s\\S]*';
    if (token === '*') return '[^/]*';
    if (token === '?') return '[^/]';
    return `\\${token}`;
  });
  try {
    return new RegExp(`^(?:${source})$`).test(value);
  } catch {
    return false;
  }
}
