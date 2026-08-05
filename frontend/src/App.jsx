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
import ErrorBoundary from './components/ErrorBoundary'

const ROLE_ROUTES = { superadmin: '/superadmin', admin: '/admin', hr: '/hr', interviewer: '/interviewer', candidate: '/candidate' }

// A visitor who arrived through a company job board / portal has the company's
// API key stored. Such users must never be bounced to the Innomax platform
// login — keep them inside their company's context.
function getCompanyKey() {
  try { return localStorage.getItem('hireflow_company_key') } catch { return null }
}

function ProtectedRoute({ children, allowedRoles }) {
  const { isAuthenticated, user } = useAuth()
  if (!isAuthenticated) {
    const key = getCompanyKey()
    // Needs auth → send to the company's own login, not Innomax's.
    return <Navigate to={key ? `/app?key=${encodeURIComponent(key)}` : '/login'} replace />
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

  return (
    <ErrorBoundary>
    <Routes>
      {/* Landing — the Innomax hero page. Only platform (Innomax) visitors land
          here. A company visitor (one carrying a company key) is sent to their
          own career page instead, never the Innomax hero. */}
      <Route path="/" element={
        isAuthenticated
          ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace />
          : getCompanyKey()
            ? <Navigate to={`/jobs?key=${encodeURIComponent(getCompanyKey())}`} replace />
            : <LandingPage />
      } />

      {/* Public */}
      <Route path="/login" element={
        isAuthenticated
          ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace />
          : getCompanyKey()
            ? <Navigate to={`/jobs?key=${encodeURIComponent(getCompanyKey())}`} replace />
            : <Login />
      } />
      <Route path="/register" element={isAuthenticated ? <Navigate to={ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate'} replace /> : <Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/jobs" element={<JobBoard />} />
      <Route path="/portal" element={<CompanyPortal />} />
      <Route path="/app" element={<CompanyLogin />} />

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

      {/* HR */}
      <Route path="/hr/*" element={
        <ProtectedRoute allowedRoles={['hr']}>
          <DashboardShell><HRDashboard /></DashboardShell>
        </ProtectedRoute>
      } />

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

      {/* Candidate */}
      <Route path="/candidate/*" element={
        <ProtectedRoute allowedRoles={['candidate']}>
          <DashboardShell><CandidateDashboard /></DashboardShell>
        </ProtectedRoute>
      } />

      {/* Default redirect */}
      <Route path="*" element={
        <Navigate to={
          isAuthenticated
            ? (ROLE_ROUTES[user?.role?.toLowerCase()] || '/candidate')
            : (getCompanyKey() ? `/jobs?key=${encodeURIComponent(getCompanyKey())}` : '/login')
        } replace />
      } />
    </Routes>
    </ErrorBoundary>
  )
}
