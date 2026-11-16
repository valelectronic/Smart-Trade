"use client";

import { useState, useEffect } from "react";
import { HunterDashboard } from "@/components/HunterDashboard";
import { Activity, Bell, TrendingUp, Crosshair } from "lucide-react";

// HARDCODED API URL (no env variable)
const API_URL = "https://smart-trade-production-44e4.up.railway.app";

export default function Home() {
  const [activeTab, setActiveTab] = useState<"portfolio" | "hunter">("portfolio");
  const [isMounted, setIsMounted] = useState(false);
  
  const [totalPortfolioValue, setTotalPortfolioValue] = useState<number>(0);
  const [isFetchingValue, setIsFetchingValue] = useState(false);
  
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

  useEffect(() => {
    let isCancelled = false;

    const fetchData = async () => {
      setIsFetchingValue(true);
      
      try {
        const res = await fetch(`${API_URL}/api/portfolio-value`);
        const data = await res.json();
        
        if (data.status === "success" && data.total_usd && !isCancelled) {
          const parsedValue = parseFloat(data.total_usd);
          if (!isNaN(parsedValue)) {
            setTotalPortfolioValue(parsedValue);
          }
        }

        try {
          const marketRes = await fetch(`${API_URL}/api/market-prices`);
          const marketData = await marketRes.json();
          if (!isCancelled) {
            setCcPrice(marketData.cc_usd || 0);
            setBtcPrice(marketData.btc_usd || 0);
          }
        } catch {
          // Silent fail
        }

      } catch (err) {
        // Silent fail
      } finally {
        if (!isCancelled) setIsFetchingValue(false);
      }
    };
    
    fetchData();
    const interval = setInterval(fetchData, 15000);
    
    return () => {
      isCancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="min-h-screen flex flex-col w-full">
      
      {/* DESKTOP TOP NAVBAR */}
      <nav className="hidden md:flex border-b border-shadow-grey bg-onyx px-6 py-3 items-center justify-between sticky top-0 z-50">
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
      <div className="md:hidden border-b border-shadow-grey bg-onyx px-4 py-2.5 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 bg-emerald rounded flex items-center justify-center font-bold text-onyx-dark text-xs">C</div>
          <span className="font-semibold text-snow text-sm">Cantex</span>
        </div>
        <Bell size={16} className="text-granite" />
      </div>

      {/* STICKY MARKET BAR */}
      <div className="sticky top-[48px] md:top-[57px] z-40 bg-carbon border-b border-jet-black px-4 md:px-6 py-2 flex items-center justify-between text-[10px] md:text-sm text-granite font-mono">
        <div className="flex items-center gap-1.5 md:gap-2">
          <span className="text-amber-400 font-bold text-xs">₿</span>
          <span className="hidden sm:inline">BTC/USD</span>
          <span className="sm:hidden">BTC</span>
          <span className="text-snow font-bold">
            {btcPrice > 0 ? `$${btcPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "---"}
          </span>
        </div>
        <div className="flex items-center gap-1.5 md:gap-2">
          <span className="text-emerald font-bold text-xs">◎</span>
          <span className="hidden sm:inline">CC/USDC</span>
          <span className="sm:hidden">CC</span>
          <span className="text-snow font-bold">
            {ccPrice > 0 ? `$${ccPrice.toFixed(5)}` : "---"}
          </span>
        </div>
      </div>

      {/* Main Content */}
      <main className="flex-1 max-w-5xl w-full mx-auto p-3 md:p-8 pb-24 md:pb-8">
        
        {/* PORTFOLIO VALUE CARD */}
        <div className="bg-carbon border border-jet-black rounded-xl md:rounded-2xl p-4 md:p-5 mb-4 md:mb-6 flex items-center justify-between">
          <div>
            <p className="text-[10px] md:text-xs text-granite mb-1 uppercase tracking-wider font-semibold">Estimated Total Value</p>
            <h2 className="text-2xl md:text-3xl font-bold text-snow font-mono">
              {isFetchingValue ? "..." : `$${totalPortfolioValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`}
            </h2>
          </div>
          <div className="p-2 md:p-3 bg-emerald/10 rounded-lg md:rounded-xl">
            <TrendingUp size={20} className="text-emerald md:w-6 md:h-6" />
          </div>
        </div>

        {/* TABS: Portfolio | Hunter */}
        <div className="mb-4 md:mb-6 flex flex-col md:flex-row md:items-end justify-between gap-3 md:gap-4">
          <div>
            <h1 className="text-lg md:text-2xl font-medium text-snow mb-0.5 md:mb-1">Trading Dashboard</h1>
            <p className="text-granite text-xs md:text-sm">Manage your strategies.</p>
          </div>

          <div className="flex bg-carbon p-1 rounded-lg md:rounded-xl border border-jet-black w-full md:w-auto">
            <button
              onClick={() => setActiveTab("portfolio")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-1.5 md:gap-2 px-4 md:px-6 py-2 text-xs md:text-sm font-medium rounded-md md:rounded-lg transition-all ${
                activeTab === "portfolio" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <Activity size={14} className="md:w-4 md:h-4" />
              Portfolio
            </button>
            <button
              onClick={() => setActiveTab("hunter")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-1.5 md:gap-2 px-4 md:px-6 py-2 text-xs md:text-sm font-medium rounded-md md:rounded-lg transition-all ${
                activeTab === "hunter" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <Crosshair size={14} className="md:w-4 md:h-4" />
              Hunter
            </button>
          </div>
        </div>

        {/* Tab Content */}
        <div className="w-full">
          {activeTab === "portfolio" && (
            <div className="w-full animate-in fade-in duration-300">
              <div className="bg-onyx border border-shadow-grey rounded-xl md:rounded-2xl p-5 md:p-6 text-center text-granite">
                <p className="text-xs md:text-sm">Portfolio details and history will appear here.</p>
                <p className="text-[10px] md:text-xs mt-2 text-granite/50">Use the Hunter tab to manage your rebate rules.</p>
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