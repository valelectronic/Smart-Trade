"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Trash2, ArrowRight, Loader2, Info } from "lucide-react";
import { getOrders, deleteOrder } from "@/lib/api";
import { Order } from "@/types";
import { Card } from "./ui/card";
import { Button } from "./ui/Button";

interface OrderWithQuote extends Order {
  quote?: {
    network_fee_cc: number;
    pool_fee_percent: number;
    execution_time_seconds: number;
  } | null;
  isFetchingQuote?: boolean;
}

export function OrderTable({ refreshTrigger }: { refreshTrigger: number }) {
  const [orders, setOrders] = useState<OrderWithQuote[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchOrders = async () => {
    setIsLoading(true);
    try {
      const data: Order[] = await getOrders();
      const ordersWithQuotes: OrderWithQuote[] = data.map(o => ({ ...o, quote: null, isFetchingQuote: true }));
      setOrders(ordersWithQuotes);
    } catch (error) {
      toast.error("Failed to load orders");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, [refreshTrigger]);

  // Fetch quotes for all orders every 5 seconds
  useEffect(() => {
    if (orders.length === 0) return;

    const fetchAllQuotes = async () => {
      const updatedOrders = await Promise.all(
        orders.map(async (order) => {
          try {
            const res = await fetch(
  `${process.env.NEXT_PUBLIC_API_URL}/quote?sell=${order.sell_token}&buy=${order.buy_token}&amount=${order.amount}`
);
            const data = await res.json();
            if (data.status === "success") {
              return { ...order, quote: data, isFetchingQuote: false };
            }
            return { ...order, quote: null, isFetchingQuote: false };
          } catch {
            return { ...order, quote: null, isFetchingQuote: false };
          }
        })
      );
      setOrders(updatedOrders);
    };

    fetchAllQuotes();
    const interval = setInterval(fetchAllQuotes, 5000);
    return () => clearInterval(interval);
  }, [orders.length]);

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await deleteOrder(id);
      toast.success("Order deleted");
      fetchOrders();
    } catch (error) {
      toast.error("Failed to delete order");
    } finally {
      setDeletingId(null);
    }
  };

  if (isLoading) {
    return (
      <Card className="flex items-center justify-center h-64 bg-onyx border-shadow-grey">
        <Loader2 className="w-8 h-8 animate-spin text-emerald" />
      </Card>
    );
  }

  if (orders.length === 0) {
    return (
      <Card className="flex flex-col items-center justify-center h-64 text-granite bg-onyx border-shadow-grey">
        <p>No active strategies.</p>
        <p className="text-sm mt-2">Create one using the form.</p>
      </Card>
    );
  }

  return (
    <Card className="p-0 overflow-hidden bg-onyx border-shadow-grey">
      {/* Desktop Table */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-shadow-grey bg-jet-black text-xs uppercase tracking-wider text-granite">
              <th className="p-4 font-medium">Trade Pair</th>
              <th className="p-4 font-medium">Action</th>
              <th className="p-4 font-medium">Trigger</th>
              <th className="p-4 font-medium">Fees</th>
              <th className="p-4 font-medium">Est. Time</th>
              <th className="p-4 font-medium text-right">Manage</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-shadow-grey/50">
            {orders.map((order) => (
              <tr key={order.id} className="hover:bg-jet-black/50 transition-colors">
                <td className="p-4">
                  <div className="flex items-center gap-2 font-mono text-sm text-snow">
                    <span>{order.sell_token}</span>
                    <ArrowRight size={14} className="text-granite" />
                    <span>{order.buy_token}</span>
                  </div>
                  {order.route_token && (
                    <span className="text-[10px] text-amber-500 mt-1 block">
                      * Routed via CC
                    </span>
                  )}
                </td>
                <td className="p-4">
                  <span className={`inline-block px-2.5 py-1 rounded-md text-xs font-bold ${order.action === "BUY" ? "bg-emerald/10 text-emerald" : "bg-red-500/10 text-red-400"}`}>
                    {order.action}
                  </span>
                </td>
                <td className="p-4 text-granite font-mono text-sm">
                  {order.direction === "ABOVE" ? ">" : "<"} ${order.trigger_price_usd}
                </td>
                <td className="p-4 text-granite font-mono text-xs">
                  {order.isFetchingQuote ? (
                    <span className="animate-pulse">...</span>
                  ) : order.quote ? (
                    <>
                      <div>{order.quote.network_fee_cc.toFixed(4)} CC</div>
                      <div className="text-[10px] text-granite/70">{order.quote.pool_fee_percent.toFixed(4)}%</div>
                    </>
                  ) : (
                    "---"
                  )}
                </td>
                <td className="p-4 text-granite font-mono text-xs">
                  {order.isFetchingQuote ? "..." : order.quote ? `~${order.quote.execution_time_seconds.toFixed(1)}s` : "---"}
                </td>
                <td className="p-4 text-right">
                  <Button 
                    variant="danger" 
                    onClick={() => handleDelete(order.id)}
                    disabled={deletingId === order.id}
                    className="px-3 py-1.5"
                  >
                    {deletingId === order.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Cards */}
      <div className="md:hidden flex flex-col gap-3 p-3 bg-onyx">
        {orders.map((order) => (
          <div 
            key={order.id} 
            className="bg-carbon rounded-xl border border-jet-black p-4 flex flex-col gap-3"
          >
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2 font-mono text-sm text-snow">
                <span className="font-semibold">{order.sell_token}</span>
                <ArrowRight size={14} className="text-granite" />
                <span className="font-semibold">{order.buy_token}</span>
              </div>
              <span className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase ${order.action === "BUY" ? "bg-emerald/10 text-emerald" : "bg-red-500/10 text-red-400"}`}>
                {order.action}
              </span>
            </div>

            {order.route_token && (
              <span className="text-[10px] text-amber-500/80 -mt-1">
                * Routed via CC
              </span>
            )}

            <div className="flex justify-between items-center bg-jet-black/50 rounded-lg p-3">
              <div className="flex flex-col">
                <span className="text-[10px] text-granite uppercase tracking-wider">Trigger</span>
                <span className="text-sm font-mono text-snow">
                  {order.direction === "ABOVE" ? ">" : "<"} ${order.trigger_price_usd}
                </span>
              </div>
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-granite uppercase tracking-wider">Amount</span>
                <span className="text-sm font-mono text-snow">
                  {order.amount} {order.sell_token}
                </span>
              </div>
            </div>

            {/* NEW: Quote Details on Mobile */}
            <div className="bg-jet-black/30 rounded-lg p-3 flex flex-col gap-1.5">
              <span className="text-[10px] text-granite uppercase tracking-wider flex items-center gap-1">
                <Info size={10} /> Live Quote
              </span>
              {order.isFetchingQuote ? (
                <span className="text-xs text-granite animate-pulse">Fetching fees...</span>
              ) : order.quote ? (
                <>
                  <div className="flex justify-between text-xs font-mono">
                    <span className="text-granite">Network Fee</span>
                    <span className="text-snow">{order.quote.network_fee_cc.toFixed(4)} CC</span>
                  </div>
                  <div className="flex justify-between text-xs font-mono">
                    <span className="text-granite">Pool Fee</span>
                    <span className="text-snow">{order.quote.pool_fee_percent.toFixed(4)}%</span>
                  </div>
                  <div className="flex justify-between text-xs font-mono">
                    <span className="text-granite">Est. Time</span>
                    <span className="text-snow">~{order.quote.execution_time_seconds.toFixed(1)}s</span>
                  </div>
                </>
              ) : (
                <span className="text-xs text-granite">Quote unavailable</span>
              )}
            </div>

            <Button 
              variant="danger" 
              onClick={() => handleDelete(order.id)}
              disabled={deletingId === order.id}
              className="w-full py-2 text-xs"
            >
              {deletingId === order.id ? (
                <span className="flex items-center gap-2 justify-center">
                  <Loader2 size={14} className="animate-spin" /> Deleting...
                </span>
              ) : (
                <span className="flex items-center gap-2 justify-center">
                  <Trash2 size={14} /> Delete Strategy
                </span>
              )}
            </Button>
          </div>
        ))}
      </div>
    </Card>
  );
}