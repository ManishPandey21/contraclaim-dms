import { useAuth } from "@/hooks/use-auth";
import { Outlet } from "react-router-dom";
import LoginPage from "@/pages/LoginPage";

// Protected Route component
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    // Render LoginPage in-place to keep URL at "/" per requirement
    return <LoginPage />;
  }

  return children ? children : <Outlet />;
};

export default ProtectedRoute;
