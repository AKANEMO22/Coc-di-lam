import React, { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppProvider, useApp } from "./context/AppContext";
import Toaster from "./components/Toaster";

import Login from "./pages/Login";
import Home from "./pages/Home";
import Jobs from "./pages/Jobs";
import Results from "./pages/Results";
import CVLibrary from "./pages/CVLibrary";
import PrepareInterview from "./pages/PrepareInterview";

// The interview room (avatar, audio, recorder) is heavy and only needed once a session starts.
const GatewayInterview = lazy(() => import("./pages/GatewayInterview"));
const InterviewerPreview = lazy(() => import("./pages/InterviewerPreview"));

function ProtectedRoute({ children }) {
  const { user } = useApp();
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function RouteLoading() {
  return (
    <div className="route-loading" role="status" aria-live="polite">
      <span className="route-loading-dot" />
      Đang tải phòng phỏng vấn...
    </div>
  );
}

// A lazy page whose chunk still fails after the one-shot reload (main.jsx) would otherwise unmount the
// whole app: show a retry instead. Navigating to another path clears it.
class RouteErrorBoundary extends React.Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous) {
    if (this.state.failed && previous.path !== this.props.path) this.setState({ failed: false });
  }

  componentDidCatch(error) {
    console.error("Route failed to load", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="route-loading" role="alert">
        Không tải được trang này. Kiểm tra kết nối mạng rồi thử lại.
        <button type="button" className="route-retry" onClick={() => window.location.reload()}>
          Tải lại
        </button>
      </div>
    );
  }
}

const guarded = element => <ProtectedRoute>{element}</ProtectedRoute>;

function AppRoutes() {
  const location = useLocation();
  const { user } = useApp();
  const fallback = <Navigate to={user ? "/home" : "/login"} replace />;

  // No page-wide zoom/fade between tabs: the sidebar and background stay put, and only the page
  // content fades in (CSS .workspace-content), so switching tabs feels instant.
  return (
    <div className="route-screen">
      <RouteErrorBoundary path={location.pathname}>
      <Suspense fallback={<RouteLoading />}>
        <Routes location={location}>
          <Route path="/interviewer-preview" element={<InterviewerPreview />} />
          <Route path="/" element={fallback} />
          <Route path="/login" element={user ? <Navigate to="/home" replace /> : <Login />} />
          <Route path="/home" element={guarded(<Home />)} />
          <Route path="/jobs" element={guarded(<Jobs />)} />
          <Route path="/cv-library" element={guarded(<CVLibrary />)} />
          <Route path="/prepare-interview" element={guarded(<PrepareInterview />)} />
          <Route path="/interview/:sessionId" element={guarded(<GatewayInterview />)} />
          <Route path="/interview" element={guarded(<Navigate to="/prepare-interview" replace />)} />
          <Route path="/results" element={guarded(<Results />)} />
          <Route path="*" element={fallback} />
        </Routes>
      </Suspense>
      </RouteErrorBoundary>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <AppRoutes />
      <Toaster />
    </AppProvider>
  );
}
