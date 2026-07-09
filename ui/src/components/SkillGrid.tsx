import { useEffect, useState } from "react";
import { fetchSkills } from "../core/http";
import type { SkillActivity, SkillManifestEntry } from "../core/types";

// Renders the loaded skills. The list comes from the backend /skills manifest, and
// live-activity comes from the vitals "skills" array — BOTH dynamic. Drop a new
// skills/*.py file into the backend and it appears here with zero edits to this file.
export function SkillGrid({ activity }: { activity: SkillActivity[] }) {
  const [skills, setSkills] = useState<SkillManifestEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="skill-grid">
      <div className="panel-title">
        Skills <span className="muted">{skills.length ? `(${skills.length})` : ""}</span>
      </div>
      {error && <div className="skill-error">offline — {error}</div>}
      <div className="skill-list">
        {skills.map((s) => {
          const isActive = activeByModule.get(s.module) ?? false;
          return (
            <div key={s.module} className={`skill ${isActive ? "skill-active" : ""}`} title={s.tools.map((t) => t.name).join(", ")}>
              <span className={`skill-dot ${isActive ? "on" : ""}`} />
              <span className="skill-name">{s.module.replace(/_ops$/, "")}</span>
              <span className="skill-cat">{s.category}</span>
              <span className="skill-count">{s.tool_count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
