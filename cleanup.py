#!/usr/bin/env python3

import argparse
import sys
import time
from typing import Callable

import boto3
import botocore.exceptions

ECS_TASK_QUERY_BATCH_SIZE = 100
ECS_TASK_DEFINITION_DELETE_BATCH_SIZE = 10


def exit_error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def write_warning(message: str) -> None:
    print(f"Warning: {message}")


def read_arguments() -> tuple[str, bool, bool]:
    # create parser
    parser = argparse.ArgumentParser(
        description="Amazon Elastic Container Service (ECS) task definition cleanup"
    )

    parser.add_argument(
        "--set-inactive",
        choices=["retain-versions", "aggressive"],
        const="retain-versions",
        nargs="?",
        help="unused task definitions set inactive, 'aggressive' targets all definition versions (default: %(const)s)",
    )

    parser.add_argument(
        "--delete-inactive",
        action="store_true",
        help="delete inactive task definitions",
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help="apply changes to ECS, dry run if not provided",
    )

    arg_list = parser.parse_args()

    if (arg_list.set_inactive is None) and (not arg_list.delete_inactive):
        exit_error("must supply at least one of --set-inactive or --delete-inactive")

    return (arg_list.set_inactive, arg_list.delete_inactive, arg_list.commit)


def dryrun_message(commit: bool) -> str:
    if not commit:
        return " (DRYRUN)"

    return ""


def canonical_task_definition_arn(task_definition_arn: str) -> str:
    end_cut = task_definition_arn.rfind(":")
    return task_definition_arn[0:end_cut]


def ecs_cluster_arn_list(client) -> list[str]:
    resp = client.list_clusters()
    return sorted(resp["clusterArns"])


def ecs_cluster_task_arn_list(client, cluster_arn: str) -> list[str]:
    resp = client.list_tasks(cluster=cluster_arn)
    return resp["taskArns"]


def ecs_cluster_task_definition_arn_list(
    client, cluster_arn: str, task_arn_list: list[str]
) -> list[str]:
    arn_list = []
    while task_arn_list:
        # grab maximum batch of `ECS_TASK_QUERY_BATCH_SIZE` ARNs from `task_arn_list`
        query_arn_list = task_arn_list[:ECS_TASK_QUERY_BATCH_SIZE]
        del task_arn_list[:ECS_TASK_QUERY_BATCH_SIZE]

        # query for tasks, add each task definition ARN used onto list
        resp = client.describe_tasks(cluster=cluster_arn, tasks=query_arn_list)
        for item in resp["tasks"]:
            arn_list.append(item["taskDefinitionArn"])

    return arn_list


def ecs_task_definition_arn_list(client, status: str) -> list[str]:
    arn_list = []
    next_token = ""
    while True:
        # query for next page of task definitions, append results onto return list
        resp = client.list_task_definitions(status=status, nextToken=next_token)
        arn_list.extend(resp["taskDefinitionArns"])
        if not "nextToken" in resp:
            # end of results
            break

        next_token = resp["nextToken"]

    return arn_list


def ecs_task_definition_deregister(client, definition_arn: str) -> None:
    client.deregister_task_definition(taskDefinition=definition_arn)


def ecs_task_definition_delete(client, definition_arn_list: list[str]) -> None:
    client.delete_task_definitions(taskDefinitions=definition_arn_list)


def process_aws_api_batch_throttle(
    process_list: list[str], batch_size: int, api_handler: Callable[[list[str]], None]
):
    # process items in `process_list` in batches set by `batch_size`, passing batch to `api_handler`
    # if `botocore.exceptions.ClientError` throttling exception thrown - pause and retry
    while process_list:
        try:
            # fetch batch of items, pass to handler
            api_handler(process_list[:batch_size])
            del process_list[:batch_size]

        except botocore.exceptions.ClientError as err:
            # extract error code, determine if API throttling
            error_code = err.response.get("Error", {}).get("Code")
            if error_code == "ThrottlingException":
                write_warning("API rate limit exceeded - sleeping for a moment")
                time.sleep(2)


def main():
    # read CLI arguments, create ECS client
    (set_inactive_mode, delete_inactive, commit) = read_arguments()
    client = boto3.client("ecs")

    if set_inactive_mode is not None:
        # task: execute active unused task definitions set inactive
        print(
            f"Setting unused ECS task definitions ACTIVE -> INACTIVE{dryrun_message(commit)}"
        )
        definition_in_use_arn_list = []
        definition_in_use_canonical_arn_set = set()

        # process each ECS cluster in turn
        for cluster_arn in ecs_cluster_arn_list(client):
            print(f"Processing ECS cluster: {cluster_arn}")

            # pull task list for cluster and in turn, the in-use task definition for each of those tasks
            task_arn_list = ecs_cluster_task_arn_list(client, cluster_arn)
            in_use_arn_list = ecs_cluster_task_definition_arn_list(
                client, cluster_arn, task_arn_list
            )

            # add in-use task definition/canonical ARNs to collections
            definition_in_use_arn_list.extend(in_use_arn_list)
            for definition_arn in in_use_arn_list:
                definition_in_use_canonical_arn_set.add(
                    canonical_task_definition_arn(definition_arn)
                )

            print(f"Task count: {len(task_arn_list)}")

        # fetch list of task definitions currently active
        print("\nFetching ACTIVE ECS task definitions")
        active_arn_list = ecs_task_definition_arn_list(client, "ACTIVE")
        print(f"Definition count: {len(active_arn_list)}")

        # determine task definitions not assigned to active ECS cluster tasks
        unused_arn_list = []
        for active_arn in active_arn_list:
            if (set_inactive_mode == "retain-versions") and (
                canonical_task_definition_arn(active_arn)
                in definition_in_use_canonical_arn_set
            ):
                # canonical ARN for task definition in use by cluster task - do not set INACTIVE
                continue

            if active_arn not in definition_in_use_arn_list:
                unused_arn_list.append(active_arn)

        print(f"Unused definition count: {len(unused_arn_list)}\n")

        # deregister ECS task definitions determined to be unused by current cluster tasks
        def _definition_deregister(batch_list: list[str]):
            definition_arn = batch_list[0]
            print(f"Deregister: {definition_arn}{dryrun_message(commit)}")
            if commit:
                ecs_task_definition_deregister(client, definition_arn)

        process_aws_api_batch_throttle(unused_arn_list, 1, _definition_deregister)

    if delete_inactive:
        # task: delete inactive task definitions
        print(
            f"Mark INACTIVE ECS task definitions for deletion{dryrun_message(commit)}"
        )

        # fetch list of task definitions currently inactive
        print("\nFetching INACTIVE ECS task definitions")
        inactive_arn_list = ecs_task_definition_arn_list(client, "INACTIVE")
        print(f"Definition count: {len(inactive_arn_list)}")

        # delete each task definition currently inactive
        def _definition_delete(batch_list: list[str]):
            for definition_arn in batch_list:
                print(f"Delete: {definition_arn}{dryrun_message(commit)}")

            if commit:
                ecs_task_definition_delete(client, batch_list)

        process_aws_api_batch_throttle(
            inactive_arn_list,
            ECS_TASK_DEFINITION_DELETE_BATCH_SIZE,
            _definition_delete,
        )


if __name__ == "__main__":
    main()
