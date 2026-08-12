import { useState, useEffect } from 'react';
import { api } from '../../../lib/api.js';
import { useAuth } from '../../../context/AuthContext.jsx';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/** General: your account (profile + password). Moved here unchanged from the
 * old single-page Settings — see SettingsLayout for why this is now tabbed. */
export default function GeneralPage() {
  const { refreshMe } = useAuth();
  return (
    <div className="mx-auto grid max-w-3xl grid-cols-1 gap-6">
      <ProfileSection refreshMe={refreshMe} />
      <PasswordSection />
    </div>
  );
}

function ProfileSection({ refreshMe }) {
  const [profile, setProfile] = useState(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => { api.get('/settings/profile').then(setProfile).catch(() => {}); }, []);

  const saveProfile = async (e) => {
    e.preventDefault();
    setErr(''); setMsg('');
    try {
      const updated = await api.patch('/settings/profile', { username: profile.username, email: profile.email });
      setProfile(updated);
      await refreshMe();
      setMsg('Profile updated.');
    } catch (ex) { setErr(ex.message); }
  };

  if (!profile) return null;
  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Profile</h2>
      <form onSubmit={saveProfile} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Username</div>
          <input value={profile.username} onChange={(e) => setProfile({ ...profile, username: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Email</div>
          <input value={profile.email} onChange={(e) => setProfile({ ...profile, email: e.target.value })} className={INPUT} />
        </div>
        <button type="submit" className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">Save profile</button>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </section>
  );
}

function PasswordSection() {
  const [pw, setPw] = useState({ current_password: '', new_password: '' });
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const changePw = async (e) => {
    e.preventDefault();
    setErr(''); setMsg('');
    try {
      await api.post('/settings/password', pw);
      setPw({ current_password: '', new_password: '' });
      setMsg('Password changed.');
    } catch (ex) { setErr(ex.message); }
  };

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Change password</h2>
      <form onSubmit={changePw} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Current password</div>
          <input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">New password</div>
          <input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} className={INPUT} />
        </div>
        <button type="submit" className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Update password</button>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </section>
  );
}
