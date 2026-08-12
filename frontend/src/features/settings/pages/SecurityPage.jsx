import { Link } from 'react-router';
import { useAuth } from '../../../context/AuthContext.jsx';
import Icon from '../../../components/Icon.jsx';

const ROW = 'flex items-center justify-between border-t border-slate-100 px-4 py-3 first:border-t-0 dark:border-slate-800/60';

/**
 * Security surfaces links out to the existing Access & Webhooks section
 * (API tokens, Nexus webhooks, anonymous access) and Role management rather
 * than re-implementing them here — those already have their own full pages
 * with their own permission checks; duplicating the forms would just be two
 * places that can drift out of sync.
 */
export default function SecurityPage() {
  const { hasPermission } = useAuth();

  return (
    <div className="mx-auto grid max-w-3xl grid-cols-1 gap-6">
      <section className="border border-slate-200 dark:border-slate-800">
        <div className="p-4">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Access control</h2>
          <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            API tokens, Nexus webhooks, and anonymous access.
          </p>
        </div>
        {hasPermission('access:read') && (
          <>
            <SecurityLink to="/access" label="API Tokens" />
            <SecurityLink to="/access/webhooks" label="Webhooks" />
            <SecurityLink to="/access/anonymous" label="Anonymous Access" />
          </>
        )}
        {!hasPermission('access:read') && (
          <div className={ROW}><span className="font-mono text-xs text-slate-400 dark:text-slate-600">Requires access:read permission.</span></div>
        )}
      </section>

      {hasPermission('roles:manage') && (
        <section className="border border-slate-200 dark:border-slate-800">
          <div className="p-4">
            <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Users & roles</h2>
            <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
              Accounts, roles, and permission assignment.
            </p>
          </div>
          <SecurityLink to="/settings/users" label="Users" />
          <SecurityLink to="/settings/roles" label="Roles & Permissions" />
          <SecurityLink to="/audit" label="Audit Log" />
        </section>
      )}
    </div>
  );
}

function SecurityLink({ to, label }) {
  return (
    <Link to={to} className={`${ROW} group text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-900/60`}>
      <span className="font-mono text-xs">{label}</span>
      <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}
