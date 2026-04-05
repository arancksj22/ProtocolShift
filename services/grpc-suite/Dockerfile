FROM python:3.12-slim

WORKDIR /app

COPY grpc-suite/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the proto and all service files
COPY protobufs/benchmark.proto ./benchmark.proto
COPY grpc-suite/postgres_service.py .
COPY grpc-suite/mongo_service.py .
COPY grpc-suite/redis_service.py .

# Generate Python stubs from the proto at build time —
# no manual generate_stubs.sh step needed when using docker-compose
RUN python -m grpc_tools.protoc \
      -I . \
      -I "$(python -c 'import grpc_tools, os; print(os.path.dirname(grpc_tools.__file__))')" \
      --python_out=. \
      --grpc_python_out=. \
      benchmark.proto

EXPOSE 50051 50052 50053 50061 50062 50063
