import { create } from "zustand";

export interface AuthUser {
  id: number;
  name: string;
  email: string;
}

interface AuthState {
  status: "loading" | "guest" | "user";
  user: AuthUser | null;
  refresh: () => Promise<void>;
  signup: (name: string, email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** A socket was refused for lack of a session: back to the login screen. */
  expired: () => void;
}

export class AuthFailure extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

async function call(path: string, body?: unknown): Promise<{ user?: AuthUser }> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new AuthFailure("network", "Can't reach the server. Is the backend running?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail;
    throw new AuthFailure(detail?.code ?? String(response.status), detail?.message ?? "Something went wrong. Try again.");
  }
  return data;
}

export const useAuth = create<AuthState>((set) => ({
  status: "loading",
  user: null,

  refresh: async () => {
    try {
      const { user } = await call("/api/auth/me");
      set({ status: user ? "user" : "guest", user: user ?? null });
    } catch {
      set({ status: "guest", user: null });
    }
  },

  signup: async (name, email, password) => {
    const { user } = await call("/api/auth/signup", { name, email, password });
    set({ status: "user", user: user ?? null });
  },

  login: async (email, password) => {
    const { user } = await call("/api/auth/login", { email, password });
    set({ status: "user", user: user ?? null });
  },

  logout: async () => {
    await call("/api/auth/logout", {}).catch(() => undefined);
    set({ status: "guest", user: null });
    // A full reload drops every in-memory conversation, so the next person
    // on this browser starts clean.
    window.location.replace(window.location.pathname);
  },

  expired: () => set({ status: "guest", user: null }),
}));
