import json

input_path = "router_finetune.jsonl"
output_path = "router_train_clean.jsonl"

bad_tools = {"get_gmail_thread"}

kept = 0
dropped = 0

with open(input_path, "r", encoding="utf-8") as fin, open(output_path, "w", encoding="utf-8") as fout:
    for line in fin:
        sample = json.loads(line)
        messages = sample.get("messages", [])

        if len(messages) < 3:
            dropped += 1
            continue

        route_text = messages[2]["content"]

        try:
            decision = json.loads(route_text)
        except json.JSONDecodeError:
            dropped += 1
            continue

        tool_names = []

        if decision.get("tool_name"):
            tool_names.append(decision["tool_name"])

        for tool in decision.get("tools", []):
            if isinstance(tool, dict) and tool.get("tool_name"):
                tool_names.append(tool["tool_name"])

        if any(tool in bad_tools for tool in tool_names):
            dropped += 1
            continue

        clean_sample = {
            "messages": messages[:3]
        }

        fout.write(json.dumps(clean_sample, ensure_ascii=False) + "\n")
        kept += 1

print("kept:", kept)
print("dropped:", dropped)