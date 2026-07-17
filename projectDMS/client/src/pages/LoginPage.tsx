import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import axios from "axios";
import { Eye, EyeOff, LogIn } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useNavigate } from "react-router-dom";
import {
  getCurrentUserProfile,
  loginWithPassword,
  type SessionProfile,
} from "@/services/session-api";
import { extractErrorMessage, logError } from "@/lib/error-logger";

// Define validation schema for login
const loginSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(6, "Password must be at least 6 characters"),
  rememberMe: z.boolean().default(false),
});

type LoginFormValues = z.infer<typeof loginSchema>;

const BACKGROUND_SOURCES = [
  "/1.png",
  "/2.jpg",
  "/3.jpeg",
  "/4.jpg",
  "/New folder/acquisition-contract-management-services.png",
  "/New folder/automated-contracts.jpg",
  "/New folder/Contract-Management.jpeg",
  "/New folder/enhance-business-efficiency-esign-concept-600nw-2491258749.webp",
  "/New folder/what-is-contract-automation-and-why-you-need-i.jpg",
];

const LOGIN_BACKGROUNDS = BACKGROUND_SOURCES.map((p) => encodeURI(p));

const clearLegacyAuthStorage = () => {
  localStorage.removeItem("accessToken");
  localStorage.removeItem("user_id");
  localStorage.removeItem("user_roles");
  localStorage.removeItem("org_id");
  localStorage.removeItem("proj_id");
  localStorage.removeItem("profile_cache");
};

const syncSessionContext = (_profile: SessionProfile) => {
  clearLegacyAuthStorage();

  window.dispatchEvent(new Event("auth-state-changed"));
};

const LoginPage = () => {
  const [showPassword, setShowPassword] = useState(false);
  const [loginError, setLoginError] = useState("");
  const { toast } = useToast();
  const navigate = useNavigate();
  const [bgIndex, setBgIndex] = useState(0);

  useEffect(() => {
    // Preload images
    LOGIN_BACKGROUNDS.forEach((src) => {
      const img = new Image();
      img.src = src;
    });
    const interval = setInterval(() => {
      setBgIndex((prev) => (prev + 1) % LOGIN_BACKGROUNDS.length);
    }, 12000); // change every 12s
    return () => clearInterval(interval);
  }, []);

  // If already authenticated, redirect away from /login to /overview
  useEffect(() => {
    getCurrentUserProfile()
      .then((profile) => {
        syncSessionContext(profile);
        navigate("/overview", { replace: true });
      })
      .catch(() => {
        clearLegacyAuthStorage();
      });
  }, [navigate]);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "",
      password: "",
      rememberMe: false,
    },
  });

  const onSubmit = async (data: LoginFormValues) => {
    setLoginError(""); // Clear previous errors
    let tokenData: Awaited<ReturnType<typeof loginWithPassword>>;
    try {
      tokenData = await loginWithPassword({
        email: data.email,
        password: data.password,
      });

      if (!tokenData?.access_token) {
        throw new Error("Login failed: missing access token.");
      }
    } catch (error: unknown) {
      logError(error, {
        scope: "LoginPage",
        action: "loginWithPassword",
      });

      let message = extractErrorMessage(error, "Login failed");
      if (axios.isAxiosError(error)) {
        if (error.response?.status === 401) {
          message = "Invalid email or password";
        } else if (error.response?.status === 404) {
          message = "Server endpoint not found";
        }
      }

      setLoginError(message);
      return;
    }

    try {
      // Fetch current user profile to synchronize session context and RBAC roles.
      // Authentication is carried by the HttpOnly cookie set by the backend.
      const me = await getCurrentUserProfile();
      syncSessionContext(me);

      if (!me?.roles || (Array.isArray(me.roles) && me.roles.length === 0)) {
        throw new Error("Login succeeded but no roles were returned for this user.");
      }

      toast({
        title: "Login successful",
        description: "Welcome back!",
      });
      navigate("/overview"); // Redirect to Overview page
    } catch (error: unknown) {
      logError(error, {
        scope: "LoginPage",
        action: "getCurrentUserProfile",
      });

      let message = extractErrorMessage(
        error,
        "Login succeeded, but the session could not be verified. Please clear site data for localhost and try again."
      );
      if (axios.isAxiosError(error)) {
        if (error.response?.status === 401) {
          message =
            "Login succeeded, but the browser did not send the session cookie. Clear site data for localhost, then reload and try again.";
        } else if (error.response?.status === 404) {
          message = "Server endpoint not found";
        }
      }

      setLoginError(message);
    }
  };

  const togglePasswordVisibility = () => {
    setShowPassword(!showPassword);
  };

  return (
    <div
      className="relative flex items-center justify-center min-h-screen bg-cover bg-center p-4"
      style={{ backgroundImage: `url(${LOGIN_BACKGROUNDS[bgIndex]})` }}
    >
      <div className="absolute inset-0 bg-black/40" aria-hidden="true"></div>
      <Card className="w-full max-w-md relative z-10">
        <CardHeader className="flex flex-col items-center text-center space-y-3">
          <div className="flex flex-col items-center gap-1">
            <img
              src="/logo.png"
              alt="ContraClaim Logo"
              className="h-24.5 w-auto"
            />
          </div>
          <div className="space-y-1">
            <CardTitle className="text-4xl font-bold">Login</CardTitle>
            <CardDescription>
              Enter your credentials to access your account
            </CardDescription>
          </div>
        </CardHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <CardContent className="space-y-4">
              {loginError && (
                <Alert variant="destructive" role="alert" aria-live="assertive">
                  <AlertDescription>{loginError}</AlertDescription>
                </Alert>
              )}

              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel htmlFor="email" className="text-base">
                      Email
                    </FormLabel>
                    <FormControl>
                      <Input
                        id="email"
                        type="email"
                        placeholder="Enter your email"
                        autoComplete="email"
                        aria-required="true"
                        aria-describedby="email-description"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription id="email-description" className="sr-only">
                      Enter your email address.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel htmlFor="password" className="text-base">
                      Password
                    </FormLabel>
                    <div className="relative">
                      <FormControl>
                        <Input
                          id="password"
                          type={showPassword ? "text" : "password"}
                          placeholder="Enter your password"
                          autoComplete="current-password"
                          aria-required="true"
                          aria-describedby="password-description"
                          className="pr-10"
                          {...field}
                        />
                      </FormControl>
                      <button
                        type="button"
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary rounded-full p-1"
                        onClick={togglePasswordVisibility}
                        aria-label={
                          showPassword ? "Hide password" : "Show password"
                        }
                        aria-controls="password"
                        tabIndex={0}
                      >
                        {showPassword ? (
                          <EyeOff className="h-4 w-4" />
                        ) : (
                          <Eye className="h-4 w-4" />
                        )}
                      </button>
                    </div>
                    <FormDescription
                      id="password-description"
                      className="sr-only"
                    >
                      Your password must be at least 6 characters long.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    id="remember-me"
                    className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                    checked={form.getValues("rememberMe")}
                    {...form.register("rememberMe")}
                  />
                  <Label
                    htmlFor="remember-me"
                    className="text-sm font-medium text-gray-700"
                  >
                    Remember me
                  </Label>
                </div>

                <a
                  href="#"
                  className="text-sm font-medium text-primary hover:text-primary/80"
                >
                  Forgot password?
                </a>
              </div>
            </CardContent>

            <CardFooter>
              <Button
                type="submit"
                className="w-full"
                disabled={form.formState.isSubmitting}
              >
                {form.formState.isSubmitting ? (
                  <span className="flex items-center justify-center">
                    <svg
                      className="animate-spin -ml-1 mr-3 h-5 w-5 text-white"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      ></circle>
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      ></path>
                    </svg>
                    Logging in...
                  </span>
                ) : (
                  <span className="flex items-center justify-center">
                    <LogIn className="mr-2 h-5 w-5" />
                    Login
                  </span>
                )}
              </Button>
            </CardFooter>
          </form>
        </Form>

        <div className="p-6 text-center border-t">
          <p className="text-sm text-gray-600">
            Don't have an account?{" "}
            <a
              href="/login"
              className="font-medium text-primary hover:text-primary/80"
            >
              Contact your System Administrator
            </a>
          </p>
        </div>
      </Card>
    </div>
  );
};

export default LoginPage;
