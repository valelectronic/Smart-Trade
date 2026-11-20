"use client";

import { useEffect, useState } from "react";
import { HunterDashboard } from "@/components/HunterDashboard";
import { Activity } from "lucide-react";

const API_BASE = "https://smart-trade-production-44e4.up.railway.app";

type MarketPrices = {
  cc_usd?: number | string | null;
  btc_usd?: number | string | null;
  status?: string;
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

export default function Home() {
  const [marketPrices, setMarketPrices] = useState<MarketPrices>({});
  const [pricesLoaded, setPricesLoaded] = useState(false);

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

  const ccPrice = formatUsd(marketPrices.cc_usd);
  const btcPrice = formatUsd(marketPrices.btc_usd);

  return (
    <div className="min-h-screen flex flex-col w-full bg-onyx">
      {/* Desktop header */}
      <header className="hidden md:flex sticky top-0 z-50 items-center justify-between border-b border-shadow-grey bg-onyx px-6 py-3">
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
        </div>
      </header>

      {/* Mobile header */}
      <header className="sticky top-0 z-50 flex flex-col gap-2 border-b border-shadow-grey bg-onyx px-4 py-3 md:hidden">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-emerald text-xs font-bold text-onyx-dark">
              C
            </div>
            <span className="text-sm font-semibold text-snow">
              Cantex Rebate Hunter
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-emerald">
            <Activity size={14} />
            <span>Hunter</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <MarketPrice label="CC" value={pricesLoaded ? ccPrice : "…"} />
          <MarketPrice label="BTC" value={pricesLoaded ? btcPrice : "…"} />
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-3 py-4 pb-8 sm:px-5 md:px-8 md:py-8">
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
    </div>
  );
}
