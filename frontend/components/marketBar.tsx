"use client";

import { useEffect, useState } from "react";
import { Bitcoin, Coins } from "lucide-react";

export function MarketBar() {
  const [btcPrice, setBtcPrice] = useState<number | null>(null);
  const [ccPrice, setCcPrice] = useState<number | null>(null);

  useEffect(() => {
    // Determine the API and WebSocket URLs dynamically
    const apiUrl = "https://smart-trade-production-44e4.up.railway.app/api";
const wsUrl = "wss://smart-trade-production-44e4.up.railway.app";

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
    <div className="flex items-center gap-6 bg-onyx border-b border-shadow-grey px-4 md:px-8 py-2 text-sm font-mono overflow-x-auto whitespace-nowrap">
      <div className="flex items-center gap-2">
        <Bitcoin size={16} className="text-amber-500" />
        <span className="text-granite">BTC/USD</span>
        <span className="text-snow font-bold">
          ${btcPrice ? btcPrice.toLocaleString(undefined, { minimumFractionDigits: 2 }) : "Loading..."}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Coins size={16} className="text-emerald" />
        <span className="text-granite">CC/USDC</span>
        <span className="text-snow font-bold">
          ${ccPrice ? ccPrice.toFixed(5) : "Loading..."}
        </span>
      </div>
    </div>
  );
}