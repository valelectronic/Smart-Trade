"use client";

import { useEffect, useState, useRef, memo } from "react";
import { Activity, ArrowRight, Zap, Clock, ShieldCheck, Save, Settings2, MonitorPlay, Trash2, TrendingUp, ChevronDown, Loader2, History, Pause, Play } from "lucide-react";
import { toast } from "sonner";
import { Card } from "./ui/card";
import { Button } from "./ui/Button";

const apiUrl = "https://smart-trade-production-44e4.up.railway.app";

const TokenDropdown = memo(({ selected, onSelect, isOpen, setIsOpen, refObj, availableTokens, balances }: any) => {
  return (
    <div ref={refObj} className="relative w-full">
      <button type="button" onClick={() => setIsOpen(!isOpen)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm flex justify-between items-center focus:ring-1 focus:ring-emerald outline-none">
        <span className="break-all text-left pr-2">{selected || "Select..."}</span>
        <ChevronDown size={16} className="text-granite shrink-0" />
      </button>
      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-full max-h-60 overflow-y-auto bg-carbon border border-jet-black rounded-xl shadow-xl z-50">
          {availableTokens.map((t: string) => (
            <button key={t} type="button" onClick={() => { onSelect(t); setIsOpen(false); }} className={`w-full text-left px-4 py-3 text-sm font-mono hover:bg-jet-black transition-colors flex justify-between ${selected === t ? 'text-emerald' : 'text-granite'}`}>
              <span>{t}</span>
              <span className="text-xs text-granite">{balances[t] ? parseFloat(balances[t]).toFixed(4) : "0.00"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
TokenDropdown.displayName = "TokenDropdown";

export function HunterDashboard() {
  const [status, setStatus] = useState<any>(null);
  const [dailyStats, setDailyStats] = useState<any>(null);
  const [activeSubTab, setActiveSubTab] = useState<"status" | "rules" | "history">("status");
  const [rules, setRules] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [availableTokens, setAvailableTokens] = useState<string[]>([]);
  const [walletBalances, setWalletBalances] = useState<Record<string, string>>({});
  
  const [isSaving, setIsSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [isTogglingPause, setIsTogglingPause] = useState(false);

  const [tokenA, setTokenA] = useState("");
  const [tokenB, setTokenB] = useState("");
  const [netMin, setNetMin] = useState("0.45");
  const [netMax, setNetMax] = useState("0.60");

  const [isAOpen, setIsAOpen] = useState(false);
  const [isBOpen, setIsBOpen] = useState(false);
  const aRef = useRef<HTMLDivElement>(null);
  const bRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchAllTokens = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/all-tokens`);
        const allTokens = await res.json();
        setAvailableTokens(allTokens);
        if (allTokens.length > 0) {
          setTokenA(allTokens[0]);
          setTokenB(allTokens[1] || allTokens[0]);
        }
      } catch (err) { toast.error("Could not load tokens."); }
    };

    const fetchBalances = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/balances`);
        if (!res.ok) return;
        const data = await res.json();
        const upper: Record<string, string> = {};
        Object.keys(data).forEach(k => upper[k.toUpperCase()] = data[k]);
        setWalletBalances(upper);
      } catch (err) {}
    };

    fetchAllTokens();
    fetchBalances();
  }, []);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (aRef.current && !aRef.current.contains(event.target as Node)) setIsAOpen(false);
      if (bRef.current && !bRef.current.contains(event.target as Node)) setIsBOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-status`);
        setStatus(await res.json());
      } catch (err) { setStatus({ status: "OFFLINE" }); }
    };
    const fetchRules = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-rules`);
        const data = await res.json();
        setRules(Array.isArray(data) ? data : []);
      } catch (err) { setRules([]); }
    };
    const fetchDailyStats = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-daily-stats`);
        const data = await res.json();
        if (data.status === "success") setDailyStats(data);
      } catch (err) { setDailyStats(null); }
    };
    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-history`);
        const data = await res.json();
        setHistory(Array.isArray(data) ? data : []);
      } catch (err) { setHistory([]); }
    };
    const fetchPauseState = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-pause`);
        const data = await res.json();
        setIsPaused(data.paused);
      } catch (err) {}
    };

    fetchStatus();
    fetchRules();
    fetchDailyStats();
    fetchHistory();
    fetchPauseState();

    const interval = setInterval(() => {
      fetchStatus();
      fetchDailyStats();
      fetchPauseState();
      fetchHistory();
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  const togglePause = async () => {
    setIsTogglingPause(true);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-pause`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paused: !isPaused }),
      });
      if (res.ok) {
        setIsPaused(!isPaused);
        toast.success(!isPaused ? "BOT PAUSED" : "BOT RESUMED");
      }
    } catch (err) { toast.error("Failed to toggle pause."); }
    finally { setIsTogglingPause(false); }
  };

    const handleSaveRule = async () => {
    if (isSaving) return;
    if (!tokenA || !tokenB) { toast.error("Select both tokens."); return; }
    if (!netMin || !netMax) { toast.error("Enter fee min and max."); return; }
    if (tokenA === tokenB) { toast.error("Tokens must be different."); return; }
    setIsSaving(true);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token_a: tokenA, token_b: tokenB, net_min: netMin, net_max: netMax }),
      });
      if (res.ok) {
        toast.success("Rule saved!");
        setRules(await fetch(`${apiUrl}/api/hunter-rules`).then(r => r.json()));
        setNetMin(""); setNetMax("");
        setTokenA(""); setTokenB("");
      } else { toast.error("Failed to save rule."); }
    } catch (err) { toast.error("Error connecting."); }
    finally { setIsSaving(false); }
  };

  const handleDeleteRule = async (id: number) => {
    if (deletingId !== null) return;
    setDeletingId(id);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules/${id}`, { method: "DELETE" });
      if (res.ok) {
        toast.success("Rule deleted.");
        setRules(rules.filter(r => r.id !== id));
      }
    } catch (err) {}
    finally { setDeletingId(null); }
  };

  const isGreen = status?.status?.includes("MET");
  const pair = status?.pair ? status.pair.split("->") : ["---", "---"];

  return (
    <div className="max-w-xl mx-auto w-full">
      
      <div className="mb-4 flex justify-end">
        <Button onClick={togglePause} disabled={isTogglingPause} className={`px-4 py-2 rounded-xl font-bold flex items-center gap-2 ${isPaused ? "bg-emerald text-onyx" : "bg-red-500 text-white"}`}>
          {isTogglingPause ? <Loader2 size={16} className="animate-spin" /> : (isPaused ? <Play size={16}/> : <Pause size={16}/>)}
          {isPaused ? "RESUME BOT" : "PAUSE BOT"}
        </Button>
      </div>

      <div className="flex bg-carbon p-1 rounded-xl border border-jet-black mb-4">
        <button onClick={() => setActiveSubTab("status")} className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-all ${activeSubTab === "status" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><MonitorPlay size={14} /> Status</button>
        <button onClick={() => setActiveSubTab("rules")} className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-all ${activeSubTab === "rules" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><Settings2 size={14} /> Rules</button>
        <button onClick={() => setActiveSubTab("history")} className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-all ${activeSubTab === "history" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><History size={14} /> History</button>
      </div>

      {activeSubTab === "status" && (
        <>
          {dailyStats && (
            <Card className="bg-carbon border-jet-black p-4 mb-4 animate-in fade-in">
              <h2 className="text-sm font-semibold text-snow flex items-center gap-2 mb-3"><TrendingUp size={16} className="text-emerald" /> Today's P&L (25 Swap Goal)</h2>
              <div className="grid grid-cols-2 gap-4">
                <div><p className="text-[10px] text-granite uppercase">Swaps</p><p className="font-mono text-lg text-snow">{dailyStats.swap_count} / 25</p></div>
                <div><p className="text-[10px] text-granite uppercase">Est. Rebate</p><p className="font-mono text-lg text-emerald">+{Number(dailyStats.estimated_rebate_cc || 0).toFixed(4)} CC</p></div>
                <div><p className="text-[10px] text-granite uppercase">Net Fees</p><p className="font-mono text-sm text-red-400">-{Number(dailyStats.total_net_fee || 0).toFixed(4)} CC</p></div>
                <div><p className="text-[10px] text-granite uppercase">Pool Fees</p><p className="font-mono text-sm text-red-400">-{Number(dailyStats.total_pool_fee_cc || 0).toFixed(4)} CC</p></div>
              </div>
              <div className="mt-3 pt-3 border-t border-jet-black/50 flex justify-between items-center">
                <span className="text-xs font-semibold text-snow">Net Profit (CC)</span>
                <span className={`font-mono text-lg font-bold ${Number(dailyStats.net_profit_cc || 0) >= 0 ? 'text-emerald' : 'text-red-500'}`}>{Number(dailyStats.net_profit_cc || 0).toFixed(4)} CC</span>
              </div>
            </Card>
          )}
          <Card className="bg-onyx border-shadow-grey w-full mb-4">
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-jet-black">
              <h2 className="text-base font-semibold text-snow flex items-center gap-2"><Activity size={16} className={isGreen ? "text-emerald" : "text-granite"} /> Engine Status</h2>
              <div className={`h-2 w-2 rounded-full ${isGreen ? 'bg-emerald' : 'bg-red-500'} animate-pulse`} />
            </div>
            <div className="bg-carbon rounded-2xl p-4 border border-jet-black mb-4">
              <div className="flex justify-between items-center mb-4">
                <div><p className="text-[10px] text-granite uppercase mb-1">Target</p><div className="flex items-center gap-2 font-mono font-bold text-snow"><span>{pair[0]}</span><ArrowRight size={14} className="text-granite" /><span>{pair[1]}</span></div></div>
                <div className="text-right"><p className="text-[10px] text-granite uppercase mb-1">Swaps Today</p><p className="font-mono font-bold text-emerald text-lg">{status?.swaps_done || 0} <span className="text-xs text-granite">/ 25</span></p></div>
              </div>
              <div className="grid grid-cols-3 gap-2 pt-3 border-t border-jet-black/50">
                <div className="flex flex-col"><span className="text-[10px] text-granite flex items-center gap-1 mb-1"><Zap size={10} /> Net Fee</span><span className={`font-mono text-sm ${isGreen ? 'text-emerald' : 'text-snow'}`}>{status?.net_fee != null ? `${Number(status.net_fee).toFixed(4)} CC` : "---"}</span></div>
                <div className="flex flex-col"><span className="text-[10px] text-granite flex items-center gap-1 mb-1"><Activity size={10} /> Pool Fee</span><span className={`font-mono text-sm ${isGreen ? 'text-emerald' : 'text-snow'}`}>{status?.pool_fee != null ? `${Number(status.pool_fee).toFixed(4)}%` : "---"}</span></div>
                <div className="flex flex-col"><span className="text-[10px] text-granite flex items-center gap-1 mb-1"><Clock size={10} /> Time</span><span className="font-mono text-sm text-snow">{status?.sdk_est_time != null ? `~${Number(status.sdk_est_time).toFixed(1)}s` : "---"}</span></div>
              </div>
            </div>
          </Card>
        </>
      )}

      {activeSubTab === "rules" && (
        <>
          <Card className="bg-onyx border-shadow-grey w-full mb-4">
             <div className="flex items-center justify-between mb-4 pb-3 border-b border-jet-black"><h2 className="text-base font-semibold text-snow flex items-center gap-2"><ShieldCheck size={16} className="text-emerald" /> Add New Rule</h2></div>
            <div className="space-y-4">
              <div className="bg-carbon rounded-2xl p-4 border border-jet-black">
                <p className="text-xs text-granite uppercase mb-3">Token Pair</p>
                <div className="flex items-center gap-2">
                  <TokenDropdown selected={tokenA} onSelect={setTokenA} isOpen={isAOpen} setIsOpen={setIsAOpen} refObj={aRef} availableTokens={availableTokens} balances={walletBalances} />
                  <ArrowRight size={16} className="text-granite shrink-0" />
                  <TokenDropdown selected={tokenB} onSelect={setTokenB} isOpen={isBOpen} setIsOpen={setIsBOpen} refObj={bRef} availableTokens={availableTokens} balances={walletBalances} />
                </div>
              </div>
              <div className="bg-carbon rounded-2xl p-4 border border-jet-black">
                 <p className="text-xs text-granite uppercase mb-3">Network Fee Target (CC)</p>
                 <div className="flex items-center gap-2">
                    <div className="w-1/2"><label className="text-[10px] text-granite mb-1 block">Min</label><input type="number" step="0.01" value={netMin} onChange={(e) => setNetMin(e.target.value)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm outline-none" /></div>
                    <div className="w-1/2"><label className="text-[10px] text-granite mb-1 block">Max</label><input type="number" step="0.01" value={netMax} onChange={(e) => setNetMax(e.target.value)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm outline-none" /></div>
                 </div>
              </div>
              <Button onClick={handleSaveRule} disabled={isSaving} className="w-full py-3 rounded-xl font-semibold flex items-center justify-center gap-2">
                {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} {isSaving ? "Saving..." : "Save Rule"}
              </Button>
            </div>
          </Card>

          <Card className="bg-onyx border-shadow-grey w-full">
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-jet-black"><h2 className="text-base font-semibold text-snow">Active Rules</h2></div>
            <div className="space-y-2">
              {rules.length === 0 ? <p className="text-sm text-granite text-center py-4">No active rules.</p> : (
                rules.map((rule) => (
                  <div key={rule.id} className="bg-carbon border border-jet-black rounded-xl p-3 flex items-center justify-between">
                    <div className="flex items-center gap-3 font-mono text-sm">
                      <span className="text-snow">{rule.token_a}</span><ArrowRight size={12} className="text-granite" /><span className="text-snow">{rule.token_b}</span>
                      <span className="text-xs text-granite ml-2">({rule.net_min} - {rule.net_max})</span>
                    </div>
                    <Button variant="danger" onClick={() => handleDeleteRule(rule.id)} disabled={deletingId === rule.id} className="px-2 py-1">
                      {deletingId === rule.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </Button>
                  </div>
                ))
              )}
            </div>
          </Card>
        </>
      )}

      {activeSubTab === "history" && (
        <Card className="bg-onyx border-shadow-grey w-full">
          <div className="flex items-center justify-between mb-4 pb-3 border-b border-jet-black"><h2 className="text-base font-semibold text-snow flex items-center gap-2"><History size={16} className="text-emerald" /> History</h2></div>
          <div className="space-y-2">
            {history.length === 0 ? <p className="text-sm text-granite text-center py-4">No swaps today.</p> : (
              history.map((swap) => (
                <div key={swap.tx_id} className="bg-carbon border border-jet-black rounded-xl p-3 flex flex-col gap-1.5">
                  <div className="flex justify-between items-center font-mono text-sm">
                    <span className="text-snow font-bold">{swap.pair}</span>
                    <span className="text-xs text-granite">{new Date(swap.created_at).toLocaleTimeString()}</span>
                  </div>
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-granite">Size: <span className="text-snow">{parseFloat(swap.sell_amount).toFixed(4)}</span></span>
                    <span className="text-granite">Net: <span className="text-emerald">{parseFloat(swap.network_fee).toFixed(4)} CC</span></span>
                  </div>
                  <div className="flex justify-between items-center text-[10px] font-mono text-granite/70">
                    <span className="truncate w-32">ID: {swap.tx_id.split("-")[0]}...</span>
                    <span>Pool: {parseFloat(swap.pool_fee).toFixed(4)}%</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>
      )}
    </div>
  );
}