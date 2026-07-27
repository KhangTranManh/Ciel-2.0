import type { ReactNode } from "react";

// Lightweight, dependency-free markdown for chat turns.
// Escapes HTML first — only our own tags are injected.
// Supports: fenced code, inline code, **bold**, *italic*, newlines.

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatInline(escaped: string): string {
  let s = escaped.replace(/`([^`]+)`/g, '<code class="md-code">$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // single *italic* — avoid matching ** leftovers
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  return s;
}

export function markdownToHtml(raw: string): string {
  const parts = raw.split(/(```[\s\S]*?```)/g);
  return parts
    .map((part) => {
      if (part.startsWith("```") && part.endsWith("```")) {
        const inner = part.slice(3, -3).replace(/^\w*\r?\n/, "");
        return `<pre class="md-pre"><code>${escapeHtml(inner.trimEnd())}</code></pre>`;
      }
      const escaped = escapeHtml(part);
      const withInline = formatInline(escaped);
      return withInline
        .split(/\n\n+/)
        .map((block) => {
          if (!block.trim()) return "";
          return `<p class="md-p">${block.replace(/\n/g, "<br/>")}</p>`;
        })
        .join("");
    })
    .join("");
}

export function MarkdownBody({ text }: { text: string }): ReactNode {
  return <div className="md-body" dangerouslySetInnerHTML={{ __html: markdownToHtml(text) }} />;
}
