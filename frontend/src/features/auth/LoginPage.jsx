import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';

/** Minimal login screen, theme-aware. Redirects to / once authenticated. */
export default function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const onSubmit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await login(username, password);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 dark:bg-slate-950">
      <form onSubmit={onSubmit} className="w-full max-w-xs border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900/60">
        <div className="mb-5 flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-400" />
          <span className="font-mono text-xs tracking-tight text-slate-600 dark:text-slate-300">sharpy</span>
        </div>

        <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">
          Username
        </label>
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          autoComplete="username"
          className="mb-4 w-full border border-slate-300 bg-white px-3 py-2 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        />

        <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          className="mb-4 w-full border border-slate-300 bg-white px-3 py-2 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        />

        {error && <div className="mb-4 font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}

        <button
          type="submit"
          disabled={busy}
          className="w-full border border-slate-300 bg-slate-100 py-2 font-mono text-xs uppercase tracking-wider text-slate-800 transition-colors hover:bg-slate-200 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        >
          {busy ? '···' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
