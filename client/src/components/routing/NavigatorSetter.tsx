import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { setNavigator } from "@/lib/router";

/**
 * Bridges React Router's navigate into a global helper usable outside React.
 * Mount this once near the top of the app (inside BrowserRouter).
 */
export default function NavigatorSetter() {
  const navigate = useNavigate();

  useEffect(() => {
    setNavigator((path, options) => {
      navigate(path, {
        replace: options?.replace,
        state: options?.state,
      });
    });
  }, [navigate]);

  return null;
}
