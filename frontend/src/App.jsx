import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import Login from "./Pages/Login";
import Dashboard from "./Pages/Dashboard";
import Jobs from "./Pages/Jobs";
import CreateJobs from "./Pages/CreateJobs";
import JobCandidates from "./Pages/JobCandidates";
import AuthCallback from "./Pages/AuthCallback";
import LandingPage from "./Pages/LandingPage";
import { useEffect } from "react";
import { saveToken } from "./services/auth";
import ProtectedRoute from "./components/ProtectedRoute";
import CandidateDetails from "./Pages/CandidateDetails";
import DailyUpdate from "./Pages/DailyUpdate";
import DailyReportsHistory from "./Pages/DailyReportsHistory";
import CentralizedCandidates from "./Pages/CentralizedCandidates";
import MockCandidateDetails from "./Pages/MockCandidateDetails";
function App() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");

    if (token) {
      saveToken(token);
      window.location.href = "/dashboard";
    }
  }, []);

  return (
    <BrowserRouter>
      <Toaster
        position="top-center"
        richColors
        closeButton
        duration={3500}
        visibleToasts={3}
        expand={false}
      />
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<Login />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/auth/success" element={<AuthCallback />} />
        <Route path="/callback" element={<AuthCallback />} />

        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />

        <Route
          path="/jobs"
          element={
            <ProtectedRoute>
              <Jobs />
            </ProtectedRoute>
          }
        />

        <Route
          path="/jobs/create"
          element={
            <ProtectedRoute>
              <CreateJobs />
            </ProtectedRoute>
          }
        />

        <Route
          path="/jobs/:id"
          element={
            <ProtectedRoute>
              <JobCandidates />
            </ProtectedRoute>
          }
        />

        <Route
          path="/jobs/:id/candidate/:candidateId"
          element={
            <ProtectedRoute>
              <CandidateDetails />
            </ProtectedRoute>
          }
        />

        <Route
          path="/daily-update"
          element={
            <ProtectedRoute>
              <DailyUpdate />
            </ProtectedRoute>
          }
        />

        <Route
          path="/daily-update/history"
          element={
            <ProtectedRoute>
              <DailyReportsHistory />
            </ProtectedRoute>
          }
        />

        <Route
          path="/centralized-candidates"
          element={
            <ProtectedRoute>
              <CentralizedCandidates />
            </ProtectedRoute>
          }
        />

        <Route
          path="/centralized-candidates/:candidateId"
          element={
            <ProtectedRoute>
              <MockCandidateDetails />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;