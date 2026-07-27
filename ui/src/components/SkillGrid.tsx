import { useEffect, useMemo, useState } from "react";
import { fetchSkills } from "../core/http";
import type { SkillActivity, SkillManifestEntry } from "../core/types";

// Skills from GET /skills + live activity from vitals — zero hardcoding.
// Search + category groups are pure UI on top of that manifest.
export function SkillGrid({ activity }: { activity: SkillActivity[] }) {
  const [skills, setSkills] = useState<SkillManifestEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchSkills()
      .then((r) => {
        if (cancelled) return;
        setSkills(r.skills);
        if (r.error) setError(r.error);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const activeByModule = new Map(activity.map((a) => [a.module, a.active]));

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) =>
        s.module.toLowerCase().includes(q) ||
        s.category.toLowerCase().includes(q) ||
        s.tools.some((t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q))
    );
  }, [skills, query]);

  const groups = useMemo(() => {
    const map = new Map<string, SkillManifestEntry[]>();
    for (const s of filtered) {
      const cat = s.category || "other";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(s);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  return (
    <div className="skill-grid">
      <div className="panel-title">
        Skills <span className="muted">{skills.length ? `(${skills.length})` : ""}</span>
      </div>
      <div className="skill-search-wrap">
        <input
          className="skill-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter skills…"
          aria-label="Filter skills"
        />
      </div>
      {error && <div className="skill-error">offline — {error}</div>}
      <div className="skill-list">
        {groups.map(([cat, list]) => (
          <div key={cat} className="skill-group">
            <div className="skill-group-label">{cat}</div>
            {list.map((s) => {
              const isActive = activeByModule.get(s.module) ?? false;
              return (
                <div
                  key={s.module}
                  className={`skill${isActive ? " skill-active" : ""}`}
                  title={s.tools.map((t) => t.name).join(", ")}
                >
                  <span className={`skill-dot${isActive ? " on" : ""}`} />
                  <span className="skill-name">{s.module.replace(/_ops$/, "")}</span>
                  <span className="skill-count">{s.tool_count}</span>
                </div>
              );
            })}
          </div>
        ))}
        {!error && filtered.length === 0 && (
          <div className="skill-empty">{query ? "No match" : "Loading…"}</div>
        )}
      </div>
    </div>
  );
}
