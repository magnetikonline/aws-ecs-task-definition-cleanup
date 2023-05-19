# AWS ECS task definition cleanup

Python CLI utility to help maintain legacy [AWS ECS task definitions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definitions.html).

The utility offers the following:

- Set task definitions `ACTIVE` -> `INACTIVE` that are not associated to ECS cluster tasks.
- Mark `INACTIVE` task definitions for deletion.

##  Requirements

- Python 3.10+
- [Boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
	```sh
	pip install boto3

## Usage

**Note:** adding `--commit` argument will apply changes to ECS task definitions, otherwise all commands will execute in a "dry run" mode only.

Locate task definitions unused by ECS cluster tasks, set `INACTIVE`:

```sh
AWS_REGION="ap-southeast-2" \
  ./cleanup.py --set-inactive
```

By default `--set-inactive` _will not_ mark task definitions `INACTIVE` if _any version_ of said definition is in use.

For example:

- Task definition `arn:aws:ecs:REGION:ACCOUNT_ID:task-definition/my-task-definition:123` is currently in use by an ECS cluster task.
- Utility locates unused definition `arn:aws:ecs:REGION:ACCOUNT_ID:task-definition/my-task-definition:666`, but will not mark `INACTIVE` as there are other `my-task-definition` definitions in use.

To set all unused task definition _versions_ `INACTIVE` - only leaving currently in use definitions `ACTIVE`:

```sh
AWS_REGION="ap-southeast-2" \
  ./cleanup.py --set-inactive=aggressive
```

Mark currently `INACTIVE` task definitions for deletion by the AWS ECS sub-system:

```sh
AWS_REGION="ap-southeast-2" \
  ./cleanup.py --delete-inactive
```

Finally, both operations can be combined and run together:

```sh
AWS_REGION="ap-southeast-2" \
  ./cleanup.py --set-inactive=aggressive --delete-inactive
```

## Reference

- https://aws.amazon.com/blogs/containers/announcing-amazon-ecs-task-definition-deletion/
- https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deregister-task-definition-v2.html
- https://docs.aws.amazon.com/AmazonECS/latest/developerguide/delete-task-definition-v2.html
