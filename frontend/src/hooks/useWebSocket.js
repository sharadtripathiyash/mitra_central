/**
 * useWebSocket — shared streaming WebSocket hook.
 *
 * Handles the full WS lifecycle:
 *   open → send payload → stream tokens → receive frames → done/error → close
 *
 * Frame protocol (JSON):
 *   {type: "token"|"status"|"sources"|"followup"|"doc"|"error"|"done", data: ...}
 */
import { useCallback, useRef, useState } from "react";

function buildWsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

export function useWebSocket(wsPath) {
  const wsRef = useRef(null);
  const [loading, setLoading]       = useState(false);
  const [streaming, setStreaming]   = useState(false);
  const [statusText, setStatusText] = useState("");
  const [currentText, setCurrentText] = useState("");

  const close = useCallback(() => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch (_) {}
      wsRef.current = null;
    }
  }, []);

  /**
   * send(payload, callbacks)
   *
   * payload  — JSON object sent on WS open
   * callbacks — {
   *   onToken(text)        — called with each streaming chunk (accumulated)
   *   onStatus(msg)        — status indicator messages
   *   onFrame(type, data)  — raw frame handler for doc / sources / followup / etc.
   *   onDone(finalText)    — stream complete, receives full accumulated text
   *   onError(msg)         — error frame or connection failure
   * }
   */
  const send = useCallback((payload, callbacks = {}) => {
    close();
    setLoading(true);
    setStreaming(false);
    setStatusText("");
    setCurrentText("");

    let accumulated = "";

    const ws = new WebSocket(buildWsUrl(wsPath));
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (evt) => {
      let frame;
      try { frame = JSON.parse(evt.data); } catch (_) { return; }
      const { type, data } = frame;

      switch (type) {
        case "token":
          if (!streaming) setStreaming(true);
          accumulated += data;
          setCurrentText(accumulated);
          callbacks.onToken?.(accumulated);
          break;
        case "status":
          setStatusText(data);
          callbacks.onStatus?.(data);
          break;
        case "error":
          callbacks.onError?.(data);
          _finish();
          break;
        case "done":
          callbacks.onDone?.(accumulated);
          _finish();
          break;
        default:
          callbacks.onFrame?.(type, data);
      }
    };

    ws.onerror = () => {
      callbacks.onError?.("Connection error. Please try again.");
      _finish();
    };

    ws.onclose = () => {
      if (wsRef.current) {
        callbacks.onDone?.(accumulated);
        _finish();
      }
    };

    function _finish() {
      setLoading(false);
      setStreaming(false);
      setStatusText("");
      wsRef.current = null;
    }
  }, [wsPath, close]);

  return { loading, streaming, statusText, currentText, send, close };
}
