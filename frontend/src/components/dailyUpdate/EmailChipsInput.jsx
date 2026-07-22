import { useId, useMemo, useState } from "react";
import { X } from "lucide-react";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;

/** Split any pasted / API string into individual emails */
export function parseEmailList(value) {
  if (Array.isArray(value)) {
    return value.flatMap((v) => parseEmailList(v));
  }
  if (value == null || value === "") return [];
  return String(value)
    .split(/[,;\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => EMAIL_RE.test(s));
}

/**
 * Chip-style email list — each address is its own pill with remove (X).
 * Add via Enter, comma, Tab, Space (after a valid email), paste, or blur.
 */
const EmailChipsInput = ({
  label = "Emails",
  emails = [],
  onChange,
  disabled = false,
  placeholder = "Type email and press Enter",
}) => {
  const inputId = useId();
  const [draft, setDraft] = useState("");

  // Flatten accidental "a@x.com b@y.com" single entries into real chips
  const chips = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const item of emails ?? []) {
      for (const email of parseEmailList(item)) {
        if (seen.has(email)) continue;
        seen.add(email);
        out.push(email);
      }
    }
    return out;
  }, [emails]);

  const pushEmails = (parts) => {
    if (disabled || !parts.length) return;
    const next = [...chips];
    let changed = false;
    for (const email of parts) {
      if (!EMAIL_RE.test(email)) continue;
      if (next.includes(email)) continue;
      next.push(email);
      changed = true;
    }
    if (changed) onChange?.(next);
  };

  const commitDraft = () => {
    const parts = parseEmailList(draft);
    if (parts.length) {
      pushEmails(parts);
      setDraft("");
    }
  };

  const removeAt = (idx) => {
    if (disabled) return;
    onChange?.(chips.filter((_, i) => i !== idx));
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" || e.key === "Tab" || e.key === ",") {
      if (draft.trim()) {
        e.preventDefault();
        commitDraft();
      }
      return;
    }
    // Space after a complete email → turn it into a chip
    if (e.key === " " || e.key === "Spacebar") {
      const trimmed = draft.trim();
      if (EMAIL_RE.test(trimmed)) {
        e.preventDefault();
        pushEmails([trimmed.toLowerCase()]);
        setDraft("");
      }
      return;
    }
    if (e.key === "Backspace" && !draft && chips.length) {
      e.preventDefault();
      removeAt(chips.length - 1);
    }
  };

  const onPaste = (e) => {
    const text = e.clipboardData?.getData("text");
    if (!text) return;
    const parts = parseEmailList(text);
    if (!parts.length) return;
    e.preventDefault();
    pushEmails(parts);
    setDraft("");
  };

  return (
    <div className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <div
        className={`mt-1 flex min-h-[36px] flex-wrap items-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1.5 ${
          disabled
            ? "bg-slate-50"
            : "bg-white focus-within:border-[#14344a]/40 focus-within:ring-2 focus-within:ring-[#14344a]/10"
        }`}
        onMouseDown={(e) => {
          // Keep focus on the text input without clearing chips
          if (disabled) return;
          if (e.target.closest("button")) return;
          const el = document.getElementById(inputId);
          if (el && e.target !== el) {
            e.preventDefault();
            el.focus();
          }
        }}
      >
        {chips.map((email, idx) => (
          <span
            key={`${email}-${idx}`}
            className="inline-flex max-w-full items-center gap-1 rounded-full border border-slate-300 bg-slate-100 py-0.5 pl-2.5 pr-0.5 text-[11px] font-medium text-slate-800"
          >
            <span className="truncate">{email}</span>
            {!disabled && (
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-200/80 text-slate-600 hover:bg-slate-300 hover:text-slate-900"
                aria-label={`Remove ${email}`}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            )}
          </span>
        ))}
        {!disabled && (
          <input
            id={inputId}
            type="text"
            inputMode="email"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={commitDraft}
            onPaste={onPaste}
            placeholder={chips.length ? "Add another…" : placeholder}
            className="min-w-[120px] flex-1 border-0 bg-transparent py-0.5 text-xs text-slate-800 outline-none placeholder:text-slate-400"
          />
        )}
        {disabled && chips.length === 0 && (
          <span className="text-xs text-slate-400">—</span>
        )}
      </div>
      {!disabled && (
        <p className="mt-1 text-[10px] text-slate-400">
          Press Enter, Space, or comma after each email to create a chip.
        </p>
      )}
    </div>
  );
};

export default EmailChipsInput;
