#!/bin/bash
# Generate Python files from proto definitions

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PROTO_DIR="$REPO_ROOT/src/pixell_runtime/proto"

cd "$REPO_ROOT"

echo "🔧 Generating Python proto files..."
echo "   Proto dir: $PROTO_DIR"
echo "   Output dir: $PROTO_DIR"

# Generate Python proto and gRPC files
python -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$PROTO_DIR" \
    --grpc_python_out="$PROTO_DIR" \
    "$PROTO_DIR/agent.proto"

echo "✅ Proto generation complete!"
echo ""
echo "Generated files:"
ls -lh "$PROTO_DIR"/*.py | grep -E "(agent_pb2|agent_pb2_grpc)" || true
