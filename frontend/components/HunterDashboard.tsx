"use client";

import { useEffect, useState, useRef } from "react";
import { toast } from "sonner";
import { HunterDashboardView } from "./hunter/HunterDashboardView";

const apiUrl = "/api/backend";
const DAILY_MAX = 25;

export function HunterDashboard() {
  const [status, setStatus] = useState<any>(null);
  const [dailyStats, setDailyStats] = useState<any>(null);
  const [control, setControl] = useState<any>(null);
  const [activeSubTab, setActiveSubTab] = useState<
    "status" | "engine" | "rules" | "history"
  >("status");

  const [rules, setRules] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [availableTokens, setAvailableTokens] = useState<string[]>([]);
  const [walletBalances, setWalletBalances] = useState<Record<string, string>>({});
  const [portfolio, setPortfolio] = useState<any>(null);
  const [slotStatus, setSlotStatus] = useState<any>(null);
  const [isRequestingSlot, setIsRequestingSlot] = useState(false);
  const [isReleasingSlot, setIsReleasingSlot] = useState(false);

  const [isSaving, setIsSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [isSavingAllowance, setIsSavingAllowance] = useState(false);
  const [isClearingLock, setIsClearingLock] = useState(false);

  const [allowanceInput, setAllowanceInput] = useState("25");
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
      const res = await fetch(`${apiUrl}/api/hunter-control`, {
        cache: "no-store",
      });

      if (!res.ok) return;

      const data = await res.json();
      setControl(data);

      if (
        !allowanceDirtyRef.current &&
        data.swaps_remaining !== undefined &&
        data.swaps_remaining !== null
      ) {
        setAllowanceInput(String(data.swaps_remaining));
      }
    } catch {
      // Keep the last known control state if the API is temporarily unavailable.
    }
  };

  const fetchSlotStatus = async () => {
    try {
      const res = await fetch(`${apiUrl}/api/slot/status`, {
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) setSlotStatus(data);
    } catch {
      // Keep the last known slot state during temporary API interruptions.
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
      } catch {
        toast.error("Could not load tokens.");
      }
    };

    const fetchPortfolio = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/portfolio-value`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === "success") {
          setPortfolio(data);
          const balances: Record<string, string> = {};
          (Array.isArray(data.tokens) ? data.tokens : []).forEach((token: any) => {
            balances[String(token.symbol).toUpperCase()] = String(token.available ?? "0");
          });
          setWalletBalances(balances);
        }
      } catch {
        // Keep the last successful portfolio snapshot during temporary API outages.
      }
    };

    fetchAllTokens();
    fetchPortfolio();
    const portfolioInterval = window.setInterval(() => { void fetchPortfolio(); }, 30000);
    return () => window.clearInterval(portfolioInterval);
  }, []);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (aRef.current && !aRef.current.contains(event.target as Node)) {
        setIsAOpen(false);
      }

      if (bRef.current && !bRef.current.contains(event.target as Node)) {
        setIsBOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${apiUrl}/api/hunter-status`, {
        cache: "no-store",
      });

      if (res.ok) setStatus(await res.json());
    } catch {
      setStatus({ status: "OFFLINE" });
    }
  };

  useEffect(() => {
    const fetchRules = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-rules`, {
          cache: "no-store",
        });

        if (!res.ok) return;

        const data = await res.json();
        setRules(Array.isArray(data) ? data : []);
      } catch {
        setRules([]);
      }
    };

    const fetchDailyStats = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-daily-stats`, {
          cache: "no-store",
        });

        if (!res.ok) return;

        const data = await res.json();
        if (data.status === "success") setDailyStats(data);
      } catch {
        setDailyStats(null);
      }
    };

    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/hunter-history`, {
          cache: "no-store",
        });

        if (!res.ok) return;

        const data = await res.json();
        setHistory(Array.isArray(data) ? data : []);
      } catch {
        setHistory([]);
      }
    };

    fetchRules();
    fetchStatus();
    fetchControl();
    fetchDailyStats();
    fetchHistory();
    fetchSlotStatus();

    const slotInterval = window.setInterval(() => {
      void fetchSlotStatus();
    }, 30000);

    const statusInterval = window.setInterval(() => {
      void fetchStatus();
    }, 10000);

    const controlInterval = window.setInterval(() => {
      void fetchControl();
    }, 30000);

    const dailyStatsInterval = window.setInterval(() => {
      void fetchDailyStats();
    }, 120000);

    const historyInterval = window.setInterval(() => {
      void fetchHistory();
    }, 120000);

    return () => {
      window.clearInterval(statusInterval);
      window.clearInterval(slotInterval);
      window.clearInterval(controlInterval);
      window.clearInterval(dailyStatsInterval);
      window.clearInterval(historyInterval);
    };
  }, []);

  const handleRequestSlot = async () => {
    if (isRequestingSlot || isReleasingSlot) return;
    setIsRequestingSlot(true);

    try {
      const res = await fetch(`${apiUrl}/api/slot/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        toast.error(data.detail || data.message || "Could not request a Hunter slot.");
        await fetchSlotStatus();
        return;
      }

      setSlotStatus((current: any) => ({
        ...current,
        status: "success",
        slot_status: data.slot_status || data.status || "waiting",
        capacity: data.capacity || current?.capacity,
      }));
      toast.success(data.message || "Hunter slot request submitted.");
      await fetchSlotStatus();
      await fetchStatus();
    } catch {
      toast.error("Could not connect to request a Hunter slot.");
    } finally {
      setIsRequestingSlot(false);
    }
  };

  const handleReleaseSlot = async () => {
    if (isRequestingSlot || isReleasingSlot) return;

    if (!window.confirm("Stop your Hunter and release your slot?")) return;

    setIsReleasingSlot(true);
    try {
      const res = await fetch(`${apiUrl}/api/slot/release`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        toast.error(data.detail || data.message || "Could not release the Hunter slot.");
        await fetchSlotStatus();
        return;
      }

      toast.success(data.message || "Hunter slot released.");
      await fetchSlotStatus();
      await fetchStatus();
    } catch {
      toast.error("Could not connect to release the Hunter slot.");
    } finally {
      setIsReleasingSlot(false);
    }
  };

  const handleSaveAllowance = async () => {
    const value = Number(allowanceInput);

    if (!Number.isInteger(value) || value < 0 || value > DAILY_MAX) {
      toast.error(`Enter a whole number from 0 to ${DAILY_MAX}.`);
      return;
    }

    setIsSavingAllowance(true);

    try {
      const res = await fetch(`${apiUrl}/api/hunter-allowance`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ manual_swaps_today: value }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        toast.error(
          data.detail ||
            data.message ||
            "Could not update the daily allowance."
        );
        return;
      }

      allowanceDirtyRef.current = false;
      await fetchControl();

      toast.success(`Daily allowance updated to ${value} remaining swaps.`);
    } catch {
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

    if (
      !window.confirm(
        "Confirm that you have reviewed the incident and want to allow the Hunter to resume?"
      )
    ) {
      return;
    }

    setIsClearingLock(true);

    try {
      const res = await fetch(`${apiUrl}/api/manual-intervention/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          acknowledged: true,
          resolution_note: note,
        }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        toast.error(
          data.detail ||
            data.message ||
            "Manual intervention could not be cleared."
        );
        return;
      }

      setResolutionNote("");
      await fetchControl();

      toast.success("Manual intervention cleared. The Hunter may resume.");
    } catch {
      toast.error("Could not connect to clear manual intervention.");
    } finally {
      setIsClearingLock(false);
    }
  };

  const handleSaveRule = async () => {
    if (isSaving) return;

    if (!tokenA || !tokenB) {
      toast.error("Select both tokens.");
      return;
    }

    if (!netMin || !netMax) {
      toast.error("Enter fee min and max.");
      return;
    }

    if (tokenA === tokenB) {
      toast.error("Tokens must be different.");
      return;
    }

    if (Number(netMin) < 0 || Number(netMax) < Number(netMin)) {
      toast.error("Check the network fee range.");
      return;
    }

    setIsSaving(true);

    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token_a: tokenA,
          token_b: tokenB,
          net_min: netMin,
          net_max: netMax,
        }),
      });

      if (res.ok) {
        toast.success("Rule saved!");

        const rulesResponse = await fetch(`${apiUrl}/api/hunter-rules`);

        if (rulesResponse.ok) {
          const data = await rulesResponse.json();
          setRules(Array.isArray(data) ? data : []);
        }

        setNetMin("");
        setNetMax("");
        setTokenA("");
        setTokenB("");
      } else {
        toast.error("Failed to save rule.");
      }
    } catch {
      toast.error("Error connecting.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteRule = async (id: number) => {
    if (deletingId !== null) return;

    setDeletingId(id);

    try {
      const res = await fetch(`${apiUrl}/api/hunter-rules/${id}`, {
        method: "DELETE",
      });

      if (res.ok) {
        toast.success("Rule deleted.");
        setRules(current => current.filter(r => r.id !== id));
      } else {
        toast.error("Failed to delete rule.");
      }
    } catch {
      toast.error("Could not connect.");
    } finally {
      setDeletingId(null);
    }
  };

  const isGreen = status?.status?.includes("MET");
  const pair = status?.pair ? status.pair.split("->") : ["---", "---"];
  const isManualLocked = Boolean(control?.manual_lock);

  return (
    <HunterDashboardView
      status={status}
      dailyStats={dailyStats}
      control={control}
      activeSubTab={activeSubTab}
      setActiveSubTab={setActiveSubTab}
      rules={rules}
      history={history}
      availableTokens={availableTokens}
      walletBalances={walletBalances}
      portfolio={portfolio}
      slotStatus={slotStatus}
      isRequestingSlot={isRequestingSlot}
      isReleasingSlot={isReleasingSlot}
      handleRequestSlot={handleRequestSlot}
      handleReleaseSlot={handleReleaseSlot}
      isSaving={isSaving}
      deletingId={deletingId}
      isSavingAllowance={isSavingAllowance}
      isClearingLock={isClearingLock}
      allowanceInput={allowanceInput}
      setAllowanceInput={(value: string) => {
        setAllowanceInput(value);
        allowanceDirtyRef.current = true;
      }}
      resolutionNote={resolutionNote}
      setResolutionNote={setResolutionNote}
      tokenA={tokenA}
      setTokenA={setTokenA}
      tokenB={tokenB}
      setTokenB={setTokenB}
      netMin={netMin}
      setNetMin={setNetMin}
      netMax={netMax}
      setNetMax={setNetMax}
      isAOpen={isAOpen}
      setIsAOpen={setIsAOpen}
      isBOpen={isBOpen}
      setIsBOpen={setIsBOpen}
      aRef={aRef}
      bRef={bRef}
      handleClearManualLock={handleClearManualLock}
      handleSaveAllowance={handleSaveAllowance}
      handleSaveRule={handleSaveRule}
      handleDeleteRule={handleDeleteRule}
      isGreen={isGreen}
      pair={pair}
      isManualLocked={isManualLocked}
      dailyMax={DAILY_MAX}
    />
  );
}