"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  ArrowRight,
  Eye,
  EyeOff,
  Loader2,
  LockKeyhole,
  UserRound,
} from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "./AuthProvider";

type Props = {
  mode: "login" | "register";
};

export function AuthForm({ mode }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const router = useRouter();
  const { refresh } = useAuth();
  const registering = mode === "register";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (registering && password.length < 6) {
      toast.error("Password must contain at least 6 characters.");
      return;
    }

    if (registering && password !== confirmPassword) {
      toast.error("Passwords do not match.");
      return;
    }

    setSubmitting(true);

    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: username.trim(),
          password,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        toast.error(
          data.detail ||
            `Could not ${registering ? "create account" : "sign in"}.`,
        );
        return;
      }

      await refresh();

      toast.success(
        registering
          ? "Account created successfully."
          : "Welcome back.",
      );

      router.replace("/");
      router.refresh();
    } catch {
      toast.error("Unable to reach the authentication service.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-onyx-dark px-4 py-10">
      <section className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald text-xl font-black text-onyx-dark shadow-[0_0_28px_rgba(34,220,149,0.2)]">
            C
          </div>

          <p className="mb-2 flex items-center justify-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-emerald">
            <Activity size={14} />
            Cantex Terminal
          </p>

          <h1 className="text-2xl font-semibold tracking-tight text-snow">
            {registering ? "Create your account" : "Welcome back"}
          </h1>

          <p className="mt-2 text-sm text-granite">
            {registering
              ? "Set up your secure trading workspace."
              : "Sign in to access your Rebate Hunter dashboard."}
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-shadow-grey bg-carbon p-6 shadow-2xl sm:p-8"
        >
          <label
            className="mb-2 block text-xs font-medium text-granite"
            htmlFor="username"
          >
            Username
          </label>

          <div className="mb-5 flex items-center gap-3 rounded-xl border border-jet-black bg-onyx px-3 focus-within:border-emerald/60">
            <UserRound size={17} className="text-granite" />

            <input
              id="username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={40}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="h-12 w-full bg-transparent text-sm text-snow outline-none placeholder:text-granite/60"
              placeholder="Enter your username"
            />
          </div>

          <label
            className="mb-2 block text-xs font-medium text-granite"
            htmlFor="password"
          >
            Password
          </label>

          <div className="mb-5 flex items-center gap-3 rounded-xl border border-jet-black bg-onyx px-3 focus-within:border-emerald/60">
            <LockKeyhole size={17} className="text-granite" />

            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete={
                registering ? "new-password" : "current-password"
              }
              required
              minLength={registering ? 6 : 1}
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="h-12 w-full bg-transparent text-sm text-snow outline-none placeholder:text-granite/60"
              placeholder={
                registering
                  ? "At least 6 characters"
                  : "Enter your password"
              }
            />

            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              className="text-granite hover:text-snow"
            >
              {showPassword ? (
                <EyeOff size={17} />
              ) : (
                <Eye size={17} />
              )}
            </button>
          </div>

          {registering && (
            <>
              <label
                className="mb-2 block text-xs font-medium text-granite"
                htmlFor="confirmPassword"
              >
                Confirm Password
              </label>

              <div className="flex items-center gap-3 rounded-xl border border-jet-black bg-onyx px-3 focus-within:border-emerald/60">
                <LockKeyhole size={17} className="text-granite" />

                <input
                  id="confirmPassword"
                  type={showConfirmPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  minLength={6}
                  maxLength={256}
                  value={confirmPassword}
                  onChange={(event) =>
                    setConfirmPassword(event.target.value)
                  }
                  className="h-12 w-full bg-transparent text-sm text-snow outline-none placeholder:text-granite/60"
                  placeholder="Re-enter your password"
                />

                <button
                  type="button"
                  onClick={() =>
                    setShowConfirmPassword((value) => !value)
                  }
                  aria-label={
                    showConfirmPassword
                      ? "Hide confirm password"
                      : "Show confirm password"
                  }
                  className="text-granite hover:text-snow"
                >
                  {showConfirmPassword ? (
                    <EyeOff size={17} />
                  ) : (
                    <Eye size={17} />
                  )}
                </button>
              </div>
            </>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-7 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-emerald text-sm font-bold text-onyx-dark transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? (
              <Loader2 size={17} className="animate-spin" />
            ) : (
              <>
                {registering ? "Create account" : "Sign in"}
                <ArrowRight size={17} />
              </>
            )}
          </button>

          <p className="mt-6 text-center text-sm text-granite">
            {registering
              ? "Already registered?"
              : "New to Cantex Terminal?"}{" "}

            <Link
              className="font-semibold text-emerald hover:underline"
              href={registering ? "/login" : "/register"}
            >
              {registering ? "Sign in" : "Create account"}
            </Link>
          </p>
        </form>

        <p className="mt-5 text-center text-[11px] text-granite/70">
          Secure access · Your Cantex credentials are stored separately
          from this session.
        </p>
      </section>
    </main>
  );
}