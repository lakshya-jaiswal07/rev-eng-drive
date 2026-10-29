import { useEffect, useState } from "react";
import { AnimatePresence } from "motion/react";
import { Logo } from "~/components/Logo";
import { DriveView } from "~/drive/DriveView";
import { AuthDialog } from "~/landing/AuthDialog";
import { Landing } from "~/landing/Landing";
import { useAuth } from "~/lib/auth";

type Route = "landing" | "login" | "signup" | "drive";

const routeFromHash = (): Route => {
  const hash = window.location.hash.replace(/^#\/?/, "");
  return hash === "login" || hash === "signup" || hash === "drive" ? hash : "landing";
};
const go = (hash: string) => {
  window.location.hash = hash;
};

/**
 * Routes: the public landing page at "/", with log-in and sign-up dialogs
 * (#login, #signup); the assistant (#drive) needs a session, and without one
 * the server refuses its socket too.
 */
export default function App() {
  const { status, refresh } = useAuth();
  const [route, setRoute] = useState<Route>(routeFromHash);

  useEffect(() => {
    void refresh();
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [refresh]);

  useEffect(() => {
    if (status === "guest" && route === "drive") go("login"); // come back here after logging in
    else if (status === "user" && (route === "login" || route === "signup")) go("drive");
  }, [status, route]);

  if (status === "loading") {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="animate-pulse">
          <Logo size={44} />
        </div>
      </div>
    );
  }
  if (status === "user" && route === "drive") return <DriveView />;

  return (
    <>
      <Landing go={go} />
      <AnimatePresence>
        {(route === "login" || route === "signup") && status === "guest" ? (
          <AuthDialog mode={route} onMode={(mode) => go(mode)} onClose={() => go("")} onDone={() => go("drive")} />
        ) : null}
      </AnimatePresence>
    </>
  );
}
