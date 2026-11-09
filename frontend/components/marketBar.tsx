"use client";

import { useEffect, useState } from "react";
import { Bitcoin, Coins } from "lucide-react";

export function MarketBar() {
  const [btcPrice, setBtcPrice] = useState<number | null>(null);
  const [ccPrice, setCcPrice] = useState<number | null>(null);

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";
    const wsUrl = apiUrl.replace("https://", "wss://").replace("http://", "ws://").replace("/api", "");

    // Polling fallback (ensures prices always update)
    const fetchPrices = async () => {
      try {
        const btcRes = await fetch(`${apiUrl}/price?sell=BTC&buy=USDC`);
        const btcData = await btcRes.json();
        if (btcData.price && btcData.price !== "0") setBtcPrice(parseFloat(btcData.price));

        const ccRes = await fetch(`${apiUrl}/price?sell=CC&buy=USDC`);
        const ccData = await ccRes.json();
        if (ccData.price && ccData.price !== "0") setCcPrice(parseFloat(ccData.price));
      } catch (err) {}
    };

    fetchPrices();
    const interval = setInterval(fetchPrices, 5000);

    // WebSocket for instant updates
    const ws = new WebSocket(`${wsUrl}/ws/prices`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.market === "BTC-USDC") setBtcPrice(parseFloat(data.price));
        if (data.market === "CC-USDC") setCcPrice(parseFloat(data.price));
      } catch (err) {}
    };

    return () => {
      ws.close();
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="flex items-center justify-between bg-onyx border-b border-shadow-grey px-4 md:px-8 py-2 text-xs md:text-sm font-mono w-full overflow-hidden">
      <div className="flex items-center gap-1.5 md:gap-2">
        <Bitcoin size={14} className="text-amber-500 shrink-0 md:w-4 md:h-4" />
        <span className="text-granite">BTC/USD</span>
        <span className="text-snow font-bold">
          {btcPrice ? `$${btcPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "Loading..."}
        </span>
      </div>
      <div className="flex items-center gap-1.5 md:gap-2">
        <Coins size={14} className="text-emerald shrink-0 md:w-4 md:h-4" />
        <span className="text-granite">CC/USDC</span>
        <span className="text-snow font-bold">
          {ccPrice ? `$${ccPrice.toFixed(5)}` : "Loading..."}
        </span>
      </div>
    </div>
  );
}