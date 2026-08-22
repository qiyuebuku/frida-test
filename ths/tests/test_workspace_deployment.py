from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]


def test_workspace_has_one_public_deployment_entrypoint() -> None:
    deploy = (WORKSPACE / "deploy.sh").read_text(encoding="utf-8")

    assert "--component" in deploy
    assert "--dry-run" in deploy
    assert "server-api|server-persist|server-scheduler" in deploy
    assert "git merge-base --is-ancestor" in deploy
    assert "rsync" not in deploy


def test_production_deployment_requires_github_main_push() -> None:
    deploy = (WORKSPACE / "deploy.sh").read_text(encoding="utf-8")
    workflow = (
        WORKSPACE / ".github" / "workflows" / "ths-android-production.yml"
    ).read_text(encoding="utf-8")

    assert '"${GITHUB_ACTIONS:-}" == "true"' in deploy
    assert '"${GITHUB_EVENT_NAME:-}" == "push"' in deploy
    assert '"${GITHUB_REF:-}" == "refs/heads/main"' in deploy
    assert '"${REVISION}" == "${GITHUB_SHA}"' in deploy
    assert "git status --porcelain)" in deploy
    assert "untracked-files=no" not in deploy

    assert "workflow_dispatch:" not in workflow
    assert "group: smart-fund-production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "github.event_name == 'push'" in workflow
    assert 'github.ref == \'refs/heads/main\'' in workflow
    assert 'production --revision "$GITHUB_SHA"' in workflow
    assert "THS_PRODUCTION_SSH_KNOWN_HOSTS" in workflow
    assert "ssh-keyscan" not in workflow


def test_production_deployment_verifies_ssh_host_key() -> None:
    workspace_deploy = (WORKSPACE / "deploy.sh").read_text(encoding="utf-8")
    ths_deploy = (WORKSPACE / "ths" / "deployment" / "deploy.sh").read_text(
        encoding="utf-8"
    )
    server_deploy = (
        WORKSPACE / "smart-fund-server" / "deployment" / "deploy_113.sh"
    ).read_text(encoding="utf-8")

    for deploy in (workspace_deploy, ths_deploy, server_deploy):
        assert "StrictHostKeyChecking=yes" in deploy
        assert "StrictHostKeyChecking=no" not in deploy


def test_legacy_ths_deployer_fails_closed_on_redroid_production() -> None:
    deploy = (WORKSPACE / "ths" / "deployment" / "deploy.sh").read_text(
        encoding="utf-8"
    )

    assert 'docker container inspect ths-trade' in deploy
    assert "Redroid production detected" in deploy
    assert deploy.index("docker container inspect ths-trade") < deploy.index(
        "git -C '${REMOTE_GIT_DIR}' fetch"
    )


def test_component_revisions_are_recorded_independently() -> None:
    deploy = (WORKSPACE / "deploy.sh").read_text(encoding="utf-8")

    assert "DEPLOYED_THS_HOOK" in deploy
    assert "DEPLOYED_THS_RUNTIME" in deploy
    assert "DEPLOYED_SERVER_API" in deploy
    assert "DEPLOYED_SERVER_WORKERS" in deploy
    assert "DEPLOYED_SERVER_THS_STREAM" in deploy


def test_ths_public_directory_is_not_flattened_with_internal_files() -> None:
    deployment = WORKSPACE / "ths" / "deployment"
    public_files = sorted(path.name for path in deployment.iterdir() if path.is_file())

    assert public_files == ["README.md", "deploy.sh"]
    assert (deployment / "internal" / "runtime").is_dir()
    assert (deployment / "internal" / "systemd").is_dir()
    assert (deployment / "internal" / "remote").is_dir()


def test_server_source_sync_uses_git_revision() -> None:
    server_deploy = (
        WORKSPACE / "smart-fund-server" / "deployment" / "deploy_113.sh"
    ).read_text(encoding="utf-8")

    assert "git -C '${REMOTE_GIT_DIR}' fetch" in server_deploy
    assert "git -C '${REMOTE_GIT_DIR}' checkout --detach --force" in server_deploy
    assert "'${REMOTE_GIT_DIR}/smart-fund-server/' '${SERVER_DIR}/'" in server_deploy
    assert '"${LOCAL_SERVER_DIR}/"' not in server_deploy


def test_server_deployment_restarts_selected_processes_only() -> None:
    server_deploy = (
        WORKSPACE / "smart-fund-server" / "deployment" / "deploy_113.sh"
    ).read_text(encoding="utf-8")

    assert "--components" in server_deploy
    assert "deploy_components" in server_deploy
    assert "api|persist|scheduler|workers|ths-stream|kg" in server_deploy
    assert "smart-fund-worker-general" in server_deploy
    assert "smart-fund-worker-http.service smart-fund-worker-internal.service" in server_deploy
    assert "install -m 644 /tmp/${SVC_WORKER_THS_SECTOR}.service" in server_deploy
    assert "deploy_compose_services" in server_deploy
    assert "migrate_systemd_to_compose" in server_deploy
    assert "docker compose --project-name" in server_deploy
