import { useAuth } from "~/lib/auth";
import type { DriveClientFrame, DriveFrame } from "./types";

/** The /ws/drive socket, reconnecting with exponential backoff. */
export class DriveSocket {
  private socket: WebSocket | null = null;
  private retries = 0;
  private closedByUs = false;

  constructor(
    private onFrame: (frame: DriveFrame) => void,
    private onStatus: (status: "connecting" | "open" | "closed") => void,
  ) {}

  connect() {
    this.closedByUs = false;
    this.onStatus("connecting");
    void this.socketUrl().then((url) => {
      if (url && !this.closedByUs) this.open(url);
    });
  }

  /** Same host: the cookie goes along. Another host: trade it for a one-time ticket first. */
  private async socketUrl(): Promise<string | null> {
    if (!__BACKEND_URL__) {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      return `${proto}://${location.host}/ws/drive`;
    }
    const base = __BACKEND_URL__.replace(/^http/, "ws");
    try {
      const response = await fetch("/api/auth/ws-ticket", { method: "POST", credentials: "same-origin" });
      if (response.status === 401) {
        this.closedByUs = true;
        this.onStatus("closed");
        useAuth.getState().expired();
        return null;
      }
      if (!response.ok) throw new Error(String(response.status));
      const { ticket } = (await response.json()) as { ticket: string };
      return `${base}/ws/drive?ticket=${encodeURIComponent(ticket)}`;
    } catch {
      // backend asleep or unreachable: back off as if the socket had dropped
      this.onStatus("closed");
      this.retryLater();
      return null;
    }
  }

  private retryLater() {
    if (this.closedByUs) return;
    const delay = Math.min(800 * 2 ** this.retries++, 8000);
    setTimeout(() => this.connect(), delay);
  }

  private open(url: string) {
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      this.retries = 0;
      this.onStatus("open");
    };
    socket.onmessage = (event) => {
      try {
        this.onFrame(JSON.parse(event.data) as DriveFrame);
      } catch {
        /* one malformed frame is not worth dropping the drive */
      }
    };
    socket.onclose = (event) => {
      this.onStatus("closed");
      if (event.code === 4401) {
        // no valid session: stop retrying and send the user back to log in
        this.closedByUs = true;
        useAuth.getState().expired();
      }
      this.retryLater();
    };
    socket.onerror = () => socket.close();
  }

  send(frame: DriveClientFrame) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(frame));
  }

  close() {
    this.closedByUs = true;
    this.socket?.close();
  }
}
