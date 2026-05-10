# Ciel 2.0 — Hybrid Memory (RAG) Walkthrough

## How the Hybrid Memory System Works

Ciel has **two types of memory** that work together:

```
┌─────────────────────────────────────────────────┐
│                  USER MESSAGE                    │
│         "What languages for my project?"         │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              1. RAG RECALL                       │
│  Search ChromaDB for similar past conversations  │
│  Found: "Midnight Falcon uses Python and Rust"   │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              2. ENRICH INPUT                     │
│  Prepend: [RECALLED PAST CONTEXT]                │
│  + [CURRENT USER REQUEST]                        │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              3. BRAIN ROUTES                     │
│  Sees recalled context → routes to "chat"        │
│  Task: "Tell user Midnight Falcon uses           │
│         Python and Rust"                         │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              4. WORKER ANSWERS                   │
│  "Operation Midnight Falcon uses Python & Rust"  │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              5. SAVE + TRIM                      │
│  Add to chat_history (JSON, max 20 messages)     │
│  If overflow → archive old msgs to ChromaDB      │
└─────────────────────────────────────────────────┘
```

---

## The Two Memory Layers

### Short-Term Memory (JSON)
- **File:** `ciel_data/memory_bank.json`
- **Capacity:** Last 20 messages
- **Speed:** Instant (in-memory)
- **Purpose:** Keeps the current conversation coherent
- **What happens when full:** Oldest messages are archived to RAG, then removed

### Long-Term Memory (RAG / ChromaDB)
- **Folder:** `ciel_data/vector_memory/`
- **Capacity:** Unlimited (grows forever)
- **Speed:** ~50ms per search (local)
- **Purpose:** Recall past conversations from days/weeks/months ago
- **How it searches:** Converts your question into a mathematical "embedding" (vector), then finds past conversations with the most similar meaning

---

## Reading the thoughts.log — Entry by Entry

Every line in `thoughts.log` follows this format:
```
[TIMESTAMP] [ACTOR] [ACTION]
content...
------------------------------------------------------------
```

Here are all the actors and what they mean:

| Actor | Action | What it means |
|-------|--------|---------------|
| `[USER]` | `[REQUEST]` | What the user typed (may include recalled context) |
| `[BRAIN]` | `[ROUTE_DECISION]` | Brain's JSON routing decision |
| `[WORKER]` | `[CHAT_TASK]` | The prompt sent to Worker for text generation |
| `[WORKER]` | `[CHAT_RESPONSE]` | Worker's answer |
| `[WORKER]` | `[FORMAT_TASK]` | Prompt sent to Worker to format a tool result |
| `[WORKER]` | `[FORMAT_RESPONSE]` | Worker's formatted output |
| `[WORKER]` | `[ERROR_REPHRASE]` | Worker rephrasing an error for the user |
| `[TOOL]` | `[RESULT]` | Raw tool execution result |
| `[TOOL]` | `[ERROR]` | Tool execution failed |
| `[RAG]` | `[ARCHIVED]` | Old message saved to long-term memory |
| `[RAG]` | `[RECALLED]` | Past conversation found relevant to current input |
| `[HEALING]` | `[ATTEMPT_N]` | Self-healing triggered on tool failure |

---

## Full Example: The "Amnesia" Test Walkthrough

### Step 1 — User plants a fact (Message #1)
```
[USER] [REQUEST]
I am planning a secret project named 'Operation Midnight Falcon' 
which uses Python and Rust together.
```
Brain routes to `save_fact` tool → saved to `facts.json`.

### Step 2 — 25 filler messages flood short-term memory (Messages #2-26)
```
[USER] [REQUEST]
What is 2 + 2?
```
Short queries (< 15 chars) skip RAG — no wasted searches.

As messages exceed 20, `_trim_history()` fires:
```
[RAG] [ARCHIVED]
Human: I am planning a secret project named 'Operation Midnight 
Falcon' which uses Python and Rust together. | Ai: Fact saved...
```
The "Midnight Falcon" conversation is now **gone from JSON** but **saved in ChromaDB**.

### Step 3 — The recall test (Message #27)
```
[RAG] [RECALLED]
[2026-05-11] Human: I am planning a secret project named 
'Operation Midnight Falcon' which uses Python and Rust together. 
| Ai: Fact saved successfully
```
ChromaDB found the relevant memory! Now it gets prepended:

```
[USER] [REQUEST]
[RECALLED PAST CONTEXT (from previous conversations)]:
Human: I am planning a secret project named 'Operation Midnight 
Falcon' which uses Python and Rust together.

[CURRENT USER REQUEST]:
What languages are we using for my secret project?
```

Brain sees the recalled context and routes to `chat`:
```
[BRAIN] [ROUTE_DECISION]
{
  "action": "chat",
  "task": "Based on our previous conversation, tell the user that 
  their secret project, 'Operation Midnight Falcon', uses Python and Rust."
}
```

Worker answers:
```
[WORKER] [CHAT_RESPONSE]
Operation Midnight Falcon uses Python and Rust.
```

✅ **Perfect recall from long-term memory.**

---

## Key Design Decisions

### Why skip RAG for short queries?
```python
MIN_QUERY_LENGTH = 15  # "Hello" = 5 chars → skip RAG
```
"Hello", "What is 2+2?", "Hi" don't need long-term memory. Skipping saves ~50ms per message and avoids noisy irrelevant results.

### Why minimum relevance score?
```python
MIN_RELEVANCE_SCORE = 0.65  # Only high-confidence matches
```
ChromaDB uses cosine similarity (0 = identical, 2 = opposite). We convert to a 0-1 score and only inject results above 0.65. This prevents weak matches like "How does WiFi work?" pulling up "What is Python?" just because both are tech questions.

### Why archive as user+assistant pairs?
```python
"Human: What is my project? | Ai: Operation Midnight Falcon uses Python and Rust."
```
Storing both sides together means the search can match on either the question OR the answer. If you ask "What did I name my project?", it matches the "Human:" part. If you ask "Which project uses Rust?", it matches the "Ai:" part.

---

## File Locations

```
ciel_data/
├── memory_bank.json          ← Short-term (last 20 messages)
├── facts.json                ← Fact vault (key-value, manual save/get)
├── vector_memory/            ← Long-term RAG (ChromaDB)
│   ├── chroma.sqlite3        ← Vector database
│   └── *.bin                 ← Embedding index files
└── logs/
    └── thoughts.log          ← Full audit trail of all actors
```
