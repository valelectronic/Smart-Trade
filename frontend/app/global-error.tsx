"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // Check if this is a chunk load error (stale deploy)
  const isChunkError = 
    error.name === "ChunkLoadError" || 
    error.message?.includes("Loading chunk") ||
    error.message?.includes("Failed to fetch dynamically imported module");

  if (isChunkError && typeof window !== "undefined") {
    const hasReloaded = sessionStorage.getItem("chunk-reload");
    if (!hasReloaded) {
      sessionStorage.setItem("chunk-reload", "true");
      window.location.reload();
      return null;
    }
  }

  return (
    <html>
      <body>
        <div style={{ padding: "2rem", textAlign: "center", fontFamily: "sans-serif" }}>
          <h2>Something went wrong</h2>
          <p>{error.message}</p>
          <button onClick={() => reset()} style={{ marginTop: "1rem", padding: "0.5rem 1rem" }}>
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}