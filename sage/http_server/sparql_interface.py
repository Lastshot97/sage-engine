# sparql_interface.py
# Author: Thomas MINIER - MIT License 2017-2018
from flask import Blueprint, request, Response, render_template, abort
from sage.query_engine.sage_engine import SageEngine
from sage.query_engine.optimizer.plan_builder import build_query_plan
from sage.query_engine.optimizer.query_parser import parse_query
from sage.query_engine.iterators.loader import load
from sage.query_engine.exceptions import UnsupportedSPARQL
from sage.http_server.schema import QueryRequest, SageSparqlQuery
from sage.http_server.utils import format_graph_uri, encode_saved_plan, decode_saved_plan, secure_url, format_marshmallow_errors, sage_http_error
from sage.database.descriptors import VoidDescriptor
import sage.http_server.responses as responses
import logging
from json import dumps
from time import time
from uuid import uuid4


def execute_query(query, default_graph_uri, next_link, dataset, mimetype, url):
    """
        Execute a query using the SageEngine and returns the appropriate HTTP response.
        Any failure will results in a rollback/abort on the current query execution.
    """
    graph = None
    try:
        graph_name = format_graph_uri(default_graph_uri, url)
        if not dataset.has_graph(graph_name):
            return sage_http_error("No RDF graph matching the default URI provided was found.")
        graph = dataset.get_graph(graph_name)

        # decode next_link or build query execution plan
        cardinalities = dict()
        start = time()
        if next_link is not None:
            if dataset.is_stateless:
                saved_plan = next_link
            else:
                saved_plan = dataset.statefull_manager.get_plan(next_link)
            plan = load(decode_saved_plan(saved_plan), dataset)
        else:
            plan, cardinalities = parse_query(query, dataset, graph_name, url)
        loading_time = (time() - start) * 1000

        # execute query
        engine = SageEngine()
        quota = graph.quota / 1000
        max_results = graph.max_results
        bindings, saved_plan, is_done, abort_reason = engine.execute(plan, quota, max_results)

        # commit or abort (if necessary)
        if abort_reason is not None:
            graph.abort()
            return sage_http_error("The SPARQL query has been aborted for the following reason: '{}'".format(abort_reason))
        else:
            graph.commit()

        start = time()
        # encode saved plan if query execution is not done yet and there was no abort
        next_page = None
        if (not is_done) and abort_reason is None:
            next_page = encode_saved_plan(saved_plan)
            if not dataset.is_stateless:
                # generate the plan id if this is the first time we execute this plan
                plan_id = next_link if next_link is not None else str(uuid4())
                dataset.statefull_manager.save_plan(plan_id, next_page)
                next_page = plan_id
        elif is_done and (not dataset.is_stateless) and next_link is not None:
            # delete the saved plan, as it will not be reloaded anymore
            dataset.statefull_manager.delete_plan(next_link)

        exportTime = (time() - start) * 1000
        stats = {"cardinalities": cardinalities, "import": loading_time, "export": exportTime}

        # send response
        if mimetype == "application/sparql-results+json":
            return Response(responses.w3c_json_streaming(bindings, next_page, stats, url), content_type='application/json')
        if mimetype == "application/xml" or mimetype == "application/sparql-results+xml":
            return Response(responses.w3c_xml(bindings, next_page, stats), content_type="application/xml")
        if mimetype == "application/json":
            return Response(responses.raw_json_streaming(bindings, next_page, stats, url), content_type='application/json')
        # otherwise, return the HTML version
        return render_template("sage_page.html", query=query, default_graph_uri=default_graph_uri, bindings=bindings, next_page=next_page, stats=stats)
    except Exception as err:
        # abort all ongoing transactions, then forward the exception to the main loop
        if graph is not None:
            graph.abort()
        raise err


def sparql_blueprint(dataset):
    """Get a Blueprint that implement a SPARQL interface with quota on /sparql/<dataset-name>"""
    s_blueprint = Blueprint("sparql-interface", __name__)

    @s_blueprint.route("/sparql", methods=["GET", "POST"])
    def sparql_index():
        mimetype = request.accept_mimetypes.best_match([
            "application/json", "application/xml",
            "application/sparql-results+json", "application/sparql-results+xml",
            "text/html"
        ])
        try:
            url = secure_url(request.base_url)
            # parse arguments
            if request.method == "GET":
                query = request.args.get("query") or None
                default_graph_uri = request.args.get("default-graph-uri") or None
                next_link = request.args.get("next") or None
                # ensure that both the query and default-graph-uri params are set
                if (query is None or default_graph_uri is None) and (next_link is None or default_graph_uri is None):
                    return sage_http_error("Invalid request sent to server: a GET request must contains both parameters 'query' and 'default-graph-uri'. See <a href='http://sage.univ-nantes.fr/documentation'>the API documentation</a> for reference.")
            elif request.method == "POST" and request.is_json:
                # POST query
                post_query, err = SageSparqlQuery().load(request.get_json())
                if err is not None and len(err) > 0:
                    # TODO better formatting
                    return Response(format_marshmallow_errors(err), status=400)
                query = post_query["query"]
                default_graph_uri = post_query["defaultGraph"]
                next_link = post_query["next"] if 'next' in post_query else None
            else:
                return sage_http_error("Invalid request sent to server: a GET request must contains both parameters 'query' and 'default-graph-uri'. See <a href='http://sage.univ-nantes.fr/documentation'>the API documentation</a> for reference.")
            # execute query
            return execute_query(query, default_graph_uri, next_link, dataset, mimetype, url)
        except UnsupportedSPARQL as err:
            logging.error(err)
            return sage_http_error(str(err))
        except Exception as err:
            logging.error(err)
            abort(500)

    @s_blueprint.route("/sparql/<graph_name>", methods=["GET", "POST"])
    def sparql_query(graph_name):
        """WARNING: old API, deprecated"""
        graph = dataset.get_graph(graph_name)
        if graph is None:
            abort(404)

        logging.debug('[/sparql/] Corresponding dataset found')
        mimetype = request.accept_mimetypes.best_match([
            "application/json", "application/xml",
            "application/sparql-results+json", "application/sparql-results+xml"
        ])
        url = secure_url(request.url)
        try:
            # A GET request always returns the homepage of the dataset
            if request.method == "GET" or (not request.is_json):
                dinfo = graph.describe(url)
                to_publish = dumps(dinfo) if graph.config()['publish'] else None
                dinfo['@id'] = url
                void_desc = {
                    "nt": VoidDescriptor(url, graph).describe("ntriples"),
                    "ttl": VoidDescriptor(url, graph).describe("turtle"),
                    "xml": VoidDescriptor(url, graph).describe("xml")
                }
                queries = [q for q in graph.example_queries if q["publish"]]
                return render_template("website/sage_dataset.html", dataset_info=dinfo, void_desc=void_desc, to_publish=to_publish, queries=queries)

            engine = SageEngine()
            post_query, err = QueryRequest().load(request.get_json())
            if err is not None and len(err) > 0:
                return Response(format_marshmallow_errors(err), status=400)
            quota = graph.quota / 1000
            max_results = graph.max_results

            # Load next link
            next_link = None
            if 'next' in post_query and post_query["next"] is not None:
                if dataset.is_stateless:
                    saved_plan = post_query["next"]
                else:
                    saved_plan = dataset.statefull_manager.get_plan(post_query["next"])
                plan = load(decode_saved_plan(saved_plan), dataset)

            # build physical query plan, then execute it with the given quota
            start = time()
            plan, cardinalities = build_query_plan(post_query["query"], dataset, graph_name, next_link)
            loading_time = (time() - start) * 1000  # convert in milliseconds
            bindings, saved_plan, is_done, abort_reason = engine.execute(plan, quota, max_results)

            # commit or abort (if necessary)
            if abort_reason is not None:
                graph.abort()
                return sage_http_error("The SPARQL query has been aborted for the following reason: '{}'".format(abort_reason))
            else:
                graph.commit()

            start = time()
            # encode saved plan if query execution is not done yet and there was no abort
            next_page = None
            if (not is_done) and abort_reason is None:
                next_page = encode_saved_plan(saved_plan)
                if not dataset.is_stateless:
                    # generate the plan id if this is the first time we execute this plan
                    plan_id = next_link if next_link is not None else str(uuid4())
                    dataset.statefull_manager.save_plan(plan_id, next_page)
                    next_page = plan_id
            elif is_done and (not dataset.is_stateless) and next_link is not None:
                # delete the saved plan, as it will not be reloaded anymore
                dataset.statefull_manager.delete_plan(next_link)

            exportTime = (time() - start) * 1000  # convert in milliseconds
            stats = {"cardinalities": cardinalities, "import": loading_time, "export": exportTime}

            if mimetype == "application/sparql-results+json":
                res = Response(responses.w3c_json_streaming(bindings, next_page, stats, url), content_type='application/json')
            if mimetype == "application/xml" or mimetype == "application/sparql-results+xml":
                res = Response(responses.w3c_xml(bindings, next_page, stats), content_type="application/xml")
            else:
                res = Response(responses.raw_json_streaming(bindings, next_page, stats, url), content_type='application/json')
            # set deprecation warning in headers
            res.headers.add("Warning", "199 SaGe/2.0 \"You are using a deprecated API. Consider uppgrading to the SaGe SPARQL query API. See http://sage.univ-nantes.fr/documentation fore more details.\"")
            return res
        except Exception as err:
            # abort all ongoing transactions, then abort the HTTP request
            graph.abort()
            logging.error(err)
            abort(500)
    return s_blueprint
