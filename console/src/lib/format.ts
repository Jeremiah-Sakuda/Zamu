/** Small presentation helpers. Anything with judgement in it belongs on the server. */

export function relativeTime(iso: string, now = Date.now()): string {
  const delta = new Date(iso).getTime() - now;
  const minutes = Math.round(Math.abs(delta) / 60000);
  const past = delta < 0;

  const say = (value: number, unit: string) =>
    `${past ? "" : "in "}${value} ${unit}${value === 1 ? "" : "s"}${past ? " ago" : ""}`;

  if (minutes < 1) return past ? "just now" : "any moment";
  if (minutes < 60) return say(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 36) return say(hours, "hour");
  return say(Math.round(hours / 24), "day");
}

export function clockTime(iso: string, timeZone: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
