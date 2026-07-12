"""
Ensures the gRPC Python stubs (benchmark_pb2 / benchmark_pb2_grpc) exist in
this directory, generating them from the single source-of-truth proto at
local/services/protobufs/benchmark.proto if missing.

The generated files are gitignored — they are build artifacts, exactly like
the ones the grpc-suite Dockerfile generates at image build time.
"""

from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROTO_DIR = BENCH_DIR.parent / "local" / "services" / "protobufs"
PROTO_FILE = PROTO_DIR / "benchmark.proto"


def ensure_stubs() -> None:
    if (BENCH_DIR / "benchmark_pb2.py").exists() and (
        BENCH_DIR / "benchmark_pb2_grpc.py"
    ).exists():
        return
    if not PROTO_FILE.exists():
        raise FileNotFoundError(f"Proto not found: {PROTO_FILE}")
    from grpc_tools import protoc

    rc = protoc.main(
        [
            "protoc",
            f"-I{PROTO_DIR}",
            f"--python_out={BENCH_DIR}",
            f"--grpc_python_out={BENCH_DIR}",
            str(PROTO_FILE),
        ]
    )
    if rc != 0:
        raise RuntimeError(f"protoc failed with exit code {rc}")


if __name__ == "__main__":
    ensure_stubs()
    print(f"Stubs present in {BENCH_DIR}")
