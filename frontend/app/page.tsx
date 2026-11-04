"use client";

import { useState, useEffect } from "react";
import { OrderForm } from "@/components/orderForm";
import { OrderTable } from "@/components/orderTable";
import { MarketBar } from "@/components/marketBar"; 
import { getBalances, getLivePrice } from "@/lib/api"; // <-- Import these
import { Wallet, Activity, Bell, PlusCircle, List, TrendingUp } from "lucide-react";

export default function Home() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [activeTab, setActiveTab] = useState<"create" | "orders">("create");
  const [isMounted, setIsMounted] = useState(false);
  
  // Portfolio Value State
  const [totalPortfolioValue, setTotalPortfolioValue] = useState<number>(0);
  const [isFetchingValue, setIsFetchingValue] = useState(false);

  // Load saved tab safely after mounting
  useEffect(() => {
    const saved = localStorage.getItem("cantex_activeTab");
    if (saved === "create" || saved === "orders") {
      setActiveTab(saved);
    }
    setIsMounted(true);
  }, []);

  // Save tab whenever it changes (but only after initial mount)
  useEffect(() => {
    if (isMounted) {
      localStorage.setItem("cantex_activeTab", activeTab);
    }
  }, [activeTab, isMounted]);

    // Fetch Total Portfolio Value (Optimized)
  useEffect(() => {
    let isCancelled = false;

    const fetchPortfolioValue = async () => {
      setIsFetchingValue(true);
      
      // Timeout safety: if it takes more than 6 seconds, stop
      const timeoutId = setTimeout(() => {
        if (!isCancelled) setIsFetchingValue(false);
      }, 6000);

      try {
        const balances = await getBalances();
        
        // Filter only tokens with a balance > 0
        const tokensWithBalance = Object.entries(balances).filter(
          ([_, amount]) => parseFloat(amount) > 0
        );

        if (tokensWithBalance.length === 0) {
          if (!isCancelled) setTotalPortfolioValue(0);
          return;
        }

        // Fetch all prices in parallel (fast)
        const pricePromises = tokensWithBalance.map(async ([token, amount]) => {
          try {
            const priceData = await getLivePrice(token, "USDC");
            if (priceData.price && priceData.price !== "0") {
              return parseFloat(amount) * parseFloat(priceData.price);
            }
          } catch (e) {
            // Ignore individual token errors
          }
          return 0;
        });

        const results = await Promise.all(pricePromises);
        const total = results.reduce((sum, val) => sum + val, 0);

        if (!isCancelled) {
          setTotalPortfolioValue(total);
        }
      } catch (err) {
        console.error("Failed to calculate portfolio value", err);
      } finally {
        clearTimeout(timeoutId);
        if (!isCancelled) setIsFetchingValue(false);
      }
    };
    
    fetchPortfolioValue();
    
    // Refresh every 60 seconds (saves Railway usage)
    const interval = setInterval(fetchPortfolioValue, 60000);
    
    return () => {
      isCancelled = true;
      clearInterval(interval);
    };
  }, []);

  const handleOrderCreated = () => {
    setRefreshTrigger((p) => p + 1);
  };

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
          <div className="flex items-center gap-2 cursor-pointer text-emerald">
            <Activity size={16} />
            <span>Dashboard</span>
          </div>
          <div className="flex items-center gap-2 cursor-pointer hover:text-snow transition-colors">
            <Wallet size={16} />
            <span>Wallet</span>
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

      {/* MARKET BAR (Sticky) */}
      <div className="sticky top-[57px] md:top-[69px] z-40 w-full overflow-hidden">
        <MarketBar />
      </div>

      {/* Main Content */}
      <main className="flex-1 max-w-5xl w-full mx-auto p-4 md:p-8 pb-24 md:pb-8">
        
        {/* --- NEW: TOTAL PORTFOLIO VALUE CARD --- */}
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

        {/* Header & Tabs */}
        <div className="mb-6 flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-medium text-snow mb-1">Trading Dashboard</h1>
            <p className="text-granite text-sm">Manage your semi-automated strategies.</p>
          </div>

          {/* Tab Switcher */}
          <div className="flex bg-carbon p-1 rounded-xl border border-jet-black w-full md:w-auto">
            <button
              onClick={() => setActiveTab("create")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-lg transition-all ${
                activeTab === "create" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <PlusCircle size={16} />
              Create Trade
            </button>
            <button
              onClick={() => setActiveTab("orders")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-lg transition-all ${
                activeTab === "orders" ? "bg-jet-black text-emerald shadow-sm" : "text-granite hover:text-snow"
              }`}
            >
              <List size={16} />
              Active Orders
            </button>
          </div>
        </div>

        {/* Tab Content */}
        <div className="w-full">
          {activeTab === "create" && (
            <div className="max-w-xl mx-auto w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
              <OrderForm onOrderCreated={handleOrderCreated} />
            </div>
          )}

          {activeTab === "orders" && (
            <div className="w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
              <OrderTable refreshTrigger={refreshTrigger} />
            </div>
          )}
        </div>

      </main>

      {/* MOBILE BOTTOM NAVBAR */}
      <nav className="md:hidden fixed bottom-0 left-0 w-full bg-onyx border-t border-shadow-grey flex justify-around items-center pt-2 pb-6 z-50">
        <div 
          onClick={() => setActiveTab("create")}
          className={`flex flex-col items-center cursor-pointer transition-colors ${activeTab === "create" ? "text-emerald" : "text-granite"}`}
        >
          <PlusCircle size={20} />
          <span className="text-[10px] mt-1 font-medium">Create</span>
        </div>
        <div 
          onClick={() => setActiveTab("orders")}
          className={`flex flex-col items-center cursor-pointer transition-colors ${activeTab === "orders" ? "text-emerald" : "text-granite"}`}
        >
          <List size={20} />
          <span className="text-[10px] mt-1 font-medium">Orders</span>
        </div>
        <div className="flex flex-col items-center text-granite hover:text-snow cursor-pointer transition-colors">
          <Wallet size={20} />
          <span className="text-[10px] mt-1">Wallet</span>
        </div>
      </nav>

    </div>
  );
}