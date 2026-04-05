#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# generate_stubs.sh
# Run this once (or after modifying benchmark.proto) to
# regenerate the Python gRPC stubs.
#
# Prerequisites:
#   pip install grpcio-tools
#
# Usage:
#   cd services/
#   bash grpc-suite/generate_stubs.sh
# ──────────────────────────────────────────────────────────
set -euo pipefail

PROTO_DIR="protobufs"
OUT_DIR="grpc-suite"

python -m grpc_tools.protoc \
  -I "${PROTO_DIR}" \
  -I "$(python -c 'import grpc_tools; import os; print(os.path.dirname(grpc_tools.__file__))')" \
  --python_out="${OUT_DIR}" \
  --grpc_python_out="${OUT_DIR}" \
  "${PROTO_DIR}/benchmark.proto"

echo "✅  Stubs generated in ${OUT_DIR}/"
echo "    benchmark_pb2.py"
echo "    benchmark_pb2_grpc.py"
