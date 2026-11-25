"use client";

import { useEffect, useState } from "react";
import { HunterDashboard } from "@/components/HunterDashboard";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { useAuth } from "@/components/auth/AuthProvider";
import {
  Activity,
  CheckCircle2,
  KeyRound,
  LayoutDashboard,
  LogOut,
  ShieldCheck,
  Trash2,
  UserRound,
  X,
} from "lucide-react";

const API_BASE = "https://smart-trade-production-44e4.up.railway.app";

type MarketPrices = {
  cc_usd?: number | string | null;
  btc_usd?: number | string | null;
  status?: string;
};

type CredentialStatus = {
  configured: boolean;
};

function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";

  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return "—";

  return parsed >= 100
    ? `$${parsed.toLocaleString("en-AU", { maximumFractionDigits: 2 })}`
    : `$${parsed.toLocaleString("en-AU", {
        minimumFractionDigits: 4,
        maximumFractionDigits: 6,
      })}`;
}

function MarketPrice({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-shadow-grey bg-black/20 px-2.5 py-1.5">
      <span className="text-[10px] font-medium uppercase tracking-wide text-granite">
        {label}
      </span>
      <span className="text-xs font-semibold tabular-nums text-snow">
        {value}
      </span>
    </div>
  );
}

function ApiKeysPanel({ onClose }: { onClose: () => void }) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [operatorKey, setOperatorKey] = useState("");
  const [tradingKey, setTradingKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadStatus = async () => {
    try {
      const response = await fetch("/api/backend/api/credentials", {
        cache: "no-store",
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Could not check credential status.");
      }

      setConfigured(Boolean(data.configured));
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not connect to the trading service.",
      );
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  const handleSave = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setMessage("");

    if (!operatorKey.trim() || !tradingKey.trim()) {
      setError("Enter both your operator key and trading key.");
      return;
    }

    setSaving(true);

    try {
      const response = await fetch("/api/backend/api/credentials", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operator_key: operatorKey.trim(),
          trading_key: tradingKey.trim(),
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Could not save your credentials.");
      }

      setConfigured(true);
      setOperatorKey("");
      setTradingKey("");
      setMessage("Your Cantex credentials have been securely saved.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save credentials.",
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (
      !window.confirm(
        "Delete your saved Cantex credentials? The Hunter will no longer be able to use them.",
      )
    ) {
      return;
    }

    setDeleting(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch("/api/backend/api/credentials", {
        method: "DELETE",
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Could not delete your credentials.");
      }

      setConfigured(false);
      setMessage("Your saved Cantex credentials have been deleted.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not delete credentials.",
      );
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-keys-title"
        className="w-full max-w-lg rounded-xl border border-shadow-grey bg-onyx p-5 shadow-2xl sm:p-6"
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-lg border border-emerald/20 bg-emerald/10 text-emerald">
              <KeyRound size={20} />
            </div>
            <h2 id="api-keys-title" className="text-lg font-semibold text-snow">
              Cantex API Keys
            </h2>
            <p className="mt-1 text-sm text-granite">
              Connect your Cantex account securely.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-2 text-granite transition hover:bg-white/5 hover:text-snow"
            aria-label="Close API keys"
          >
            <X size={19} />
          </button>
        </div>

        <div className="mb-5 flex items-center gap-3 rounded-lg border border-shadow-grey bg-black/20 p-3">
          <ShieldCheck size={19} className="shrink-0 text-emerald" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-snow">
              Encrypted credential storage
            </p>
            <p className="mt-0.5 text-xs text-granite">
              Your keys are encrypted before being stored. They are never
              returned by the status endpoint.
            </p>
          </div>
        </div>

        {configured === null && !error && (
          <p className="py-4 text-center text-sm text-granite">
            Checking credential status…
          </p>
        )}

        {configured === true && (
          <div className="mb-5 flex items-center gap-2 rounded-lg border border-emerald/20 bg-emerald/5 p-3">
            <CheckCircle2 size={18} className="shrink-0 text-emerald" />
            <span className="text-sm text-snow">
              Cantex credentials are configured.
            </span>
          </div>
        )}

        {configured !== null && (
          <form onSubmit={handleSave} className="space-y-4">
            <div>
              <label
                htmlFor="operator-key"
                className="mb-1.5 block text-sm font-medium text-snow"
              >
                Operator Key
              </label>
              <input
                id="operator-key"
                type="password"
                autoComplete="off"
                value={operatorKey}
                onChange={event => setOperatorKey(event.target.value)}
                placeholder="Enter your operator key"
                className="w-full rounded-lg border border-shadow-grey bg-black/30 px-3 py-3 text-sm text-snow outline-none transition placeholder:text-granite/60 focus:border-emerald"
              />
            </div>

            <div>
              <label
                htmlFor="trading-key"
                className="mb-1.5 block text-sm font-medium text-snow"
              >
                Trading Key
              </label>
              <input
                id="trading-key"
                type="password"
                autoComplete="off"
                value={tradingKey}
                onChange={event => setTradingKey(event.target.value)}
                placeholder="Enter your trading key"
                className="w-full rounded-lg border border-shadow-grey bg-black/30 px-3 py-3 text-sm text-snow outline-none transition placeholder:text-granite/60 focus:border-emerald"
              />
            </div>

            <button
              type="submit"
              disabled={saving || configured === null}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald px-4 py-3 text-sm font-semibold text-onyx-dark transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <KeyRound size={16} />
              {saving ? "Saving credentials…" : "Save Cantex Credentials"}
            </button>
          </form>
        )}

        {configured === true && (
          <button
            type="button"
            onClick={() => void handleDelete()}
            disabled={deleting}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-red-500/30 px-4 py-3 text-sm font-medium text-red-400 transition hover:bg-red-500/10 disabled:opacity-50"
          >
            <Trash2 size={16} />
            {deleting ? "Deleting…" : "Delete Saved Credentials"}
          </button>
        )}

        {error && (
          <p role="alert" className="mt-4 rounded-lg bg-red-500/10 p-3 text-sm text-red-400">
            {error}
          </p>
        )}

        {message && (
          <p role="status" className="mt-4 rounded-lg bg-emerald/10 p-3 text-sm text-emerald">
            {message}
          </p>
        )}

        <p className="mt-5 text-center text-[11px] leading-relaxed text-granite">
          Never share your Cantex keys with anyone. Only enter credentials
          obtained from your own Cantex account.
        </p>
      </section>
    </div>
  );
}

export default function Home() {
  const [marketPrices, setMarketPrices] = useState<MarketPrices>({});
  const [pricesLoaded, setPricesLoaded] = useState(false);
  const [showApiKeys, setShowApiKeys] = useState(false);
  const [showAccount, setShowAccount] = useState(false);

  const { user, logout } = useAuth();

  useEffect(() => {
    let active = true;

    const loadMarketPrices = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/market-prices`, {
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error(`Market price request failed: ${response.status}`);
        }

        const data: MarketPrices = await response.json();

        if (active) {
          setMarketPrices(data);
          setPricesLoaded(true);
        }
      } catch (error) {
        console.error("Unable to load market prices:", error);
        if (active) setPricesLoaded(true);
      }
    };

    void loadMarketPrices();

    const interval = window.setInterval(() => {
      void loadMarketPrices();
    }, 10_000);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!showApiKeys && !showAccount) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowApiKeys(false);
        setShowAccount(false);
      }
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [showApiKeys, showAccount]);

  const ccPrice = formatUsd(marketPrices.cc_usd);
  const btcPrice = formatUsd(marketPrices.btc_usd);

  const openApiKeys = () => {
    setShowAccount(false);
    setShowApiKeys(true);
  };

  const openAccount = () => {
    setShowApiKeys(false);
    setShowAccount(current => !current);
  };

  return (
    <AuthGuard>
      <div className="flex min-h-screen w-full flex-col bg-onyx">
        {/* Compact desktop header */}
        <header className="sticky top-0 z-50 hidden items-center justify-between border-b border-shadow-grey bg-onyx/95 px-6 py-3 backdrop-blur md:flex">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald font-bold text-onyx-dark shadow-[0_0_15px_rgba(34,220,149,0.3)]">
              C
            </div>
            <span className="text-lg font-semibold tracking-tight text-snow">
              Cantex Rebate Hunter
            </span>
          </div>

          <div className="flex items-center gap-3">
            <MarketPrice label="CC" value={pricesLoaded ? ccPrice : "…"} />
            <MarketPrice label="BTC" value={pricesLoaded ? btcPrice : "…"} />

            <div className="flex items-center gap-2 text-sm text-emerald">
              <Activity size={16} />
              <span>Hunter Dashboard</span>
            </div>

            <button
              type="button"
              onClick={openAccount}
              className="flex items-center gap-2 rounded-lg border border-shadow-grey px-3 py-2 text-sm text-snow transition hover:border-emerald/40 hover:bg-white/5"
            >
              <UserRound size={15} className="text-emerald" />
              <span className="max-w-[120px] truncate">
                {user?.username ?? "Account"}
              </span>
            </button>
          </div>
        </header>

        {/* Compact mobile header */}
        <header className="sticky top-0 z-50 border-b border-shadow-grey bg-onyx/95 px-4 py-3 backdrop-blur md:hidden">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-emerald text-sm font-bold text-onyx-dark">
                C
              </div>
              <span className="truncate text-sm font-semibold text-snow">
                Cantex Rebate Hunter
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-1.5 text-xs text-emerald">
              <Activity size={14} />
              <span>Hunter</span>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <MarketPrice label="CC" value={pricesLoaded ? ccPrice : "…"} />
            <MarketPrice label="BTC" value={pricesLoaded ? btcPrice : "…"} />
          </div>
        </header>

        <main className="mx-auto w-full max-w-5xl min-w-0 flex-1 px-3 py-4 pb-28 sm:px-5 md:px-8 md:py-8 md:pb-8">
          <div className="mb-4 md:mb-6">
            <h1 className="text-lg font-medium text-snow md:text-2xl">
              Rebate Hunter
            </h1>
            <p className="mt-1 text-xs text-granite md:text-sm">
              Monitor execution, manage swap rules and review trading activity.
            </p>
          </div>

          <div className="w-full min-w-0">
            <HunterDashboard />
          </div>
        </main>

        {/* Mobile bottom navigation */}
        <nav className="fixed inset-x-0 bottom-0 z-50 border-t border-shadow-grey bg-onyx/95 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur md:hidden">
          <div className="mx-auto flex max-w-lg items-center justify-around">
            <button
              type="button"
              className="flex min-w-[72px] flex-col items-center gap-1 rounded-lg px-3 py-2 text-emerald"
            >
              <LayoutDashboard size={20} />
              <span className="text-[10px] font-medium">Dashboard</span>
            </button>

            <button
              type="button"
              onClick={openApiKeys}
              className="flex min-w-[72px] flex-col items-center gap-1 rounded-lg px-3 py-2 text-granite transition hover:bg-white/5 hover:text-emerald"
            >
              <KeyRound size={20} />
              <span className="text-[10px] font-medium">API Keys</span>
            </button>

            <button
              type="button"
              onClick={openAccount}
              className="flex min-w-[72px] flex-col items-center gap-1 rounded-lg px-3 py-2 text-granite transition hover:bg-white/5 hover:text-emerald"
            >
              <UserRound size={20} />
              <span className="text-[10px] font-medium">Account</span>
            </button>
          </div>
        </nav>

        {/* Account popover */}
        {showAccount && (
          <>
            <button
              type="button"
              aria-label="Close account menu"
              className="fixed inset-0 z-[60] cursor-default bg-black/30"
              onClick={() => setShowAccount(false)}
            />

            <section className="fixed bottom-20 right-4 z-[65] w-[min(300px,calc(100vw-2rem))] rounded-xl border border-shadow-grey bg-onyx p-4 shadow-2xl md:bottom-auto md:right-6 md:top-[72px]">
              <div className="mb-4 border-b border-shadow-grey pb-3">
                <p className="text-xs text-granite">Signed in as</p>
                <p className="mt-1 truncate text-sm font-semibold text-snow">
                  {user?.username}
                </p>
              </div>

              <button
                type="button"
                onClick={openApiKeys}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-sm text-snow transition hover:bg-white/5"
              >
                <KeyRound size={17} className="text-emerald" />
                Manage Cantex API Keys
              </button>

              <button
                type="button"
                onClick={() => void logout()}
                className="mt-1 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-sm text-red-400 transition hover:bg-red-500/10"
              >
                <LogOut size={17} />
                Log out
              </button>
            </section>
          </>
        )}

        {showApiKeys && (
          <ApiKeysPanel onClose={() => setShowApiKeys(false)} />
        )}
      </div>
    </AuthGuard>
  );
}