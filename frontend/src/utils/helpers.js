/**
 * Shared utilities: markdown rendering, HTML escaping, WS URL building
 */

export function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderMarkdown(text) {
  if (!text) return "";
  try {
    return window.marked.parse(text, { breaks: true });
  } catch (_) {
    return escapeHtml(text).replace(/\n/g, "<br>");
  }
}

export function buildWsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}
