from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]


def test_workspace_has_one_public_deployment_entrypoint() -> None:
    deploy = (WORKSPACE / "deploy.sh").read_text(encoding="utf-8")

    assert "--component" in deploy
    assert "--dry-run" in deploy
    assert "server-api|server-persist|server-scheduler" in deploy
    assert "git merge-base --is-ancestor" in deploy
    assert "rsync" not in deploy


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
