import { useCiel } from "./hooks/useCiel";
import { VitalsBar } from "./components/VitalsBar";
import { SkillGrid } from "./components/SkillGrid";
import { ThoughtStream } from "./components/ThoughtStream";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Transcript } from "./io/output/Transcript";
import { TextInput } from "./io/input/TextInput";

// Layout: top vitals bar, a 3-column body (skills | conversation | cognition stream),
// and the input dock. Input/output are pulled from io/ so voice can join later
// without restructuring anything here.
export default function App() {
  const { connection, chat, thoughts, vitals, status, pendingConfirm, send, respondConfirm } =
    useCiel();

  return (
    <div className="app">
      <VitalsBar vitals={vitals} connection={connection} />

      <div className="body">
        <aside className="col-left">
          <SkillGrid activity={vitals?.skills ?? []} />
        </aside>

        <main className="col-center">
          <Transcript chat={chat} status={status} />
          {/* Input dock — TextInput today; a VoiceInput mic button slots in beside it. */}
          <div className="input-dock">
            <TextInput onSubmit={send} disabled={connection !== "open"} />
          </div>
        </main>

        <aside className="col-right">
          <ThoughtStream thoughts={thoughts} />
        </aside>
      </div>

      {pendingConfirm && <ConfirmDialog request={pendingConfirm} onRespond={respondConfirm} />}
    </div>
  );
}
