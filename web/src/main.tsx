import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

const stored = localStorage.getItem("interject-theme");
const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
document.documentElement.classList.toggle("dark", stored ? stored === "dark" : !prefersLight);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
