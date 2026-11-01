"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { errorMessage, publicPost } from "@/lib/api";
import { getRefreshToken, setTokens } from "@/lib/auth";

const inputClass =
  "mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const registering = mode === "register";

  useEffect(() => {
    if (getRefreshToken()) router.replace("/invoices");
  }, [router]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (registering) {
        await publicPost("/api/auth/register/", { username, email, password });
      }
      const tokens = await publicPost<{ access: string; refresh: string }>(
        "/api/auth/login/",
        { username, password },
      );
      setTokens(tokens.access, tokens.refresh);
      router.replace("/invoices");
    } catch (caught) {
      setError(errorMessage(caught));
      setSubmitting(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center bg-slate-50 p-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold text-slate-900">InvoiceFlow</h1>
        <p className="mt-1 text-sm text-slate-500">
          {registering ? "Create a merchant account" : "Sign in to your account"}
        </p>

        {error && (
          <p role="alert" className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </p>
        )}

        <label className="mt-4 block text-sm font-medium text-slate-700">
          Username
          <input
            className={inputClass}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            required
          />
        </label>

        {registering && (
          <label className="mt-3 block text-sm font-medium text-slate-700">
            Email
            <input
              className={inputClass}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
            />
          </label>
        )}

        <label className="mt-3 block text-sm font-medium text-slate-700">
          Password
          <input
            className={inputClass}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={registering ? "new-password" : "current-password"}
            required
          />
        </label>

        <button
          type="submit"
          disabled={submitting}
          className="mt-5 w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {submitting ? "Please wait..." : registering ? "Create account" : "Sign in"}
        </button>

        <button
          type="button"
          onClick={() => {
            setMode(registering ? "login" : "register");
            setError("");
          }}
          className="mt-3 w-full text-sm text-blue-700 hover:underline"
        >
          {registering ? "Already have an account? Sign in" : "New merchant? Create an account"}
        </button>
      </form>
    </main>
  );
}
