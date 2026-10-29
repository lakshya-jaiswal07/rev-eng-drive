import type { DriveClientFrame, DriveFrame } from "./types";

/** The /ws/drive socket, with the same reconnect backoff as the main agent's. */
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
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${location.host}/ws/drive`);
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
    socket.onclose = () => {
      this.onStatus("closed");
      if (this.closedByUs) return;
      const delay = Math.min(800 * 2 ** this.retries++, 8000);
      setTimeout(() => this.connect(), delay);
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
