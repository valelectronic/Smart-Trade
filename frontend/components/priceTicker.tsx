"use client";

import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, Activity } from "lucide-react";
import { AreaChart, Area, ResponsiveContainer, YAxis, Tooltip } from "recharts";
import { Card } from "./ui/card";

export function PriceTicker({ 
  sellToken, 
  buyToken, 
  action 
}: { 
  sellToken: string; 
  buyToken: string; 
  action: "BUY" | "SELL";
}) {
  const [price, setPrice] = useState<number | null>(null);
  const [prevPrice, setPrevPrice] = useState<number | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    if (!sellToken || !buyToken || sellToken === buyToken) return;

    setPrice(null);
    setPrevPrice(null);
    setHistory([]);
    setStatus("loading");

    const targetToken = buyToken.toUpperCase(); 
    
    if (targetToken === "CC") {
      setStatus("unavailable");
      return;
    }

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";
    const wsUrl = apiUrl.replace("https://", "wss://").replace("http://", "ws://").replace("/api", "");

    const market1 = `${targetToken}-CC`;
    const market2 = `CC-${targetToken}`;

    fetch(`${apiUrl}/subscribe?market=${market1}`, { method: "POST" }).catch(console.error);
    fetch(`${apiUrl}/subscribe?market=${market2}`, { method: "POST" }).catch(console.error);

    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/history?sell=${targetToken}&buy=CC`);
        const data = await res.json();
        setHistory(data);
      } catch (error) {}
    };
    fetchHistory();

    const fetchPrices = async () => {
      try {
        const res = await fetch(`${apiUrl}/price?sell=${targetToken}&buy=CC`);
        const data = await res.json();
        if (data.price && data.price !== "0") {
          const newPrice = parseFloat(data.price);
          setPrice((currentPrice) => {
            if (currentPrice !== null) setPrevPrice(currentPrice);
            return newPrice;
          });
          setStatus("ready");
        }
      } catch (err) {}
    };

    fetchPrices();
    const interval = setInterval(fetchPrices, 2000);

    const ws = new WebSocket(`${wsUrl}/ws/prices`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.market === market1 || data.market === market2) {
          const newPrice = parseFloat(data.price);
          setPrice((currentPrice) => {
            if (currentPrice !== null) setPrevPrice(currentPrice);
            return newPrice;
          });
          setStatus("ready");
        }
      } catch (err) {}
    };

    const timeoutId = setTimeout(() => {
      setStatus((currentStatus) => {
        if (currentStatus === "loading") return "unavailable";
        return currentStatus;
      });
    }, 8000);

    return () => {
      ws.close();
      clearInterval(interval);
      clearTimeout(timeoutId);
    };
  }, [sellToken, buyToken, action]);

  const isUp = prevPrice !== null && price !== null && price > prevPrice;
  const isDown = prevPrice !== null && price !== null && price < prevPrice;
  
  const displayToken = buyToken.toUpperCase();

    return (
    <Card className="bg-carbon border-jet-black mb-4 p-0 overflow-hidden flex flex-col">
      
      {/* TOP SECTION */}
      <div className="p-3 sm:p-4 flex flex-col sm:flex-row sm:items-center justify-between border-b border-jet-black/50 gap-2">
        
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 bg-jet-black rounded-lg shrink-0">
            <Activity size={16} className={status === "loading" ? "text-emerald animate-pulse" : "text-granite"} />
          </div>
          <div className="min-w-0">
            <p className="text-[10px] sm:text-xs text-granite font-medium uppercase tracking-wider">Live Market Price</p>
            <h3 className="text-base sm:text-lg font-bold text-snow font-mono truncate">
              {displayToken} / USD
            </h3>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0 sm:justify-end">
          {status === "loading" && (
            <span className="text-xs sm:text-sm text-granite animate-pulse">Fetching...</span>
          )}
          {status === "unavailable" && (
            <span className="text-[10px] sm:text-xs text-red-400 font-medium bg-red-500/10 px-2 py-1 rounded">Price Unavailable</span>
          )}
          {status === "ready" && (
            <>
              {/* Changed: Removed break-all, used truncate, adjusted font size */}
              <span className={`text-xl sm:text-2xl md:text-3xl font-mono font-bold truncate transition-colors duration-300 ${isUp ? 'text-emerald' : isDown ? 'text-red-400' : 'text-snow'}`}>
                ${price ? price.toFixed(5) : "0.00000"}
              </span>
              {isUp && <TrendingUp size={18} className="text-emerald shrink-0" />}
              {isDown && <TrendingDown size={18} className="text-red-400 shrink-0" />}
            </>
          )}
        </div>
      </div>
      
      {/* CHART SECTION */}
      <div className="h-20 sm:h-24 w-full bg-onyx-dark/30">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={history} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} stopOpacity={0.4}/>
                <stop offset="95%" stopColor={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <YAxis domain={['dataMin', 'dataMax']} hide />
            <Tooltip 
              contentStyle={{ backgroundColor: '#131317', borderColor: '#252B2F', borderRadius: '8px', color: '#F9F9F9', fontSize: '12px' }}
              itemStyle={{ color: '#22DC95' }}
              labelStyle={{ display: 'none' }}
              formatter={(value: any) => [`$${Number(value).toFixed(5)}`, 'Price']}
            />
            <Area 
              type="monotone" 
              dataKey="price" 
              stroke={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} 
              strokeWidth={2}
              fillOpacity={1} 
              fill="url(#colorPrice)" 
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}