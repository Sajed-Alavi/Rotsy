import { Outlet } from 'react-router';
import Sidebar from './Sidebar.jsx';
import TopBar from './TopBar.jsx';

/** App shell: sidebar + topbar + scrollable content area. Theme-aware. */
export default function AppShell() {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
