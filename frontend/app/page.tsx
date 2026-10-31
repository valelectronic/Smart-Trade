"use client";

import { useState } from "react";
import { OrderForm } from "@/components/orderForm";
import { OrderTable } from "@/components/orderTable";
import { MarketBar } from "@/components/marketBar"; 
import { Wallet, Activity, Bell, Home as HomeIcon, PlusCircle, List } from "lucide-react";

export default function Home() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [activeTab, setActiveTab] = useState<"create" | "orders">("create");

  const handleOrderCreated = () => {
    setRefreshTrigger((p) => p + 1);
  };

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

      {/* MARKET BAR (Sticky) */}
      <div className="sticky top-[57px] md:top-[69px] z-40">
        <MarketBar />
      </div>

      {/* Main Content */}
      <main className="flex-1 max-w-5xl w-full mx-auto p-4 md:p-8 pb-24 md:pb-8">
        
        {/* Header & Tabs */}
        <div className="mb-6 flex flex-col md:flex-row md:items-end justify-between gap-4 mt-4">
          <div>
            <h1 className="text-xl md:text-2xl font-medium text-snow mb-1">Trading Dashboard</h1>
            <p className="text-granite text-sm">Manage your semi-automated strategies.</p>
          </div>

          {/* Tab Switcher */}
          <div className="flex bg-carbon p-1 rounded-lg border border-jet-black w-full md:w-auto">
            <button
              onClick={() => setActiveTab("create")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-md transition-all ${
                activeTab === "create" ? "bg-jet-black text-emerald" : "text-granite hover:text-snow"
              }`}
            >
              <PlusCircle size={16} />
              Create Trade
            </button>
            <button
              onClick={() => setActiveTab("orders")}
              className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2 text-sm font-medium rounded-md transition-all ${
                activeTab === "orders" ? "bg-jet-black text-emerald" : "text-granite hover:text-snow"
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