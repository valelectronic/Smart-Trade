import { NextRequest, NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth-cookie";

const API_URL = process.env.CANTEX_API_URL;

async function proxy(request: NextRequest, path: string[]) {
  const token = await getSessionToken();

  if (!token) {
    return NextResponse.json(
      { detail: "Please sign in again." },
      { status: 401 },
    );
  }

  if (!API_URL) {
    return NextResponse.json(
      { detail: "CANTEX_API_URL is not configured." },
      { status: 500 },
    );
  }

  const target = `${API_URL}/${path
    .map(encodeURIComponent)
    .join("/")}${request.nextUrl.search}`;

  const headers = new Headers({
    Authorization: `Bearer ${token}`,
  });

  const contentType = request.headers.get("content-type");

  if (contentType) {
    headers.set("Content-Type", contentType);
  }

  try {
    const hasBody = !["GET", "HEAD"].includes(request.method);

    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
    });

    const payload = await upstream.arrayBuffer();
    const responseHeaders = new Headers();

    const upstreamType = upstream.headers.get("content-type");

    if (upstreamType) {
      responseHeaders.set("Content-Type", upstreamType);
    }

    return new NextResponse(payload, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      { detail: "The trading service is temporarily unavailable." },
      { status: 502 },
    );
  }
}

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

export async function GET(
  request: NextRequest,
  context: RouteContext,
) {
  return proxy(request, (await context.params).path);
}

export async function POST(
  request: NextRequest,
  context: RouteContext,
) {
  return proxy(request, (await context.params).path);
}

export async function PUT(
  request: NextRequest,
  context: RouteContext,
) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(
  request: NextRequest,
  context: RouteContext,
) {
  return proxy(request, (await context.params).path);
}