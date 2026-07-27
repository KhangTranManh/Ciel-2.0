// Wire protocol shared with the Python backend (main_api.py).
// Kept deliberately close to the server's send_json shapes. Additive-only:
// new fields (e.g. a future `lang` on responses, or `response_chunk` streaming
// for voice) can be added without breaking existing consumers.

export interface SkillTool {
  name: string;
  description: string;
}

export interface SkillManifestEntry {
  module: string;
  category: string; // "internal" | "external"
  tool_count: number;
  tools: SkillTool[];
  has_prompt: boolean;
}

export interface SkillsResponse {
  ready: boolean;
  skills: SkillManifestEntry[];
  totals: { modules: number; tools: number };
  error?: string;
}

export interface SkillActivity {
  module: string;
  category: string;
  tool_count: number;
  active: boolean;
}

export interface TokenCount {
  input: number;
  output: number;
  total: number;
}

// Matches main_api.broadcast_vitals — optional fields stay optional so older
// backends that only send call counts still type-check and render.
export interface Vitals {
  vram_used: number;
  vram_total: number;
  llm_calls: Record<string, number>;
  llm_calls_total: number;
  llm_tokens?: Record<string, TokenCount>;
  llm_tokens_total?: number;
  llm_cost_usd?: Record<string, number>;
  llm_cost_usd_total?: number;
  tiers: Record<string, boolean>;
  skills: SkillActivity[];
}

export interface ConfirmRequest {
  tool_name: string;
  preview: string;
  tool_args: Record<string, unknown>;
}

// Backend safety-gate wait (main_api.py). UI countdown mirrors this.
export const CONFIRM_TIMEOUT_SEC = 60;

// ---- Server -> Client frames ----
export type ServerMessage =
  | { type: "thought"; data: string }
  | { type: "vitals"; data: Vitals }
  | { type: "status"; data: string }
  | { type: "response"; data: string }
  | { type: "error"; data: string }
  | { type: "confirm_request"; data: ConfirmRequest };

// ---- Client -> Server frames ----
// `cancel` is additive (Tier-5): UI sends it; backend wires request_cancel when ready.
export type ClientMessage =
  | { message: string }
  | { type: "confirm_response"; approved: boolean }
  | { type: "cancel" };

// ---- Internal app event bus channel names ----
// The UI, and any future modality (voice in/out), talk ONLY to these events —
// never directly to the WebSocket. That is what keeps modalities pluggable.
export interface BusEvents {
  thought: string;
  vitals: Vitals;
  status: string;
  response: string;
  error: string;
  confirm: ConfirmRequest;
  connection: "connecting" | "open" | "closed";
  /** Soft UI notices (TTS fail, send dropped) — not agent errors. */
  notice: string;
}
