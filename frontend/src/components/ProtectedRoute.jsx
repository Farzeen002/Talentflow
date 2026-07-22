import { Navigate } from "react-router-dom";
import { isAuthenticated } from "../services/auth";
import AppLayout from "./AppLayout";

const ProtectedRoute = ({ children }) => {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return <AppLayout>{children}</AppLayout>;
};

export default ProtectedRoute;