# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
import grpc

import sage.grpc.service_pb2 as service__pb2


class SageSPARQLStub(object):
  """The SaGe SPARQL query server
  """

  def __init__(self, channel):
    """Constructor.

    Args:
      channel: A grpc.Channel.
    """
    self.Query = channel.unary_unary(
        '/sage.SageSPARQL/Query',
        request_serializer=service__pb2.SageQuery.SerializeToString,
        response_deserializer=service__pb2.SageResponse.FromString,
        )


class SageSPARQLServicer(object):
  """The SaGe SPARQL query server
  """

  def Query(self, request, context):
    """Execute a SPARQL query using the Web preemption model
    """
    context.set_code(grpc.StatusCode.UNIMPLEMENTED)
    context.set_details('Method not implemented!')
    raise NotImplementedError('Method not implemented!')


def add_SageSPARQLServicer_to_server(servicer, server):
  rpc_method_handlers = {
      'Query': grpc.unary_unary_rpc_method_handler(
          servicer.Query,
          request_deserializer=service__pb2.SageQuery.FromString,
          response_serializer=service__pb2.SageResponse.SerializeToString,
      ),
  }
  generic_handler = grpc.method_handlers_generic_handler(
      'sage.SageSPARQL', rpc_method_handlers)
  server.add_generic_rpc_handlers((generic_handler,))