import { useNavigate } from 'react-router-dom';
import Icon from './Icon.jsx';
import { useAuth } from '../context/AuthContext.jsx';
import { useTheme } from '../context/ThemeContext.jsx';

/** Thin top bar: brand on the left, theme toggle + user menu on the right. */
export default function TopBar() {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <header className="flex h-12 items-center justify-between border-b border-slate-200 bg-white px-4 dark:border-slate-800 dark:bg-slate-950">
      <div className="font-mono text-xs text-slate-400 dark:text-slate-600">sonatype nexus · advanced console</div>
      <div className="flex items-center gap-2">
        <button
          onClick={toggleTheme}
          className="flex h-7 w-7 items-center justify-center rounded border border-slate-200 text-slate-500 transition-colors hover:text-slate-900 dark:border-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
          title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={14} />
        </button>
        {user && (
          <>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-slate-700 dark:text-slate-300">{user.username}</span>
              <span className="font-mono text-[10px] uppercase text-slate-400 dark:text-slate-600">
                {user.roles.map((r) => r.name).join(', ')}
              </span>
            </div>
            <button
              onClick={handleLogout}
              className="flex h-7 w-7 items-center justify-center rounded border border-slate-200 text-slate-500 transition-colors hover:text-slate-900 dark:border-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
              title="Sign out"
            >
              <Icon name="logout" size={13} />
            </button>
          </>
        )}
      </div>
    </header>
  );
}
