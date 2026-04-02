"""
Proto stubs package.

Generate Python stubs with:
  pip install grpcio-tools
  python -m grpc_tools.protoc \
      -I agentix/proto \
      --python_out=agentix/proto \
      --grpc_python_out=agentix/proto \
      agentix/proto/trigger.proto

The generated files (trigger_pb2.py, trigger_pb2_grpc.py) are .gitignore'd
so they must be re-generated in each environment.
"""
