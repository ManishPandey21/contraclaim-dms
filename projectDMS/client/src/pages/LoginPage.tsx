import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
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
import { Eye, EyeOff, LogIn, ShieldCheck } from "lucide-react";
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
      className="relative min-h-[100svh] overflow-x-hidden bg-paper font-franklin text-ink"
      data-testid="login-page"
    >
      <div
        className="absolute inset-0 bg-gradient-to-br from-brand-soft via-paper to-paper"
        data-testid="login-background"
        aria-hidden="true"
      />

      <main className="relative z-10 mx-auto flex min-h-[100svh] w-full max-w-7xl flex-col px-4 py-5 sm:px-6 sm:py-6 lg:px-8">
        <header className="flex items-center justify-between gap-4">
          <a
            href="/"
            className="inline-flex items-center"
            aria-label="ContraClaim home"
          >
            <img
              src="/contraclaim2.png"
              alt="ContraClaim"
              className="h-8 w-auto sm:h-9"
            />
          </a>
          <span className="hidden text-sm font-semibold tracking-wide text-ink/60 sm:inline">
            Secure contract intelligence
          </span>
        </header>

        <div
          className="grid flex-1 items-center gap-7 py-7 sm:gap-9 sm:py-10 lg:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)] lg:gap-14"
          data-testid="login-responsive-layout"
        >
          <section className="mx-auto max-w-2xl text-center lg:mx-0 lg:text-left">
            <span className="inline-flex items-center rounded-full border border-brand/15 bg-white/65 px-3 py-1 text-xs font-bold uppercase tracking-[0.14em] text-brand backdrop-blur-sm">
              Governed workspace
            </span>
            <h1 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.02em] text-ink sm:mt-5 sm:text-4xl lg:text-5xl">
              Welcome back to your contract record.
            </h1>
            <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-ink/65 sm:mt-4 sm:text-base sm:leading-7 lg:mx-0">
              Claims, correspondence and evidence—connected in one defensible
              workspace.
            </p>
            <div className="mt-4 inline-flex items-center gap-2 text-xs font-semibold text-ink/70 sm:mt-6 sm:text-sm">
              <ShieldCheck className="h-4 w-4 text-brand" aria-hidden="true" />
              <span>Role-based access · Complete audit trail</span>
            </div>
          </section>

          <Card
            className="mx-auto w-full max-w-md border-ink/10 bg-white/95 shadow-[0_28px_70px_-36px_rgba(13,27,46,0.45)] backdrop-blur-md lg:mx-0 lg:justify-self-end"
            data-testid="login-card"
          >
            <CardHeader className="space-y-1.5 px-5 pb-4 pt-5 sm:px-7 sm:pt-7">
              <p className="text-xs font-bold uppercase tracking-[0.14em] text-brand">
                Sign in
              </p>
              <CardTitle className="font-serif text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                Access your workspace
              </CardTitle>
              <CardDescription className="text-sm text-ink/55">
                Use your organisation credentials to continue.
              </CardDescription>
            </CardHeader>

            <Form {...form}>
              <form onSubmit={form.handleSubmit(onSubmit)}>
                <CardContent className="space-y-4 px-5 pb-5 sm:px-7 sm:pb-7">
                  {loginError && (
                    <Alert
                      variant="destructive"
                      role="alert"
                      aria-live="assertive"
                    >
                      <AlertDescription>{loginError}</AlertDescription>
                    </Alert>
                  )}

                  <FormField
                    control={form.control}
                    name="email"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel
                          htmlFor="email"
                          className="text-sm font-semibold text-ink"
                        >
                          Work email
                        </FormLabel>
                        <FormControl>
                          <Input
                            id="email"
                            type="email"
                            placeholder="name@company.com"
                            autoComplete="email"
                            aria-required="true"
                            aria-describedby="email-description"
                            className="h-11 border-ink/15 bg-white"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription
                          id="email-description"
                          className="sr-only"
                        >
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
                        <div className="flex items-center justify-between gap-3">
                          <FormLabel
                            htmlFor="password"
                            className="text-sm font-semibold text-ink"
                          >
                            Password
                          </FormLabel>
                          <a
                            href="#"
                            className="text-xs font-semibold text-brand transition hover:text-[#1157a8] sm:text-sm"
                          >
                            Forgot password?
                          </a>
                        </div>
                        <div className="relative">
                          <FormControl>
                            <Input
                              id="password"
                              type={showPassword ? "text" : "password"}
                              placeholder="Enter your password"
                              autoComplete="current-password"
                              aria-required="true"
                              aria-describedby="password-description"
                              className="h-11 border-ink/15 bg-white pr-11"
                              {...field}
                            />
                          </FormControl>
                          <button
                            type="button"
                            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full p-1 text-ink/45 transition hover:text-ink focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2"
                            onClick={togglePasswordVisibility}
                            aria-label={
                              showPassword ? "Hide password" : "Show password"
                            }
                            aria-controls="password"
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

                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="remember-me"
                      className="h-4 w-4 rounded border-ink/25 text-brand focus:ring-brand"
                      checked={form.getValues("rememberMe")}
                      {...form.register("rememberMe")}
                    />
                    <Label
                      htmlFor="remember-me"
                      className="text-sm font-medium text-ink/70"
                    >
                      Keep me signed in on this device
                    </Label>
                  </div>

                  <Button
                    type="submit"
                    className="h-11 w-full bg-brand font-semibold text-white shadow-[0_10px_24px_-12px_rgba(20,102,196,0.9)] hover:bg-[#1157a8]"
                    disabled={form.formState.isSubmitting}
                  >
                    {form.formState.isSubmitting ? (
                      <span className="flex items-center justify-center">
                        <svg
                          className="-ml-1 mr-3 h-5 w-5 animate-spin text-white"
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
                          />
                          <path
                            className="opacity-75"
                            fill="currentColor"
                            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                          />
                        </svg>
                        Signing in...
                      </span>
                    ) : (
                      <span className="flex items-center justify-center">
                        Sign in
                        <LogIn className="ml-2 h-4 w-4" aria-hidden="true" />
                      </span>
                    )}
                  </Button>

                  <p className="text-center text-xs leading-5 text-ink/50">
                    Need access? Contact your system administrator.
                  </p>
                </CardContent>
              </form>
            </Form>
          </Card>
        </div>

        <footer className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-center text-xs text-ink/45 lg:justify-start">
          <span>Authorised users only</span>
          <span aria-hidden="true">·</span>
          <span>Activity is audited</span>
        </footer>
      </main>
    </div>
  );
};

export default LoginPage;
