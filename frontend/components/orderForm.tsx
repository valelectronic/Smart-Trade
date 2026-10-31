"use client";

import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { createOrder, getMarkets, getBalances, getLivePrice } from "@/lib/api";
import { Card } from "./ui/card";
import { Button } from "./ui/Button";
import { ChevronDown, Check, ArrowUpDown, AlertCircle } from "lucide-react";
import { PriceTicker } from "./priceTicker";

export function OrderForm({ onOrderCreated }: { onOrderCreated: () => void }) {
  const [isLoading, setIsLoading] = useState(false);
  const [availableTokens, setAvailableTokens] = useState<string[]>([]);
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [livePrice, setLivePrice] = useState<number>(0);
  
  const [isSellOpen, setIsSellOpen] = useState(false);
  const [isBuyOpen, setIsBuyOpen] = useState(false);
  const sellRef = useRef<HTMLDivElement>(null);
  const buyRef = useRef<HTMLDivElement>(null);

  const [sellToken, setSellToken] = useState<string>("");
  const [buyToken, setBuyToken] = useState<string>("");
  const [action, setAction] = useState<"BUY" | "SELL">("SELL");
  const [triggerPrice, setTriggerPrice] = useState("");
  const [amount, setAmount] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  // Fetch tokens and balances, then merge them
  useEffect(() => {
    Promise.all([getMarkets(), getBalances()]).then(([marketData, balanceData]) => {
      const uniqueTokens = new Set<string>();
      
      // 1. Add all tokens from the market data (FORCE UPPERCASE)
      marketData.forEach(m => {
        uniqueTokens.add(m.base.toUpperCase());
        uniqueTokens.add(m.quote.toUpperCase());
      });

      // 2. Add all tokens from the user's balances (FORCE UPPERCASE)
      const upperBalances: Record<string, string> = {};
      Object.keys(balanceData).forEach(token => {
        const upperToken = token.toUpperCase();
        uniqueTokens.add(upperToken);
        upperBalances[upperToken] = balanceData[token];
      });

      // Add CC as a fallback just in case
      uniqueTokens.add("CC");

      const tokens = Array.from(uniqueTokens).sort();
      setAvailableTokens(tokens);
      setBalances(upperBalances);
      
      if (tokens.length > 0) {
        setSellToken(tokens[0]);
        setBuyToken(tokens[1] || tokens[0]);
      }
    }).catch(() => toast.error("Could not load market data."));
  }, []);

  // Fetch live price of the target token when tokens or action change
  useEffect(() => {
    if (sellToken && buyToken && sellToken !== buyToken) {
      const targetToken = action === "SELL" ? buyToken : sellToken;
      
      getLivePrice(targetToken, "CC").then(data => {
        if (data.price && data.price !== "0") {
          setLivePrice(parseFloat(data.price));
        } else {
          setLivePrice(0);
        }
      }).catch(() => setLivePrice(0));
    }
  }, [sellToken, buyToken, action]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (sellRef.current && !sellRef.current.contains(event.target as Node)) setIsSellOpen(false);
      if (buyRef.current && !buyRef.current.contains(event.target as Node)) setIsBuyOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSwapTokens = () => {
    const temp = sellToken;
    setSellToken(buyToken);
    setBuyToken(temp);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg("");

    if (sellToken === buyToken) {
      setErrorMsg("You cannot swap a token for itself.");
      return;
    }

    const currentBalance = parseFloat(balances[sellToken] || "0");
    const requestedAmount = parseFloat(amount);

    if (requestedAmount > currentBalance) {
      setErrorMsg(`Insufficient balance. You only have ${currentBalance} ${sellToken}.`);
      return;
    }

    if (sellToken === "CC" && requestedAmount < 100) {
      setErrorMsg("Minimum trade size is 100 CC.");
      return;
    }

    const priceInput = parseFloat(triggerPrice);
    const tradeValueUsd = sellToken === "CC" ? requestedAmount : requestedAmount * priceInput;
    if (tradeValueUsd < 10) {
      setErrorMsg("Trade size too small. Minimum is ~100 CC equivalent.");
      return;
    }

    const targetToken = action === "SELL" ? buyToken : sellToken;
    let freshPrice = livePrice;
    
    try {
      const priceData = await getLivePrice(targetToken, "CC");
      if (priceData.price && priceData.price !== "0") {
        freshPrice = parseFloat(priceData.price);
        setLivePrice(freshPrice);
      }
    } catch (err) {
      console.error("Could not fetch fresh price before submit");
    }

    if (freshPrice > 0) {
      if (action === "SELL" && priceInput < freshPrice) {
        setErrorMsg(`For SELL RISE, target must be ABOVE the price of ${targetToken} ($${freshPrice.toFixed(5)}).`);
        return;
      }
      if (action === "BUY" && priceInput > freshPrice) {
        setErrorMsg(`For BUY DIP, target must be BELOW the price of ${targetToken} ($${freshPrice.toFixed(5)}).`);
        return;
      }
    }
    
    setIsLoading(true);

    try {
      await createOrder({
        id: crypto.randomUUID(),
        action,
        sell_token: sellToken,
        buy_token: buyToken,
        trigger_price_usd: triggerPrice,
        direction: action === "BUY" ? "BELOW" : "ABOVE",
        amount: amount,
      });
      
      toast.success("Order saved successfully!");
      setTriggerPrice("");
      setAmount("");
      setErrorMsg("");
      onOrderCreated();
      
      // Refresh balances and force UPPERCASE
      getBalances().then(data => {
        const upper: Record<string,string> = {};
        Object.keys(data).forEach(k => upper[k.toUpperCase()] = data[k]);
        setBalances(upper);
      });
    } catch (error: any) {
      toast.error(error.message || "Failed to create order");
    } finally {
      setIsLoading(false);
    }
  };

  const Label = ({ children }: { children: React.ReactNode }) => (
    <label className="block text-xs font-semibold text-granite uppercase tracking-wider mb-2">
      {children}
    </label>
  );

  const TokenDropdown = ({ selected, onSelect, isOpen, setIsOpen, refObj }: { selected: string, onSelect: (t: string) => void, isOpen: boolean, setIsOpen: (b: boolean) => void, refObj: React.RefObject<HTMLDivElement | null> }) => (
    <div ref={refObj} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full bg-carbon border border-jet-black rounded-lg p-3 text-snow font-mono text-sm flex justify-between items-center focus:ring-1 focus:ring-emerald outline-none truncate"
      >
        <span className="truncate">{selected || "Select Token..."}</span>
        <ChevronDown size={16} className="text-granite shrink-0" />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-full max-h-60 overflow-y-auto bg-carbon border border-jet-black rounded-lg shadow-xl z-50">
          {availableTokens.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => { onSelect(t); setIsOpen(false); }}
              className={`w-full text-left px-4 py-3 text-sm font-mono flex items-center justify-between hover:bg-jet-black transition-colors ${selected === t ? 'text-emerald' : 'text-granite'}`}
            >
              <span className="truncate">{t}</span>
              <span className="text-xs text-granite">{balances[t] ? parseFloat(balances[t]).toFixed(4) : "0.00"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <Card className="bg-onyx border-shadow-grey relative z-10">
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-jet-black">
        <h2 className="text-lg font-medium text-snow">New Trade Alert</h2>
        <div className="h-2 w-2 rounded-full bg-emerald shadow-[0_0_8px_rgba(34,220,149,0.8)] animate-pulse" />
      </div>

      <PriceTicker sellToken={sellToken} buyToken={buyToken} action={action} />
      
      <form onSubmit={handleSubmit} className="space-y-5">
        
        <div>
          <div className="flex justify-between items-end mb-2">
            <Label>You Pay (Sell)</Label>
            <span className="text-xs text-granite">Bal: {balances[sellToken] ? parseFloat(balances[sellToken]).toFixed(4) : "0.00"}</span>
          </div>
          <TokenDropdown selected={sellToken} onSelect={setSellToken} isOpen={isSellOpen} setIsOpen={setIsSellOpen} refObj={sellRef} />
        </div>

        <div className="flex justify-center -my-2 relative z-20">
          <button 
            type="button"
            onClick={handleSwapTokens}
            className="bg-jet-black p-2 rounded-full border border-shadow-grey hover:border-emerald hover:text-emerald transition-colors text-granite shadow-lg cursor-pointer"
            title="Swap Pay/Receive"
          >
            <ArrowUpDown size={16} />
          </button>
        </div>

        <div>
          <div className="flex justify-between items-end mb-2">
            <Label>You Receive (Buy)</Label>
            <span className="text-xs text-granite">Bal: {balances[buyToken] ? parseFloat(balances[buyToken]).toFixed(4) : "0.00"}</span>
          </div>
          <TokenDropdown selected={buyToken} onSelect={setBuyToken} isOpen={isBuyOpen} setIsOpen={setIsBuyOpen} refObj={buyRef} />
        </div>

        <div className="pt-2">
          <Label>Action</Label>
          <div className="flex bg-carbon p-1 rounded-lg border border-jet-black">
            <button
              type="button"
              onClick={() => setAction("BUY")}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${action === "BUY" ? "bg-emerald/20 text-emerald" : "text-granite hover:text-snow"}`}
            >
              BUY DIP
            </button>
            <button
              type="button"
              onClick={() => setAction("SELL")}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${action === "SELL" ? "bg-red-500/20 text-red-400" : "text-granite hover:text-snow"}`}
            >
              SELL RISE
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Trigger Price (USD)</Label>
            <input 
              type="number" step="any" required placeholder={livePrice > 0 ? `~${livePrice.toFixed(5)}` : "0.00"}
              className="w-full bg-carbon border border-jet-black rounded-lg p-3 text-snow font-mono text-sm focus:ring-1 focus:ring-emerald outline-none"
              value={triggerPrice} onChange={(e) => setTriggerPrice(e.target.value)}
            />
          </div>
          <div>
            <Label>Amount ({sellToken || '...'})</Label>
            <input 
              type="number" step="any" required placeholder="0.00"
              className="w-full bg-carbon border border-jet-black rounded-lg p-3 text-snow font-mono text-sm focus:ring-1 focus:ring-emerald outline-none"
              value={amount} onChange={(e) => setAmount(e.target.value)}
            />
          </div>
        </div>

        {errorMsg && (
          <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 p-3 rounded-lg border border-red-500/20">
            <AlertCircle size={16} />
            <span>{errorMsg}</span>
          </div>
        )}

        <Button type="submit" isLoading={isLoading} className="w-full mt-4 py-3">
          Create Alert Order
        </Button>
      </form>
    </Card>
  );
}