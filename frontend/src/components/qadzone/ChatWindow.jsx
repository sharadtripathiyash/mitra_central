/**
 * ChatWindow — renders message history + live streaming bubble.
 */
import { useEffect, useRef } from "react";
import { renderMarkdown } from "../../utils/helpers";
import { StatusIndicator } from "../shared/StatusIndicator";

export function ChatWindow({ messages, streaming, statusText, currentHtml, loading }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, currentHtml, loading]);

  return (
    <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
      {messages.map((m, i) => (
        <div key={i}>
          {m.role === "user" ? (
            <div className="flex justify-end">
              <div className="bg-brand-600 text-white rounded-2xl rounded-br-sm px-5 py-3 max-w-[85%] whitespace-pre-wrap text-sm">
                {m.text}
              </div>
            </div>
          ) : (
            <div
              className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5 prose-container"
              dangerouslySetInnerHTML={{ __html: m.html }}
            />
          )}
        </div>
      ))}

      {/* Live streaming bubble */}
      {(streaming || (loading && statusText)) && (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
          <StatusIndicator text={statusText} />
          {currentHtml && (
            <div
              className="prose-container"
              dangerouslySetInnerHTML={{ __html: currentHtml }}
            />
          )}
          {streaming && (
            <span className="inline-block w-1.5 h-4 bg-brand-500 animate-pulse ml-0.5 align-middle" />
          )}
        </div>
      )}

      {/* Thinking indicator */}
      {loading && !streaming && !statusText && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
          <span>Thinking…</span>
        </div>
      )}

      {/* Bottom padding so messages don't hide behind floating input */}
      <div className="h-24" ref={bottomRef} />
    </div>
  );
}
