# Agent Swift service release

This runbook requires the owner's one deployment approval. Preparing source,
tests, image, parity evidence and the offline soak does not authorize these VPS
or ECS commands. Never publish the frontend or apply the separate HTTPS plan.

## Prerequisites and reviewed artifacts

- PR #11 is merged. The base merge is `bc12f924009f9883a9a460db40487780e4ec6a57`;
  its tree matches deployed source `039fae5eae82d65895b66569efef792bedb7d8f6`.
- Use the exact candidate SHA, built agent image, manifest-pinned backend archive,
  full-suite results, secret/IP scans and twenty-symbol parity report.
- The 900-second offline gate must pass with both existing features enabled and
  `--agent-questions 100 --agent-forex-fixtures "$private_parity_inputs"`.
  The packer rejects an agent release without all hundred factual turns passing.
  No production subscriber, poller, model provider, or live database participates
  in that load test. It exercises real agent HTTP fast paths for crypto and Forex.
- AWS CLI must authenticate to the existing project. Confirm its selected Region
  in AWS Settings → View all projects → Overview → Additional Info → Region,
  or use the verified matching profile configuration. Check project plan state.
- Prepare `/etc/swiftchart-agent/runtime.env` privately on the VPS (0600), using
  the existing agent's Telegram token, unchanged allowlists, Region/model, and
  xAI settings. Do not copy OANDA credentials, CMC keys, the duplicated backend,
  old trading databases, or the operator's temporary AWS login session.
- Bedrock needs an appropriate dedicated VPS workload credential source. The
  old ECS task role is not portable to a non-ECS container. A bare operator login
  is insufficient. Verify this before any cutover; preparation fails closed if
  workload credentials are absent. The runtime file supports a dedicated
  `AWS_BEARER_TOKEN_BEDROCK` or a dedicated AWS key pair supplied privately.
- Verify existing EFS state and any legacy custom alert obligations before
  stopping the old service. Retain EFS and the old task definition for rollback.
  The new agent uses fresh bounded JSON conversations. It does not import the
  legacy SQLite conversations/conditional-alert worker or its cached market DB.

## Exact-commit local preparation

```sh
sha=$(git rev-parse HEAD)
python scripts/check_stage1.py
npm --prefix frontend test
python scripts/agent_parity.py --output "$new_private_parity_directory"
python scripts/crypto_soak.py --database "$private_frozen_database" \
  --candles "$private_crypto_candles" --output "$new_private_soak_directory" \
  --agent-questions 100 --agent-forex-fixtures "$private_parity_inputs"
python -m scripts.final_phase_pack --ref "$sha" --output "$private_backend_archive" \
  --soak-report "$new_private_soak_directory/report.json"
docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$sha" \
  -f agent/Dockerfile -t "swiftchart-agent:$sha" agent
docker save "swiftchart-agent:$sha" | gzip > "$private_agent_image_archive"
```

Keep raw parity responses, all databases, logs, test transcripts and archives
outside Git. Scans must also cover the extracted backend archive and `/app` from
the built agent image. Git-triggered Vercel publishing remains disabled.

## After the owner's yes: preflight and artifact staging

```sh
profile=swiftchart-agent
region=$(aws configure get region --profile "$profile")
aws --profile "$profile" --region "$region" sts get-caller-identity
aws --profile "$profile" --region "$region" freetier get-account-plan-state
aws --profile "$profile" --region "$region" cloudformation describe-stacks \
  --stack-name SwiftChartTradingAgent \
  --query 'Stacks[0].Outputs' > "$private_ecs_outputs"
```

Use the returned exact ClusterName and ServiceName unchanged. Record current
desired count, task definition and running task ARNs privately. Verify current
API/bot source paths still equal the previous source above. No unexpected source,
allowlist, identity, container, nginx, or task-definition drift may be overwritten.
Test the Telegram identity with `getMe`, never another poller. A separately
approved `aws login --profile swiftchart-agent` may be needed for an expired CLI
session; never export its operator credentials into the agent container.

Copy only the two reviewed clean archives to the VPS. Create a new release root
`/opt/swiftchart-releases/$sha`; refuse an existing path; extract the backend
payload there and load the image with `docker load`. No Git reset or working-tree
sync is used. The shared environment, API database and existing bot/executor
processes are not replaced. Run an offline import check on the extracted API.

## VPS prepare and API switch

Set `api_nginx_file` to the verified current API server configuration path and
`state=/opt/swiftchart-agent-releases/$sha`. All commands below run on the VPS.

```sh
python3 "$root/scripts/agent_vps_release.py" prepare --approved \
  --root "$root" --commit "$sha" --state "$state" \
  --previous-root /opt/swiftchart-releases/039fae5eae82d65895b66569efef792bedb7d8f6 \
  --nginx "$api_nginx_file" --runtime-env /etc/swiftchart-agent/runtime.env
python3 "$root/scripts/agent_vps_release.py" activate-api --approved --state "$state"
python3 "$root/scripts/agent_vps_release.py" verify --approved --state "$state"
```

Prepare verifies every manifest file and the hundred-question soak receipt,
backs up the current live database consistently, saves API/nginx definitions,
creates a private shared internal token and prepares only the API process switch.
Activation denies `/internal/` in API nginx server blocks, validates nginx before
reload, then recreates only `swiftchart-api` against the new immutable source.
The signal bot and executor must retain their process IDs. Failed API activation
attempts rollback; it never retries a deployment. No database schema change or
database restore is part of this release.

Before stopping the old agent, run the image's explicit setup checks with the
private runtime file, host networking and the production resource limits:

```sh
docker run --rm --network host --read-only --cpus 0.25 --memory 768m \
  --memory-swap 768m --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=134217728 \
  --env-file /etc/swiftchart-agent/runtime.env \
  --entrypoint python "swiftchart-agent:$sha" \
  -m telegram_bot.check_setup --api --telegram --bedrock
```

This performs one Bedrock readiness request, API reads and Telegram `getMe` only.
It does not start a poller or send a Telegram message. If it fails, restore the
previous API and leave the old ECS poller intact.

## Non-overlapping poller handover

On the local authenticated CLI, using the exact previously recorded identifiers:

```sh
aws --profile "$profile" --region "$region" ecs update-service \
  --cluster "$cluster" --service "$service" --desired-count 0
aws --profile "$profile" --region "$region" ecs wait services-stable \
  --cluster "$cluster" --services "$service"
aws --profile "$profile" --region "$region" ecs wait tasks-stopped \
  --cluster "$cluster" --tasks $previous_task_arns
```

Re-describe the service and every previous task. Only after desired/running/pending
are all zero and all previous tasks are STOPPED, write the verified private
`$state/legacy-stopped.json` receipt, including `all_previous_tasks_stopped: true`.
Never fabricate that receipt from an `update-service` acknowledgment.

Then on the VPS:

```sh
python3 "$root/scripts/agent_vps_release.py" start-agent --approved \
  --state "$state" --image "swiftchart-agent:$sha"
python3 "$root/scripts/agent_vps_release.py" verify --approved --state "$state"
docker inspect swiftchart-agent --format '{{json .HostConfig}}'
```

Inspect only sanitized container metadata; environment values are private.
Verify 0.25 CPU, 768 MiB RAM and swap cap, 128 PIDs, read-only root, bounded logs,
no published port and only `/var/lib/swiftchart-agent` mounted for JSON/log state.
The API trading database and old EFS are never mounted into this container.
Do not create another signal scanner or alert worker.

Verify public denial of `/internal/agent/markets`, direct local token access,
normal website/API health, and live API-to-agent parity. Confirm a real allowed
Telegram question receives an answer; distinguish actual inbound polling from
an injected handler/outbound-only probe. Record reply IDs/timing privately.
Only after the Telegram check succeeds, archive `OffsetOWS/swiftchart-trading-agent`
with `gh repo archive OffsetOWS/swiftchart-trading-agent --yes`. Verify private
and archived flags. Never delete the repository, EFS, or rollback task definition.

## Rollback

```sh
# VPS: stops/removes only this new agent, restores only API/nginx definitions.
python3 "$root/scripts/agent_vps_release.py" rollback --approved --state "$state"
```

Confirm the new container is stopped before restoring the saved old ECS service
task definition and saved desired count. Wait for stability and an inbound
Telegram check. Preserve every database write and receipt since deployment;
do not restore the pre-release DB backup over new trades or messages. Keep the
private state directory, JSON conversations and logs for reconciliation. Do not
reset the executor, signal bot, old checkout, shared environment or frontend.
