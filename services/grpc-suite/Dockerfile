FROM python:3.12-slim

WORKDIR /app

# Install grpc tools + service deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy proto and all service files
COPY ../protobufs/benchmark.proto ./benchmark.proto
COPY postgres_service.py .
COPY mongo_service.py .
COPY redis_service.py .

# Generate Python stubs from the proto at build time
# (avoids needing to run generate_stubs.sh manually)
RUN python -m grpc_tools.protoc \
      -I . \
      -I "$(python -c 'import grpc_tools, os; print(os.path.dirname(grpc_tools.__file__))')" \
      --python_out=. \
      --grpc_python_out=. \
      benchmark.proto

EXPOSE 50051 50052 50053 50061 50062 50063
