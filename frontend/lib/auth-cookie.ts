import "server-only";
import { cookies } from "next/headers";

export const AUTH_COOKIE = "cantex_session";
export const AUTH_MAX_AGE = 60 * 60 * 24;

export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(AUTH_COOKIE)?.value ?? null;
}