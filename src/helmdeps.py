#!/usr/bin/python
import argparse
import json
import logging
import os
import tarfile
import tempfile

import yaml
import pydot

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def parse_chart(chart_folder):
    chart_path = os.path.join(chart_folder, "Chart.yaml")
    if not os.path.exists(chart_path):
        raise Exception(f"Chart.yaml could not be found at {chart_path}")

    with open(chart_path, "r") as chart:
        try:
            parsed_chart = yaml.safe_load(chart)
        except yaml.YAMLError as exc:
            raise Exception(f"Failed to parse Chart.yaml file in {chart_folder}") from exc

    chart_metadata = {
        "name": parsed_chart["name"],
        "version": parsed_chart["version"],
        "dependencies": {
            dependency["name"]: {
                "name": dependency["name"],
                "version": dependency["version"],
                "repository": dependency["repository"],
                "condition": dependency.get("condition", "")
            }
            for dependency in parsed_chart.get("dependencies", [])
        }
    }

    dependencies_folder = os.path.join(chart_folder, "charts")

    if not chart_metadata["dependencies"]:
        return chart_metadata
    if not os.path.exists(dependencies_folder):
        logger.warn(f"Missing subchart folder at {dependencies_folder} for the {chart_metadata['name']} chart")
        return chart_metadata

    for file_name in os.listdir(dependencies_folder):
        file_path = os.path.join(dependencies_folder, file_name)
        if os.path.isdir(file_path) and file_name in chart_metadata["dependencies"]:
            sub_chart_metadata = parse_chart(file_path)
        elif os.path.splitext(file_name)[1] == ".tgz":
            # Extract to temp dir
            with tempfile.TemporaryDirectory() as tmp_dir, tarfile.open(file_path) as dependency_package:
                def is_within_directory(directory, target):
                    
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    
                    return prefix == abs_directory
                
                def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                
                    for member in tar.getmembers():
                        member_path = os.path.join(path, member.name)
                        if not is_within_directory(path, member_path):
                            raise Exception("Attempted Path Traversal in Tar File")
                
                    tar.extractall(path, members, numeric_owner=numeric_owner) 
                    
                
                safe_extract(dependency_package, tmp_dir)
                # Assumes that the tgz file has the Chart.yaml nested in another folder
                sub_chart_metadata = parse_chart(os.path.join(tmp_dir, os.listdir(tmp_dir)[0]))
        else:
            continue

        chart_metadata["dependencies"][sub_chart_metadata["name"]]["dependencies"] = sub_chart_metadata["dependencies"]

    for name, dependency_metadata in chart_metadata["dependencies"].items():
        if "dependencies" not in dependency_metadata:
            logger.warn(f"Unable to locate Chart.yaml for dependency {name}")

    return chart_metadata


def build_graph(chart_metadata, output_dir):
    graph = pydot.Dot('helm_dependencies', graph_type='digraph', rankdir="TB", newrank=True, nodesep=0.1, ranksep=2)

    def build_node(metadata):
        node_name = f"{metadata['name']}@{metadata['version']}"
        graph.add_node(pydot.Node(node_name))

        for dependency_metadata in metadata.get("dependencies", {}).values():
            build_node(dependency_metadata)
            dependency_node_name = f"{dependency_metadata['name']}@{dependency_metadata['version']}"
            graph.add_edge(pydot.Edge(node_name, dependency_node_name, label=dependency_metadata.get("condition", "")))

    build_node(chart_metadata)
    output_location = os.path.join(output_dir, f'{chart_metadata["name"]}_dependencies_graph_combined.png')
    graph.write_png(output_location)
    logger.info(f"Outputting to {output_location}")


def build_combined_graph(chart_metadata, output_dir):
    top_graph = pydot.Dot(graph_type='digraph', compound='true', rankdir="TB", newrank=True, nodesep=0.1, ranksep=2)

    def build_cluster(metadata, parent_node=None, parent_cluster=None):
        node_label = f"{metadata['name']}@{metadata['version']}"
        if parent_node:
            node_name = f"{parent_node}__{node_label}"
        else:
            node_name = f"{parent_node}__{node_label}"

        cluster = pydot.Cluster(node_name)
        cluster.add_node(pydot.Node(node_name, label=node_label, style="filled", fillcolor="white"))
        parent_cluster.add_subgraph(cluster)

        for dependency_metadata in metadata.get("dependencies", {}).values():
            dependency_node_name = f"{dependency_metadata['name']}@{dependency_metadata['version']}"

            if dependency_metadata.get("dependencies", {}):
                child_cluster = build_cluster(dependency_metadata, parent_node=node_name, parent_cluster=cluster)

                if dependency_metadata.get("condition", ""):
                    child_cluster.set_fillcolor("#eeeeee")
                    child_cluster.set_style("filled")
                parent_cluster.add_edge(
                    pydot.Edge(node_name, f"{node_name}__{dependency_node_name}", label=dependency_metadata.get("condition", "")))
            else:
                # Leaf node
                leaf_node = pydot.Node(f"{node_name}__{dependency_node_name}", label=dependency_node_name, style="filled", fillcolor="white")
                if dependency_metadata.get("condition", ""):
                    leaf_node.set_fillcolor("#eeeeee")
                cluster.add_node(leaf_node)
                cluster.add_edge(
                    pydot.Edge(node_name, leaf_node, label=dependency_metadata.get("condition", "")))

        return cluster

    build_cluster(chart_metadata, parent_cluster=top_graph)
    output_location = os.path.join(output_dir, f'{chart_metadata["name"]}_dependencies_graph.png')
    top_graph.write_png(output_location)
    logger.info(f"Outputting to {output_location}")


def main():
    parser = argparse.ArgumentParser(description='Build a graph or JSON output of all dependencies for a given Helm chart')
    parser.add_argument('chart_dir', help='Location of Chart.yaml file')
    parser.add_argument('--output-dir', dest="output_dir", default=".", help='Output location of the file. Defaults to the current directory')
    parser.add_argument('--output-type', dest='type', choices=["graph", "combined-graph", "json"], default="graph", help='Type of output')
    parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False, help='Enable verbose logging')
    args = parser.parse_args()

    chart_metadata = parse_chart(args.chart_dir)

    if args.output_dir and not os.path.exists(args.output_dir):
        raise Exception(f"{args.output_dir} does not exist")
    if not os.path.isdir(args.output_dir):
        raise Exception(f"{args.output_dir} is not a valid file directory")

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.type == "graph":
        build_combined_graph(chart_metadata, args.output_dir)
    elif args.type == "combined-graph":
        build_graph(chart_metadata, args.output_dir)
    elif args.type == "json":
        output_location = os.path.join(args.output_dir, f'{chart_metadata["name"]}_dependency.json')
        with open(output_location, "w") as f:
            f.write(json.dumps(chart_metadata, indent=4))
        logger.info(f"Outputting to {output_location}")


if __name__ == '__main__':
    main()
