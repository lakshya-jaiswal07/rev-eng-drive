import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Badge } from "~/components/ui/primitives";
import { useDrive } from "./store";
import type { DriveState, PlaceInfo, RouteInfo } from "./types";

/**
 * The trip on a real map (OpenStreetMap tiles, Leaflet; no key, no account).
 *
 * It draws what the agent is doing, not only where the car is going, so the
 * interruption handling is visible: a guess planned from partial speech is a
 * grey dashed line, a guess the driver took back turns red and fades, a route
 * whose inputs changed goes amber while it is replanned, and only the route the
 * agent actually acted on is solid.
 *
 * Road shapes come from the public OSRM demo server. It is a best-effort
 * nicety: if it is slow or unreachable the map falls back to straight lines,
 * and nothing about the agent depends on it.
 */

const OSRM = "https://router.project-osrm.org/route/v1/driving";
const DROPPED_FOR_MS = 25_000; // a taken-back guess stays visible this long
const STOP_EMOJI: Record<string, string> = {
  fuel: "⛽", charging: "🔌", food: "🍽️", snacks: "🍪", coffee: "☕", drinks: "🥤", rest: "🚻",
  pharmacy: "💊", atm: "🏧", shopping: "🛍️", custom: "📍", place: "📍",
};

/** A pin's text: "🍪 a snack break", "📍 Lonavla", "🍽️ a meal · Lonavla". */
function stopText(s: RouteInfo["stops"][number]): string {
  const icon = STOP_EMOJI[s.kind] ?? "📍";
  if (s.kind === "place") return `${icon} ${s.near}`;
  return s.at_named_place ? `${icon} ${s.label} · ${s.near}` : `${icon} ${s.label}`;
}

type LatLng = [number, number];
// Stop labels can be the driver's own words ("stop for <anything>"), so never inject them as HTML.
const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const at = (p: { lat: number; lon: number }): LatLng => [p.lat, p.lon];

const roadCache = new Map<string, LatLng[]>();
const inFlight = new Set<string>();

/** Road geometry through the points, or null until it arrives (callers draw straight lines meanwhile). */
function useRoad(points: LatLng[] | null, onArrive: () => void): LatLng[] | null {
  const key = points ? points.map(([a, b]) => `${a.toFixed(4)},${b.toFixed(4)}`).join(";") : "";
  useEffect(() => {
    if (!points || points.length < 2 || roadCache.has(key) || inFlight.has(key)) return;
    inFlight.add(key);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 5000);
    const coords = points.map(([lat, lon]) => `${lon},${lat}`).join(";");
    fetch(`${OSRM}/${coords}?overview=simplified&geometries=geojson`, { signal: abort.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j) => {
        const line = j?.routes?.[0]?.geometry?.coordinates as [number, number][] | undefined;
        roadCache.set(key, line?.length ? line.map(([lon, lat]) => [lat, lon] as LatLng) : points);
      })
      .catch(() => roadCache.set(key, points)) // offline or rate-limited: straight lines
      .finally(() => {
        clearTimeout(timer);
        inFlight.delete(key);
        onArrive();
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return key ? (roadCache.get(key) ?? null) : null;
}

interface Dropped {
  place: PlaceInfo;
  at: number;
}

/** What to say in the badge over the map. */
function headline(drive: DriveState | null, guess: PlaceInfo | null, dropped: Dropped[], replanning: boolean): { text: string; tone: string } {
  if (!drive) return { text: "Connecting...", tone: "neutral" };
  const status = drive.route_status;
  const recentDrop = dropped.length ? dropped[dropped.length - 1] : null;
  if (guess) return { text: `Planning ${guess.name} early - you're still speaking`, tone: "info" };
  if (replanning || status === "stale") return { text: "Trip changed - the old route is stale, replanning", tone: "warn" };
  if (status === "running" || status === "pending") {
    const prefix = recentDrop && Date.now() - recentDrop.at < 8000 ? `${recentDrop.place.name} dropped · ` : "";
    return { text: `${prefix}Planning the route to ${drive.destination?.name ?? "..."}`, tone: "info" };
  }
  if (drive.navigation) return { text: `Guidance active to ${drive.navigation.destination} · committed once`, tone: "live" };
  if (drive.route) return { text: `Route ready to ${drive.route.destination.name}`, tone: "live" };
  return { text: `You're in ${drive.origin.name}. Say where you're going.`, tone: "neutral" };
}

export function RouteMap() {
  const drive = useDrive((s) => s.drive);
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const lastRoute = useRef<RouteInfo | null>(null);
  const prevTarget = useRef<PlaceInfo | null>(null);
  const fittedFor = useRef("");
  const [dropped, setDropped] = useState<Dropped[]>([]);
  const [, setTick] = useState(0);
  const rerender = () => setTick((n) => n + 1);

  // -- the map itself, created once ----------------------------------------
  useEffect(() => {
    if (!host.current || mapRef.current) return;
    const map = L.map(host.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    map.setView([22.5, 79], 5); // India until the first state arrives
    layer.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(host.current);
    return () => {
      resize.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Taken-back guesses fade out after a while.
  useEffect(() => {
    const timer = setInterval(() => setDropped((d) => d.filter((x) => Date.now() - x.at < DROPPED_FOR_MS)), 2000);
    return () => clearInterval(timer);
  }, []);

  // A guess for the place already being planned is drawn as that plan, not twice.
  const shownGuess = drive?.guess && drive.guess.id !== drive.destination?.id ? drive.guess : null;

  // -- notice when the place the agent was working towards was taken back -----
  useEffect(() => {
    if (!drive) return;
    if (drive.route_status === "valid" && drive.route) lastRoute.current = drive.route;
    const target = drive.guess ?? drive.destination ?? null;
    const prev = prevTarget.current;
    if (prev && prev.id !== target?.id && prev.id !== drive.destination?.id) {
      setDropped((d) => [...d.filter((x) => x.place.id !== prev.id), { place: prev, at: Date.now() }].slice(-3));
    }
    prevTarget.current = target;
  }, [drive]);

  // -- geometry ---------------------------------------------------------------
  const origin = drive ? at(drive.origin) : null;
  const route = drive?.route_status === "valid" ? drive.route : null;
  const replanning = !route && lastRoute.current && (drive?.route_status === "stale" || drive?.route_status === "running")
    && lastRoute.current.destination.id === drive?.destination?.id ? lastRoute.current : null;
  // Through every stop in driving order (the server lists them in the order they were asked for).
  const pathOf = (r: RouteInfo): LatLng[] => [
    at(r.origin),
    ...[...r.stops]
      .filter((s) => s.lat !== undefined && s.lon !== undefined)
      .sort((a, b) => a.km_from_start - b.km_from_start)
      .map((s) => [s.lat!, s.lon!] as LatLng),
    at(r.destination),
  ];
  const planningTo = !route && !replanning && drive?.destination ? drive.destination : null;

  const routeRoad = useRoad(route ? pathOf(route) : null, rerender);
  const staleRoad = useRoad(replanning ? pathOf(replanning) : null, rerender);
  const guessRoad = useRoad(origin && shownGuess ? [origin, at(shownGuess)] : null, rerender);
  const planRoad = useRoad(origin && planningTo ? [origin, at(planningTo)] : null, rerender);
  const drop0 = useRoad(origin && dropped[0] ? [origin, at(dropped[0].place)] : null, rerender);
  const drop1 = useRoad(origin && dropped[1] ? [origin, at(dropped[1].place)] : null, rerender);
  const drop2 = useRoad(origin && dropped[2] ? [origin, at(dropped[2].place)] : null, rerender);
  const dropRoads = [drop0, drop1, drop2];

  // -- draw -------------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    const group = layer.current;
    if (!map || !group || !drive || !origin) return;
    group.clearLayers();
    const pts: LatLng[] = [origin];
    const line = (path: LatLng[], className: string, weight: number) => L.polyline(path, { className, weight, interactive: false }).addTo(group);
    const dot = (p: LatLng, className: string, label: string, dir: L.Direction = "top") =>
      L.circleMarker(p, { radius: 7, className, weight: 3 })
        .bindTooltip(label, { permanent: true, direction: dir, offset: dir === "top" ? [0, -8] : [8, 0], className: "dm-tip" })
        .addTo(group);

    dropped.forEach((d, i) => {
      if (d.place.id === drive.destination?.id) return;
      const p = at(d.place);
      line(dropRoads[i] ?? [origin, p], "dm-line dm-dropped", 4);
      dot(p, "dm-dot dm-dot-dropped", `${d.place.name} - dropped`, "right");
      pts.push(p);
    });
    if (replanning) {
      line(staleRoad ?? pathOf(replanning), "dm-line dm-stale", 5);
      dot(at(replanning.destination), "dm-dot dm-dot-dest", replanning.destination.name, "right");
      pts.push(...pathOf(replanning));
    }
    if (planningTo) {
      line(planRoad ?? [origin, at(planningTo)], "dm-line dm-guess", 5);
      dot(at(planningTo), "dm-dot dm-dot-guess", `${planningTo.name}...`, "right");
      pts.push(at(planningTo));
    }
    if (route) {
      line(routeRoad ?? pathOf(route), "dm-line dm-route-casing", 9);
      line(routeRoad ?? pathOf(route), "dm-line dm-route", 5);
      route.stops.forEach((s) => {
        if (s.lat === undefined || s.lon === undefined) return;
        L.marker([s.lat, s.lon], {
          icon: L.divIcon({ className: "", html: `<div class="dm-stop">${escapeHtml(stopText(s))}</div>`, iconSize: [0, 0] }),
          interactive: false,
        }).addTo(group);
      });
      dot(at(route.destination), "dm-dot dm-dot-dest", route.destination.name, "right");
      pts.push(...pathOf(route));
    }
    if (shownGuess) {
      line(guessRoad ?? [origin, at(shownGuess)], "dm-line dm-guess", 5);
      dot(at(shownGuess), "dm-dot dm-dot-guess", `${shownGuess.name}?`, "right");
      pts.push(at(shownGuess));
    }
    dot(origin, "dm-dot dm-dot-origin", `You · ${drive.origin.name}`);

    // Re-frame only when the set of places changes, not on every frame. The car's
    // position alone is framed once at the start, never later: between a guess
    // being dropped and the next one arriving there can be a frame with nothing
    // else on the map, and zooming in on it would make the map jump.
    const frameKey = pts.map(([a, b]) => `${a.toFixed(2)},${b.toFixed(2)}`).sort().join("|");
    if (frameKey !== fittedFor.current) {
      const first = fittedFor.current === "";
      if (pts.length > 1) {
        map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 10, animate: !first });
        fittedFor.current = frameKey;
      } else if (first) {
        map.setView(origin, 7, { animate: false });
        fittedFor.current = frameKey;
      }
    }
  });

  const { text, tone } = useMemo(
    () => headline(drive, shownGuess, dropped, replanning !== null),
    [drive, shownGuess, dropped, replanning],
  );

  return (
    <div className="drive-map relative h-full min-h-[240px] w-full overflow-hidden rounded-[inherit]">
      <div ref={host} className="absolute inset-0" aria-label="Route map" role="region" />
      <div className="pointer-events-none absolute left-1/2 top-3 z-[500] -translate-x-1/2">
        <Badge tone={tone as never} className="pointer-events-auto shadow-[var(--shadow-lift)]" data-testid="map-status">
          {text}
        </Badge>
      </div>
      <div className="dm-legend pointer-events-none absolute bottom-6 left-3 z-[500]">
        <span><i className="dm-key dm-key-guess" /> planned early</span>
        <span><i className="dm-key dm-key-dropped" /> taken back</span>
        <span><i className="dm-key dm-key-stale" /> replanning</span>
        <span><i className="dm-key dm-key-route" /> active route</span>
      </div>
    </div>
  );
}
