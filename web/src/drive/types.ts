/** Frames of the /ws/drive protocol (the in-car use-case extension). */

export interface PlaceInfo {
  id: string;
  name: string;
  state: string;
  label: string;
  lat: number;
  lon: number;
}

export interface RouteStop {
  kind: string;
  label: string;
  near: string;
  km_from_start: number;
  lat?: number;
  lon?: number;
  /** In a town the driver named ("lunch at Lonavala", "via Nashik"), not just near the road. */
  at_named_place?: boolean;
  spec?: string;
}

export interface RouteInfo {
  origin: PlaceInfo;
  destination: PlaceInfo;
  distance_km: number;
  eta_min: number;
  via: string;
  avoid_highways: boolean;
  stops: RouteStop[];
}

export interface DriveState {
  origin: PlaceInfo;
  destination: PlaceInfo | null;
  stops: string[];
  avoid_highways: boolean;
  route: RouteInfo | null;
  route_status: string;
  route_attempt: number;
  navigation: { action_id: string; destination: string; attempt: number } | null;
  saved: Record<string, string>;
  straight_km: number | null;
  /** Destination guessed from partial speech; planned early, never acted on. */
  guess?: PlaceInfo | null;
}

export type DriveFrame =
  | { t: "ready"; mode: "drive"; provider: string; origin: string }
  | { t: "drive"; ts: number; state: DriveState }
  | { t: "drive_event"; ts: number; kind: string; text: string }
  | { t: "stage"; ts: number; stage: string; detail: string; turn_id?: string | null }
  | { t: "token"; ts: number; turn_id: string; text: string }
  | { t: "message"; ts: number; turn_id: string; role: "user" | "agent" | "system"; content: string; modality: string; status: "complete" | "interrupted" | "resumed" }
  | { t: "filler"; ts: number; turn_id: string; text: string }
  | { t: "spec"; ts: number; status: "started" | "hit" | "miss" | "discarded"; query: string; saved_ms: number }
  | { t: "tool"; ts: number; call_id: string; name: string; status: string; args: Record<string, unknown>; latency_ms: number }
  | { t: "metric"; ts: number; name: string; value: number; unit: string; note: string }
  | { t: "checkpoint"; ts: number; checkpoint_id: string; turn_id: string; summary: string; kept_tokens: number }
  | { t: "error"; ts: number; message: string };

export type DriveClientFrame =
  | { t: "partial"; text: string }
  | { t: "final"; text: string; modality: "voice" | "text" }
  | { t: "interrupt"; heard_chars?: number }
  | { t: "resume" }
  | { t: "ping" };
