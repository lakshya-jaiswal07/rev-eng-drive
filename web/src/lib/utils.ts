import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function ms(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "--";
  if (value < 1) return "<1 ms";
  return `${value.toFixed(digits)} ms`;
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}
