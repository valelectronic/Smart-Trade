"use client";

import { useState, useEffect } from "react";
import { HunterDashboard } from "@/components/HunterDashboard";
import { Wallet, Activity, Bell, TrendingUp, Crosshair } from "lucide-react";

export default function Home() {
  const [activeTab, setActiveTab] = useState<"portfolio" | "hunter">("portfolio");
  const [isMounted, setIsMounted] = useState(false);
  
  // Portfolio Value State
  const [totalPortfolioValue, setTotalPortfolioValue] = useState<number>(0);
  const [isFetchingValue, setIsFetchingValue] = useState(false);
  
  // Market Prices (Static)
  const [ccPrice, setCcPrice] = useState<number>(0);
  const [btcPrice, setBtcPrice] = useState<number>(0);

  useEffect(() => {
    const saved = localStorage.getItem("cantex_activeTab");
    if (saved === "portfolio" || saved === "hunter") {
      setActiveTab(saved);
    }
    setIsMounted(true);
  }, []);

  useEffect(() => {
    if (isMounted) {
      localStorage.setItem("cantex_activeTab", activeTab);
    }
  }, [activeTab, isMounted]);

  // Fetch Portfolio + Market Prices
  useEffect(() => {
    let isCancelled = false;

    const fetchData = async () => {
      setIsFetchingValue(true);
      
      try {
        // 1. Fetch Portfolio Value
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${apiUrl}/api/portfolio-value`);
        const data = await res.json();
        
        if (data.status === "success" && data.total_usd && !isCancelled) {
          const parsedValue = parseFloat(data.total_usd);
          if (!isNaN(parsedValue)) {
            setTotalPortfolioValue(parsedValue);
          }
        }

        // 2. Fetch Market Prices (static from prices.json via a tiny endpoint)
        // We use the same portfolio endpoint's data if it contains the prices,
        // or we can add a tiny /api/market-prices endpoint later.
        // For now, we will read from a separate fetch if available.
        try {
          const marketRes = await fetch(`${apiUrl}/api/market-prices`);
          const marketData = await marketRes.json();
          if (!isCancelled) {
            setCcPrice(marketData.cc_usd || 0);
            setBtcPrice(marketData.btc_usd || 0);
          }
        } catch {
          // Market prices endpoint not yet added. Use defaults.
        }

      } catch (err) {
        // Silent fail
      } finally {
        if (!isCancelled) setIsFetchingValue(false);
      }
    };
    
    fetchData();
    const interval = setInterval(fetchData, 15000); // Every 15s
    
    return () => {
      isCancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="min-h-screen flex flex-col w-full">
      
      {/* DESKTOP TOP NAVBAR */}
      <nav className="hidden md:flex border-b border-shadow-grey bg-onyx px-6 py-4 items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="h-8 w-8 bg-emerald rounded-lg flex items-center justify-center font-bold text-onyx-dark shadow-[0_0_15px_rgba(34,220,149,0.3)]">
            C
          </div>
          <span className="font-semibold text-lg tracking-tight text-snow">Cantex Terminal</span>
        </div>
        <div className="flex items-center gap-6 text-sm text-granite">
          <div className="flex items-center gap-2 text-emerald">
            <Activity size={16} />
            <span>Dashboard</span>
          </div>
        </div>
      </nav>

      {/* MOBILE TOP HEADER */}
      <div className="md:hidden border-b border-shadow-grey bg-onyx px-4 py-3 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 bg-emerald rounded flex items-center justify-center font-bold text-onyx-dark text-xs">C</div>
          <span className="font-semibold text-snow">Cantex</span>
        </div>
        <Bell size={18} className="text-granite" />
      </div>

      {/* STATIC MARKET BAR (No Polling) */}
      <div className="bg-carbon border-b border-jet-black px-6 py-2 flex items-center justify-between text-xs md:text-sm text-granite font-mono">
        <div className="flex items-center gap-2">
          <span className="text-amber-400 font-bold">₿</span>
          <span>BTC/USD</span>
          <span className="text-snow font-bold">
            {btcPrice > 0 ? `$${btcPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "---"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-emerald font-bold">◎</span>
          <span>CC/USDC</span>
          <span className="text-snow font-bold">
            {ccPrice > 0 ? `$${ccPrice.toFixed(5)}` : "---"}
          </span>
        </div>
      </div>

      {/* Main Content */}
      <main className="flex-1 max-w-5xl w-full mx-auto p-4 md:p-8 pb-24 md:pb-8">
        
        {/* PORTFOLIO VALUE CARD */}
        <div className="bg-carbon border border-jet-black rounded-2xl p-5 mb-6 flex items-center justify-between">
          <div>
            <p className="text-xs text-granite mb-1 uppercase tracking-wider font-semibold">Estimated Total Value</p>
            <h2 className="text-3xl font-bold text-snow font-mono">
              {isFetchingValue ? "..." : `$${totalPortfolioValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`}
            </h2>
          </div>
          <div className="p-3 bg-emerald/10 rounded-xl">
            <TrendingUp size={24} className="text-emerald" />
          </div>
        </div>

        {/* TABS: Portfolio | Hunter */}
        <div className="mb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-medium text-snow mb-1">Trading Dashboard</h1>
            <p className="text-granite text-sm">Manage your strategies.</p>
          </div>

          <div className="flex bg-carbon p-1 rounded-xl border border-jet-black w-full md:w-auto">
            <button
              onClick={() => setActiveTab("portfolio")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-lg transition-all ${
                activeTab === "portfolio" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <Activity size={16} />
              Portfolio
            </button>
            <button
              onClick={() => setActiveTab("hunter")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-lg transition-all ${
                activeTab === "hunter" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <Crosshair size={16} />
              Hunter
            </button>
          </div>
        </div>

        {/* Tab Content */}
        <div className="w-full">
          {activeTab === "portfolio" && (
            <div className="w-full animate-in fade-in duration-300">
              <div className="bg-onyx border border-shadow-grey rounded-2xl p-6 text-center text-granite">
                <p className="text-sm">Portfolio details and history will appear here.</p>
                <p className="text-xs mt-2 text-granite/50">Use the Hunter tab to manage your rebate rules.</p>
              </div>
            </div>
          )}

          {activeTab === "hunter" && (
            <div className="w-full animate-in fade-in duration-300">
              <HunterDashboard />
            </div>
          )}
        </div>

      </main>

      {/* MOBILE BOTTOM NAVBAR */}
      <nav className="md:hidden fixed bottom-0 left-0 w-full bg-onyx border-t border-shadow-grey flex justify-around items-center pt-2 pb-6 z-50">
        <div 
          onClick={() => setActiveTab("portfolio")}
          className={`flex flex-col items-center cursor-pointer transition-colors ${activeTab === "portfolio" ? "text-emerald" : "text-granite"}`}
        >
          <Activity size={20} />
          <span className="text-[10px] mt-1 font-medium">Portfolio</span>
        </div>
        <div 
          onClick={() => setActiveTab("hunter")}
          className={`flex flex-col items-center cursor-pointer transition-colors ${activeTab === "hunter" ? "text-emerald" : "text-granite"}`}
        >
          <Crosshair size={20} />
          <span className="text-[10px] mt-1 font-medium">Hunter</span>
        </div>
      </nav>

    </div>
  );
}