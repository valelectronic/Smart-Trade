"use client";

import { useEffect, useState, useRef, memo } from "react";
import {
  ArrowRight, ShieldCheck, Save, Settings2,
  MonitorPlay, Trash2, TrendingUp, ChevronDown, Loader2, History,
  AlertTriangle, Lock, CheckCircle2
} from "lucide-react";
import { toast } from "sonner";
import { Card } from "./ui/card";
import { Button } from "./ui/Button";

const apiUrl = "/api/backend";
const DAILY_MAX = 100;
const DEFAULT_DAILY_LIMIT = 25;

const TokenDropdown = memo(({ selected, onSelect, isOpen, setIsOpen, refObj, availableTokens, balances, portfolioTokens }: any) => (
  <div ref={refObj} className="relative w-full">
    <button type="button" onClick={() => setIsOpen(!isOpen)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm flex justify-between items-center focus:ring-1 focus:ring-emerald outline-none">
      <span className="break-all text-left pr-2">{selected || "Select..."}</span>
      <ChevronDown size={16} className="text-granite shrink-0" />
    </button>
    {isOpen && (
      <div className="absolute top-full left-0 mt-1 w-full max-h-60 overflow-y-auto bg-carbon border border-jet-black rounded-xl shadow-xl z-50">
        {availableTokens.map((t: string) => {
          const upper = String(t).toUpperCase();
          const available = Number(balances[upper] ?? 0).toLocaleString("en-US", { maximumFractionDigits: 8 });
          const usdValue = portfolioTokens[upper]?.value_usd;
          return (
            <button key={t} type="button" onClick={() => { onSelect(t); setIsOpen(false); }} className={`w-full text-left px-4 py-3 text-sm font-mono hover:bg-jet-black transition-colors flex justify-between gap-2 ${selected === t ? "text-emerald" : "text-granite"}`}>
              <span className="min-w-0">
                <span className="block text-snow">{t}</span>
                <span className="block text-[10px] text-granite font-sans">{available} available</span>
              </span>
              <span className="shrink-0 text-right text-xs text-granite self-center">
                {usdValue != null
                  ? `$${Number(usdValue).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : "—"}
              </span>
            </button>
          );
        })}
      </div>
    )}
  </div>
));
TokenDropdown.displayName = "TokenDropdown";

export function HunterDashboard() {
  const [status, setStatus] = useState<any>(null);
  const [dailyStats, setDailyStats] = useState<any>(null);
  const [control, setControl] = useState<any>(null);
  const [activeSubTab, setActiveSubTab] = useState<"status" | "rules" | "history">("status");
  const [rules, setRules] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [availableTokens, setAvailableTokens] = useState<string[]>([]);
  const [walletBalances, setWalletBalances] = useState<Record<string, string>>({});
  const [portfolio, setPortfolio] = useState<any>(null);

  const [isSaving, setIsSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [isSavingAllowance, setIsSavingAllowance] = useState(false);
  const [isClearingLock, setIsClearingLock] = useState(false);
  const [isClearLockDialogOpen, setIsClearLockDialogOpen] = useState(false);
  const [allowanceInput, setAllowanceInput] = useState(String(DEFAULT_DAILY_LIMIT));
  const allowanceDirtyRef = useRef(false);
  const [resolutionNote, setResolutionNote] = useState("");

  const [tokenA, setTokenA] = useState("");
  const [tokenB, setTokenB] = useState("");
  const [netMin, setNetMin] = useState("0.45");
  const [netMax, setNetMax] = useState("0.60");

  const [isAOpen, setIsAOpen] = useState(false);
  const [isBOpen, setIsBOpen] = useState(false);
  const aRef = useRef<HTMLDivElement>(null);
  const bRef = useRef<HTMLDivElement>(null);

  const fetchControl = async () => {
    try {
      const res = await fetch(`${apiUrl}/api/hunter-control`, { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      setControl(data);
      const savedLimit = data.daily_swap_limit ?? data.daily_limit;
      if (!allowanceDirtyRef.current && savedLimit !== undefined && savedLimit !== null) {
        setAllowanceInput(String(savedLimit));
      }
    } catch (err) {
      // Keep the last known control state if the API is temporarily unavailable.
    }
  };

  useEffect(() => {
    const fetchAllTokens = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/all-tokens`);
        if (!res.ok) return;
        const allTokens = await res.json();
        setAvailableTokens(Array.isArray(allTokens) ? allTokens : []);
        if (Array.isArray(allTokens) && allTokens.length > 0) {
          setTokenA(allTokens[0]);
          setTokenB(allTokens[1] || allTokens[0]);
        }
      } catch (err) { toast.error("Could not load tokens."); }
    };

    fetchAllTokens();
  }, []);

  useEffect(() => {
    const fetchBalances = async () => {
      try {
        // This endpoint supplies the USD valuation fields used by the portfolio card.
        const res = await fetch(`${apiUrl}/api/portfolio-value`, { cache: "no-store" });
        if (!res.ok) return;

        const data = await res.json();

        // Keep the last good snapshot if the response has an unexpected shape.
        if (!data || !Array.isArray(data.tokens)) return;

        setPortfolio(data);

        const upper: Record<string, string> = {};
        data.tokens.forEach((token: any) => {
          if (token?.symbol) {
            upper[String(token.symbol).toUpperCase()] = String(token.available ?? "0");
          }
        });
        setWalletBalances(upper);
      } catch (err) {
        // Retain the last successful balance and portfolio snapshot.
      }
    };

    void fetchBalances();
    const portfolioInterval = window.setInterval(() => { void fetchBalances(); }, 30000);
    return () => window.clearInterval(portfolioInterval);
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
        const res = await fetch(`${apiUrl}/api/hunter-status`, { cache: "no-store" });
        if (res.ok) setStatus(await res.json());
      } catch (err) { setStatus({ status: "OFFLINE" }); }
    };
    const fetchRules = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-rules`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        setRules(Array.isArray(data) ? data : []);
      } catch (err) { setRules([]); }
    };
    const fetchDailyStats = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-daily-stats`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === "success") setDailyStats(data);
      } catch (err) { setDailyStats(null); }
    };
    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-history`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        setHistory(Array.isArray(data) ? data : []);
      } catch (err) { setHistory([]); }
    };

    // Fetch the latest values immediately when the dashboard mounts.
    fetchRules();
    fetchStatus();
    fetchControl();
    fetchDailyStats();
    fetchHistory();

    // Keep fast-changing engine status separate from database-backed dashboard data.
    const statusInterval = window.setInterval(() => {
      void fetchStatus();
    }, 10000);

    // Control state changes infrequently, so poll it less often.
    const controlInterval = window.setInterval(() => {
      void fetchControl();
    }, 30000);

    // Daily totals and history do not need frequent refreshes.
    const dailyStatsInterval = window.setInterval(() => {
      void fetchDailyStats();
    }, 120000);

    const historyInterval = window.setInterval(() => {
      void fetchHistory();
    }, 120000);

    return () => {
      window.clearInterval(statusInterval);
      window.clearInterval(controlInterval);
      window.clearInterval(dailyStatsInterval);
      window.clearInterval(historyInterval);
    };
  }, []);

  const handleSaveAllowance = async () => {
    const value = Number(allowanceInput);
    if (!Number.isInteger(value) || value < 0 || value > DAILY_MAX) {
      toast.error(`Enter a whole number from 0 to ${DAILY_MAX}.`);
      return;
    }
    setIsSavingAllowance(true);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-daily-limit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ daily_swap_limit: value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.detail || data.message || "Could not update the daily allowance.");
        return;
      }
      allowanceDirtyRef.current = false;
      await fetchControl();
      toast.success(`Daily Hunter target updated to ${value} swaps per day.`);
    } catch (err) {
      toast.error("Could not connect to update the daily allowance.");
    } finally {
      setIsSavingAllowance(false);
    }
  };

  const handleClearManualLock = async () => {
    const note = resolutionNote.trim();
    if (note.length < 10) {
      toast.error("Add a review note of at least 10 characters.");
      return;
    }
    setIsClearLockDialogOpen(true);
  };

  const confirmClearManualLock = async () => {
    const note = resolutionNote.trim();
    if (note.length < 10) {
      setIsClearLockDialogOpen(false);
      toast.error("Add a review note of at least 10 characters.");
      return;
    }
    setIsClearLockDialogOpen(false);
    setIsClearingLock(true);
    try {
      const res = await fetch(`${apiUrl}/api/manual-intervention/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ acknowledged: true, resolution_note: note }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.detail || data.message || "Manual intervention could not be cleared.");
        return;
      }
      setResolutionNote("");
      await fetchControl();
      toast.success("Manual intervention cleared. The Hunter may resume.");
    } catch (err) {
      toast.error("Could not connect to clear manual intervention.");
    } finally {
      setIsClearingLock(false);
    }
  };

  const handleSaveRule = async () => {
    if (isSaving) return;
    if (!tokenA || !tokenB) { toast.error("Select both tokens."); return; }
    if (!netMin || !netMax) { toast.error("Enter fee min and max."); return; }
    if (tokenA === tokenB) { toast.error("Tokens must be different."); return; }
    if (Number(netMin) < 0 || Number(netMax) < Number(netMin)) { toast.error("Check the network fee range."); return; }
    setIsSaving(true);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token_a: tokenA, token_b: tokenB, net_min: netMin, net_max: netMax }),
      });
      if (res.ok) {
        const rulesResponse = await fetch(`${apiUrl}/api/hunter-rules`, { cache: "no-store" });
        if (!rulesResponse.ok) throw new Error(`Rules refresh failed: ${rulesResponse.status}`);
        const data = await rulesResponse.json();
        setRules(Array.isArray(data) ? data : []);
        toast.success("Rule saved!");
        setNetMin(""); setNetMax("");
        setTokenA(""); setTokenB("");
      } else {
        toast.error("Failed to save rule.");
      }
    } catch (err) { toast.error("Error connecting."); }
    finally { setIsSaving(false); }
  };

  const handleDeleteRule = async (id: number) => {
    if (deletingId !== null) return;
    setDeletingId(id);
    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules/${id}`, { method: "DELETE" });
      // DELETE may return 204 (no body). Refresh the authoritative list instead of parsing JSON.
      const rulesResponse = await fetch(`${apiUrl}/api/hunter-rules`, { cache: "no-store" });
      if (!rulesResponse.ok) throw new Error(`Rules refresh failed: ${rulesResponse.status}`);
      const data = await rulesResponse.json();
      const refreshedRules = Array.isArray(data) ? data : [];
      const stillExists = refreshedRules.some((rule: any) => rule.id === id);
      setRules(refreshedRules);
      if (res.ok || !stillExists) {
        toast.success("Rule deleted.");
      } else {
        toast.error("The rule is still present. Deletion was not confirmed.");
      }
    } catch (err) { toast.error("Could not connect."); }
    finally { setDeletingId(null); }
  };

  const isManualLocked = Boolean(control?.manual_lock);
  const portfolioTokens: Record<string, any> = Object.fromEntries(
    (portfolio?.tokens || []).map((token: any) => [String(token.symbol).toUpperCase(), token])
  );

  return (
    <div className="w-full max-w-xl mx-auto px-2 sm:px-0">
      <Card className="bg-carbon border-jet-black p-4 mb-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs text-granite">Total portfolio value</p>
            <p className="mt-1 text-2xl sm:text-3xl font-semibold tracking-tight text-snow font-mono">
              {portfolio?.total_usd != null
                ? `$${Number(portfolio.total_usd).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : "—"}
            </p>
            <p className="mt-1 text-xs text-granite">
              {portfolio?.valuation_complete === false
                ? `${portfolio?.unpriced_symbols?.length || 0} token(s) not included in valuation`
                : portfolio?.updated_at
                  ? `Updated ${new Date(portfolio.updated_at * 1000).toLocaleTimeString()}`
                  : "Portfolio valuation unavailable"}
            </p>
          </div>
          <span className="text-xs text-granite border border-jet-black rounded-lg px-2 py-1">
            {portfolio?.tokens?.length ?? 0} assets
          </span>
        </div>
        <details className="mt-4 border-t border-jet-black pt-3">
          <summary className="cursor-pointer list-none flex items-center justify-between text-sm text-snow">
            <span>View token balances and values</span>
            <ChevronDown size={16} className="text-granite" />
          </summary>
          <div className="mt-3 max-h-72 overflow-y-auto divide-y divide-jet-black/70 pr-1">
            {(portfolio?.tokens || []).length === 0 ? (
              <p className="py-3 text-sm text-granite">No portfolio data available.</p>
            ) : portfolio.tokens.map((token: any) => (
              <div key={token.symbol} className="py-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-snow">{token.symbol}</p>
                  <p className="mt-0.5 text-xs text-granite font-mono">
                    {Number(token.available || 0).toLocaleString("en-US", { maximumFractionDigits: 8 })} available
                  </p>
                  <p className="text-[11px] text-granite">
                    {token.price_usd != null
                      ? `$${Number(token.price_usd).toLocaleString("en-US", { maximumFractionDigits: 8 })} per token`
                      : "USD price unavailable"}
                  </p>
                </div>
                <p className="shrink-0 text-sm font-mono text-snow">
                  {token.value_usd != null
                    ? `$${Number(token.value_usd).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                    : "—"}
                </p>
              </div>
            ))}
          </div>
        </details>
      </Card>
      <div className="grid grid-cols-3 gap-1 bg-carbon p-1 rounded-xl border border-jet-black mb-4">
        <button onClick={() => setActiveSubTab("status")} className={`min-w-0 flex items-center justify-center gap-1 sm:gap-2 py-2 px-1 text-[11px] sm:text-xs font-medium rounded-lg transition-all ${activeSubTab === "status" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><MonitorPlay size={14} /> Status</button>
        <button onClick={() => setActiveSubTab("rules")} className={`min-w-0 flex items-center justify-center gap-1 sm:gap-2 py-2 px-1 text-[11px] sm:text-xs font-medium rounded-lg transition-all ${activeSubTab === "rules" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><Settings2 size={14} /> Rules</button>
        <button onClick={() => setActiveSubTab("history")} className={`min-w-0 flex items-center justify-center gap-1 sm:gap-2 py-2 px-1 text-[11px] sm:text-xs font-medium rounded-lg transition-all ${activeSubTab === "history" ? "bg-jet-black text-emerald shadow-sm" : "text-granite"}`}><History size={14} /> History</button>
      </div>

      {activeSubTab === "status" && (
        <>
          {isManualLocked && (
            <Card className="bg-red-950/30 border border-red-500/50 p-4 mb-4">
              <div className="flex items-start gap-3">
                <AlertTriangle size={20} className="text-red-400 shrink-0 mt-0.5" />
                <div className="min-w-0 flex-1">
                  <h2 className="text-sm font-bold text-red-300 flex items-center gap-2"><Lock size={14} /> MANUAL INTERVENTION REQUIRED</h2>
                  <p className="text-xs text-red-200/90 mt-2">The Hunter is locked and will not resume automatically. Review the transaction and account state before clearing this lock.</p>
                  {control?.lock_reason && <p className="text-xs text-snow mt-3 break-words"><span className="text-red-300">Reason:</span> {control.lock_reason}</p>}
                  {control?.lock_tx_id && <p className="text-[11px] text-granite mt-1 break-all">Transaction: {control.lock_tx_id}</p>}
                  {control?.locked_at && <p className="text-[11px] text-granite mt-1">Locked: {new Date(control.locked_at).toLocaleString()}</p>}
                  <div className="mt-4 space-y-2">
                    <label className="block text-xs text-red-200" htmlFor="resolution-note">Review and resolution note</label>
                    <textarea id="resolution-note" value={resolutionNote} onChange={e => setResolutionNote(e.target.value)} rows={3} maxLength={500} placeholder="Describe what you checked and why it is safe to resume..." className="w-full bg-onyx border border-red-500/30 rounded-xl p-3 text-snow text-sm outline-none focus:ring-1 focus:ring-red-400" />
                    <Button onClick={handleClearManualLock} disabled={isClearingLock || resolutionNote.trim().length < 10} className="w-full py-2 rounded-xl flex items-center justify-center gap-2 bg-red-600 text-white">
                      {isClearingLock ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
                      {isClearingLock ? "Clearing..." : "Review & Resume Hunter"}
                    </Button>
                  </div>
                </div>
              </div>
            </Card>
          )}

          {dailyStats && (
            <Card className="bg-carbon border-jet-black p-4 mb-4 animate-in fade-in">
              <h2 className="text-sm font-semibold text-snow flex items-center gap-2 mb-3"><TrendingUp size={16} className="text-emerald" /> Today's Performance</h2>
              <div className="grid grid-cols-2 gap-4">
                <div><p className="text-[10px] text-granite uppercase">Confirmed swaps</p><p className="font-mono text-lg text-snow">{dailyStats.swap_count} / {control?.daily_swap_limit ?? control?.daily_limit ?? DEFAULT_DAILY_LIMIT}</p></div>
                <div><p className="text-[10px] text-granite uppercase">Est. Rebate</p><p className="font-mono text-lg text-emerald">+{Number(dailyStats.estimated_rebate_cc || 0).toFixed(4)} CC</p></div>
                <div><p className="text-[10px] text-granite uppercase">Net Fees</p><p className="font-mono text-sm text-red-400">-{Number(dailyStats.total_net_fee || 0).toFixed(4)} CC</p></div>
                <div><p className="text-[10px] text-granite uppercase">Pool Fees</p><p className="font-mono text-sm text-red-400">-{Number(dailyStats.total_pool_fee_cc || 0).toFixed(4)} CC</p></div>
              </div>
              <div className="mt-3 pt-3 border-t border-jet-black/50 flex justify-between items-center">
                <span className="text-xs font-semibold text-snow">Net Profit (CC)</span>
                <span className={`font-mono text-lg font-bold ${Number(dailyStats.net_profit_cc || 0) >= 0 ? "text-emerald" : "text-red-500"}`}>{Number(dailyStats.net_profit_cc || 0).toFixed(4)} CC</span>
              </div>
            </Card>
          )}

          <Card className="bg-carbon border-jet-black p-4 mb-4">
            <div className="flex flex-col xs:flex-row xs:items-center justify-between gap-2 mb-3">
              <div><h2 className="text-sm font-semibold text-snow">Daily Hunter Target</h2><p className="text-[11px] text-granite mt-1">Set how many successful swaps the Hunter may execute each UTC day. External manual swaps are not counted.</p></div>
              <span className="text-xs text-granite">0–{DAILY_MAX}</span>
            </div>
            <div className="flex gap-2">
              <input type="number" min="0" max={DAILY_MAX} step="1" value={allowanceInput} onChange={e => { setAllowanceInput(e.target.value); allowanceDirtyRef.current = true; }} className="min-w-0 flex-1 bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm outline-none" />
              <Button onClick={handleSaveAllowance} disabled={isSavingAllowance} className="px-4 rounded-xl flex items-center gap-2">
                {isSavingAllowance ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                Save
              </Button>
            </div>
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-granite">
              <span>Target: <span className="text-snow font-mono">{control?.daily_swap_limit ?? control?.daily_limit ?? "--"}</span></span>
              <span>Completed: <span className="text-snow font-mono">{control?.swaps_completed ?? dailyStats?.swap_count ?? "--"}</span></span>
              <span>Remaining: <span className="text-emerald font-mono">{control?.swaps_remaining ?? "--"}</span></span>
              <span>Resets at 00:00 UTC (01:00 Nigeria time).</span>
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
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                  <TokenDropdown selected={tokenA} onSelect={setTokenA} isOpen={isAOpen} setIsOpen={setIsAOpen} refObj={aRef} availableTokens={availableTokens} balances={walletBalances} portfolioTokens={portfolioTokens} />
                  <ArrowRight size={16} className="text-granite shrink-0 self-center rotate-90 sm:rotate-0" />
                  <TokenDropdown selected={tokenB} onSelect={setTokenB} isOpen={isBOpen} setIsOpen={setIsBOpen} refObj={bRef} availableTokens={availableTokens} balances={walletBalances} portfolioTokens={portfolioTokens} />
                </div>
              </div>
              <div className="bg-carbon rounded-2xl p-4 border border-jet-black">
                <p className="text-xs text-granite uppercase mb-3">Network Fee Target (CC)</p>
                <div className="grid grid-cols-2 gap-2">
                  <div className="min-w-0"><label className="text-[10px] text-granite mb-1 block">Min</label><input type="number" step="0.01" value={netMin} onChange={e => setNetMin(e.target.value)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm outline-none" /></div>
                  <div className="min-w-0"><label className="text-[10px] text-granite mb-1 block">Max</label><input type="number" step="0.01" value={netMax} onChange={e => setNetMax(e.target.value)} className="w-full bg-onyx border border-jet-black rounded-xl p-3 text-snow font-mono text-sm outline-none" /></div>
                </div>
              </div>
              <Button onClick={handleSaveRule} disabled={isSaving} className="w-full py-3 rounded-xl font-semibold flex items-center justify-center gap-2">
                {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} {isSaving ? "Saving..." : "Save Rule"}
              </Button>
            </div>
          </Card>

          <Card className="bg-onyx border-shadow-grey w-full">
            <div className="flex items-center justify-between gap-2 mb-4 pb-3 border-b border-jet-black"><h2 className="text-base font-semibold text-snow">Active Rules</h2></div>
            <div className="space-y-2">
              {rules.length === 0 ? <p className="text-sm text-granite text-center py-4">No active rules.</p> : rules.map(rule => (
                <div key={rule.id} className="bg-carbon border border-jet-black rounded-xl p-3 flex items-center justify-between">
                  <div className="min-w-0 flex flex-wrap items-center gap-2 sm:gap-3 font-mono text-sm">
                    <span className="text-snow">{rule.token_a}</span><ArrowRight size={12} className="text-granite" /><span className="text-snow">{rule.token_b}</span>
                    <span className="text-xs text-granite ml-2">({rule.net_min} - {rule.net_max})</span>
                  </div>
                  <Button variant="danger" onClick={() => handleDeleteRule(rule.id)} disabled={deletingId === rule.id} className="px-2 py-1">
                    {deletingId === rule.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                  </Button>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      {activeSubTab === "history" && (
        <Card className="bg-onyx border-shadow-grey w-full">
          <div className="flex items-center justify-between mb-4 pb-3 border-b border-jet-black"><h2 className="text-base font-semibold text-snow flex items-center gap-2"><History size={16} className="text-emerald" /> History</h2></div>
          <div className="space-y-2">
            {history.length === 0 ? <p className="text-sm text-granite text-center py-4">No swaps today.</p> : history.map(swap => (
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
                  <span className="truncate w-32">ID: {String(swap.tx_id || "").split("-")[0]}...</span>
                  <span>Pool: {parseFloat(swap.pool_fee).toFixed(4)}%</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {isClearLockDialogOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) setIsClearLockDialogOpen(false); }}>
          <div role="alertdialog" aria-modal="true" aria-labelledby="clear-lock-title" aria-describedby="clear-lock-description" className="w-full max-w-md rounded-2xl border border-red-500/40 bg-carbon p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <AlertTriangle size={22} className="mt-0.5 shrink-0 text-red-400" />
              <div>
                <h2 id="clear-lock-title" className="text-base font-semibold text-snow">Allow the Hunter to resume?</h2>
                <p id="clear-lock-description" className="mt-2 text-sm leading-relaxed text-granite">Confirm that you have reviewed the incident and account state. Clearing this manual intervention allows the Hunter to resume.</p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setIsClearLockDialogOpen(false)} className="rounded-xl px-4">Cancel</Button>
              <Button onClick={confirmClearManualLock} disabled={isClearingLock} className="rounded-xl bg-red-600 px-4 text-white hover:bg-red-700">Confirm & Resume</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
