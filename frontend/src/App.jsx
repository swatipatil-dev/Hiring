import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Register from './pages/Register'
import ForgotPassword from './pages/ForgotPassword'
import JobBoard from './pages/public/JobBoard'
import CompanyPortal from './pages/public/CompanyPortal'
import CompanyLogin from './pages/public/CompanyLogin'
import LandingPage from './pages/LandingPage'
import AdminDashboard from './pages/admin/AdminDashboard'
import AdminLogin from './pages/admin/AdminLogin'
import SuperAdminDashboard from './pages/superadmin/SuperAdminDashboard'
import DepartmentPage from './pages/superadmin/DepartmentPage'
import SupportTicketsPage from './pages/superadmin/SupportTicketsPage'
import HRDashboard from './pages/hr/HRDashboard'
import InterviewerDashboard from './pages/interviewer/InterviewerDashboard'
import CandidateDashboard from './pages/candidate/CandidateDashboard'
import OnlineTest from './pages/candidate/OnlineTest'
import TestInstructions from './pages/candidate/TestInstructions'
import MyProfile from './pages/shared/MyProfile'
import ErrorBoundary from './components/ErrorBoundary'
import { getStoredSlug } from './utils/tenant'

const ROLE_ROUTES = { superadmin: '/superadmin', admin: '/admin', hr: '/hr', interviewer: '/interviewer', candidate: '/candidate' }

// A visitor who arrived through a company job board / portal has that company's slug
// stored. Such users must never be bounced to the Innomax platform login — keep them
// inside their company's context.
//
// This used to read the company's `sk_live_` API key out of localStorage and paste it
// back into every redirect below, which is how a secret meant for one link ended up in
// the address bar of anyone who ever signed in. The slug is public, so it can travel.
const companyPath = (slug, sub = '') => `/${encodeURIComponent(slug)}${sub}`

function ProtectedRoute({ children, allowedRoles }) {
  const { isAuthenticated, user } = useAuth()
  if (!isAuthenticated) {
    const slug = getStoredSlug()
    // Needs auth → send to the company's own login, not Innomax's.
    return <Navigate to={slug ? companyPath(slug) : '/login'} replace />
  }
  if (allowedRoles && !allowedRoles.includes(user?.role?.toLowerCase())) {
    return <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/login'} replace />
  }
  return children
}

function DashboardShell({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">{children}</main>
    </div>
  )
}

export default function App() {
  const { isAuthenticated, user } = useAuth()
  const location = useLocation()
  const isAuth = ['/login', '/register'].includes(location.pathname)

  // Each is mounted on two paths below (the wildcard plus one path a company slug
  // route would otherwise capture); one element keeps them identical.
  const hrPage = (
    <ProtectedRoute allowedRoles={['hr']}>
      <DashboardShell><HRDashboard /></DashboardShell>
    </ProtectedRoute>
  )
  const candidatePage = (
    <ProtectedRoute allowedRoles={['candidate']}>
      <DashboardShell><CandidateDashboard /></DashboardShell>
    </ProtectedRoute>
  )

  return (
    <ErrorBoundary>
    <Routes>
      {/* Landing — the Innomax hero page.
          This and /login below are the platform's own front door, and they stay that way
          no matter which company portals the browser has visited. They used to redirect a
          visitor carrying a company slug to that company's job board, because a company
          had no URL of its own — a key in a query string is not something you can type or
          bookmark, so the platform root doubled as their entrance. Every company now has
          /<slug>, so that hack is obsolete, and leaving it in meant one visit to any
          client's careers page permanently took career.innomaxskills.com away from
          whoever made it — staff included. Do not put it back. */}
      <Route path="/" element={
        isAuthenticated
          ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace />
          : <LandingPage />
      } />

      {/* Public */}
      <Route path="/login" element={
        isAuthenticated
          ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace />
          : <Login />
      } />
      <Route path="/register" element={isAuthenticated ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace /> : <Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      {/* Legacy shapes. These carried ?key=<sk_live_…>; the pages now exchange that
          key for a slug on mount and rewrite the URL. Remove with the ?key= support. */}
      <Route path="/jobs" element={<JobBoard />} />
      <Route path="/portal" element={<CompanyPortal />} />
      <Route path="/app" element={<CompanyLogin />} />

      {/* Shared — every authenticated role. Declared at the top level, not under
          a role wildcard: /superadmin/*, /admin/*, /hr/*, /interviewer/* and
          /candidate/* are each role-locked and would bounce the other four.
          No allowedRoles → any signed-in user. */}
      <Route path="/profile" element={
        <ProtectedRoute>
          <DashboardShell><MyProfile /></DashboardShell>
        </ProtectedRoute>
      } />

      {/* Super Admin — departments gets its own route so sidebar stays */}
      <Route path="/superadmin/departments" element={
        <ProtectedRoute allowedRoles={['superadmin']}>
          <DashboardShell><DepartmentPage /></DashboardShell>
        </ProtectedRoute>
      } />
      <Route path="/superadmin/support" element={
        <ProtectedRoute allowedRoles={['superadmin']}>
          <DashboardShell><SupportTicketsPage /></DashboardShell>
        </ProtectedRoute>
      } />
      <Route path="/superadmin/*" element={
        <ProtectedRoute allowedRoles={['superadmin']}>
          <DashboardShell><SuperAdminDashboard /></DashboardShell>
        </ProtectedRoute>
      } />

      {/* Admin login — direct client access, no superadmin required */}
      <Route path="/admin/login" element={
        <AdminLogin />
      } />

      {/* Admin */}
      <Route path="/admin/*" element={
        <ProtectedRoute allowedRoles={['admin']}>
          <DashboardShell><AdminDashboard /></DashboardShell>
        </ProtectedRoute>
      } />

      {/* HR. /hr/jobs is spelled out because React Router ranks /:companySlug/jobs
          (dynamic + static) above /hr/* (static + splat): without it the sidebar's
          Job Postings link rendered the public job board for a company named "hr". */}
      <Route path="/hr/*" element={hrPage} />
      <Route path="/hr/jobs" element={hrPage} />

      {/* Interviewer */}
      <Route path="/interviewer/*" element={
        <ProtectedRoute allowedRoles={['interviewer']}>
          <DashboardShell><InterviewerDashboard /></DashboardShell>
        </ProtectedRoute>
      } />

      {/* Test Instructions — full screen */}
      <Route path="/test/:interviewId" element={
        <ProtectedRoute allowedRoles={['candidate']}>
          <TestInstructions />
        </ProtectedRoute>
      } />

      {/* Online Test Exam — full screen */}
      <Route path="/test/:interviewId/exam" element={
        <ProtectedRoute allowedRoles={['candidate']}>
          <OnlineTest />
        </ProtectedRoute>
      } />

      {/* Candidate. /candidate/jobs spelled out for the same reason as /hr/jobs. */}
      <Route path="/candidate/*" element={candidatePage} />
      <Route path="/candidate/jobs" element={candidatePage} />

      {/* Brand-name company URLs: career.innomaxskills.com/<company-slug>.
          Declared last, but React Router v6 ranks static segments above dynamic ones, so
          /login and friends still win. What actually keeps a company from claiming one of
          those names is RESERVED_SLUGS in backend/src/tenancy/slug.py — add any new
          top-level route there too. An unknown slug renders CompanyLogin's "not found"
          state rather than the catch-all below, which is why it is a real route. */}
      <Route path="/:companySlug" element={<CompanyLogin />} />
      <Route path="/:companySlug/jobs" element={<JobBoard />} />
      <Route path="/:companySlug/portal" element={<CompanyPortal />} />
      <Route path="/:companySlug/forgot-password" element={<ForgotPassword />} />

      {/* Default redirect */}
      <Route path="*" element={
        <Navigate to={
          isAuthenticated
            ? (ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate')
            : (getStoredSlug() ? companyPath(getStoredSlug(), '/jobs') : '/login')
        } replace />
      } />
    </Routes>
    </ErrorBoundary>
  )
}
