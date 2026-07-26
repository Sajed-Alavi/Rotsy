import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';

/**
 * Route guard. While the initial /auth/me probe is in flight we render nothing
 * (avoids a flash of the login page for an already-authed user). If the probe
 * finishes and there is no user, redirect to /login.
 */
export default function ProtectedRoute() {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}
