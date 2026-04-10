import { ArrowRight } from "lucide-react";

export function FollowupChips({ chips, onSelect }) {
  if (!chips || chips.length === 0) return null;
  return (
    <div className="mt-2 flex flex-col gap-1">
      {chips.map((q, i) => (
        <button
          key={i}
          onClick={() => onSelect?.(q)}
          className="apex-followup text-left"
        >
          <ArrowRight size={10} style={{ flexShrink: 0, opacity: 0.6 }} />
          <span>{q}</span>
        </button>
      ))}
    </div>
  );
}
