#!/usr/bin/env bash
# Start the local model server on the GPU machine (proposal: "Hardware and Infrastructure").
# vLLM exposes an OpenAI-compatible API; the Report Service uses it with RA_PROVIDER=vllm.
#
# Proof of concept (1 x L40S 48 GB): one model at a time.
#   ./serve.sh arctic     # SQL specialist, Arctic-Text2SQL-R1-7B at 16-bit
#   ./serve.sh qwen       # general + vision model, Qwen3.8-27B at 8-bit (FP8)
# Pilot (2 x L40S): run both, one per GPU, on ports 8001 and 8002.
#
# Model weights are downloaded once and copied to the server; nothing calls out at run time.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/models}"
API_KEY="${VLLM_API_KEY:-}"           # optional: the Report Service sends it as a bearer token
which_model="${1:-arctic}"

common=(--host 0.0.0.0 --gpu-memory-utilization 0.90 --max-num-seqs 16 --enable-prefix-caching)
if [ -n "$API_KEY" ]; then common+=(--api-key "$API_KEY"); fi

case "$which_model" in
  arctic)
    CUDA_VISIBLE_DEVICES="${GPU:-0}" exec vllm serve "$MODEL_DIR/Arctic-Text2SQL-R1-7B" \
      --served-model-name arctic-text2sql-r1-7b --port "${PORT:-8001}" \
      --max-model-len 16384 --dtype bfloat16 "${common[@]}"
    ;;
  qwen)
    # --reasoning-parser splits the model's thinking from the answer, so reasoning tokens can be logged separately.
    CUDA_VISIBLE_DEVICES="${GPU:-0}" exec vllm serve "$MODEL_DIR/Qwen3.8-27B-FP8" \
      --served-model-name qwen3.8-27b --port "${PORT:-8002}" \
      --max-model-len 32768 --quantization fp8 --reasoning-parser qwen3 \
      --limit-mm-per-prompt '{"image": 4}' "${common[@]}"
    ;;
  *)
    echo "usage: $0 arctic|qwen" >&2; exit 2;;
esac
