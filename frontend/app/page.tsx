"use client";

import { useState } from "react";
import { OrderForm } from "@/components/orderForm";
import { OrderTable } from "@/components/orderTable";
import { MarketBar } from "@/components/marketBar"; 
import { Wallet, Activity, Bell, Home as HomeIcon } from "lucide-react";

export default function Home() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  return (
    <div className="min-h-screen flex flex-col">
      
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

      {/* MARKET BAR (BTC & CC PRICES) */}
      <MarketBar />

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-8">
        <div className="mb-6">
          <h1 className="text-xl md:text-2xl font-medium text-snow mb-1">Trading Dashboard</h1>
          <p className="text-granite text-sm">Manage your semi-automated strategies.</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-4 order-2 lg:order-1">
            <OrderForm onOrderCreated={() => setRefreshTrigger(p => p + 1)} />
          </div>
          <div className="lg:col-span-8 order-1 lg:order-2">
            <OrderTable refreshTrigger={refreshTrigger} />
          </div>
        </div>
      </main>

      {/* MOBILE BOTTOM NAVBAR */}
      <nav className="md:hidden fixed bottom-0 left-0 w-full bg-onyx border-t border-shadow-grey flex justify-around items-center py-3 z-50">
        <div className="flex flex-col items-center text-emerald cursor-pointer">
          <HomeIcon size={20} />
          <span className="text-[10px] mt-1 font-medium">Home</span>
        </div>
        <div className="flex flex-col items-center text-granite hover:text-snow cursor-pointer transition-colors">
          <Activity size={20} />
          <span className="text-[10px] mt-1">Markets</span>
        </div>
        <div className="flex flex-col items-center text-granite hover:text-snow cursor-pointer transition-colors">
          <Wallet size={20} />
          <span className="text-[10px] mt-1">Wallet</span>
        </div>
      </nav>

    </div>
  );
}