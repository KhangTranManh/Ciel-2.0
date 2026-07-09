import type { SkillsResponse } from "./types";

// REST base derived from the WS URL so there is a single place to point at the
// backend. ws://host:port/ws  ->  http://host:port
const WS_URL = import.meta.env.VITE_CIEL_WS_URL ?? "ws://localhost:8000/ws";

export const HTTP_BASE = WS_URL.replace(/^ws/, "http").replace(/\/ws$/, "");

export async function fetchSkills(): Promise<SkillsResponse> {
  const res = await fetch(`${HTTP_BASE}/skills`);
  if (!res.ok) throw new Error(`/skills ${res.status}`);
  return (await res.json()) as SkillsResponse;
}
