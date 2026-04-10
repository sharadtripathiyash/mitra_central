export function StatusIndicator({ text }) {
  if (!text) return null;
  return (
    <div className="flex items-center gap-2 text-sm text-brand-600 mb-2">
      <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
      <span>{text}</span>
    </div>
  );
}
