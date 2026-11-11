#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/ec2-user/vtuber_Ciel"
REPO_ROOT="$PROJECT_ROOT/tools/see-through"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
SOURCE_IMAGE="$PROJECT_ROOT/source/ciel_source.png"
OUTPUT_DIR="$PROJECT_ROOT/outputs/seethrough_seed42"
MODEL_CACHE="$PROJECT_ROOT/models/huggingface"
MINIMUM_FREE_VRAM_MIB=14000

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing isolated Python environment: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -f "$SOURCE_IMAGE" ]]; then
  echo "Missing source image: $SOURCE_IMAGE" >&2
  exit 1
fi

if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "Missing SeeThrough repository: $REPO_ROOT" >&2
  exit 1
fi

FREE_VRAM_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n 1 | tr -d ' ')"
if (( FREE_VRAM_MIB < MINIMUM_FREE_VRAM_MIB )); then
  echo "Only ${FREE_VRAM_MIB} MiB VRAM is free; ${MINIMUM_FREE_VRAM_MIB} MiB is required." >&2
  echo "Existing GPU processes were not modified. Retry later." >&2
  exit 2
fi

if find "$OUTPUT_DIR" -type f -print -quit 2>/dev/null | grep -q .; then
  echo "Output directory already contains files: $OUTPUT_DIR" >&2
  echo "Refusing to overwrite an earlier run." >&2
  exit 3
fi

mkdir -p "$OUTPUT_DIR" "$MODEL_CACHE" "$PROJECT_ROOT/logs"

echo "SeeThrough commit: $(git -C "$REPO_ROOT" rev-parse HEAD)"
echo "Free VRAM before run: ${FREE_VRAM_MIB} MiB"
echo "Source SHA-256: $(sha256sum "$SOURCE_IMAGE" | cut -d ' ' -f 1)"

cd "$REPO_ROOT"
HF_HOME="$MODEL_CACHE" PYTHONUNBUFFERED=1 "$PYTHON_BIN" \
  inference/scripts/inference_psd.py \
  --srcp "$SOURCE_IMAGE" \
  --save_dir "$OUTPUT_DIR" \
  --seed 42 \
  --resolution 1280 \
  --save_to_psd \
  --tblr_split \
  --group_offload

echo "SeeThrough run completed: $OUTPUT_DIR"
