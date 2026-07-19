import { Component, type ReactNode } from "react";

// If anything inside the widget panel throws during render (a bad prop, a hook
// misuse, a null-dereference — impossible to fully rule out sight-unseen while
// debugging blind), React unmounts the WHOLE tree and shows nothing, while the
// Tauri window itself still displays at the right size — exactly the "window
// shows up but is blank" symptom being chased. This makes a render error VISIBLE
// (red text + the actual message) instead of a silent blank panel, so the next
// report says what actually broke instead of "nothing appears."
interface State { error: Error | null }

export class WidgetErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("[widget] render crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 14, color: "#f0b7b1", fontFamily: "monospace", fontSize: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Widget crashed while rendering:</div>
          <div>{this.state.error.message}</div>
        </div>
      );
    }
    return this.props.children;
  }
}
