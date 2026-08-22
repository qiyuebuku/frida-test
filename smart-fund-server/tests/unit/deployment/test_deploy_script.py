from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = SERVER_ROOT / "deployment" / "deploy_113.sh"


def test_schema_migrations_run_as_local_postgres_owner() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    function = script.split("apply_schema_migrations() {", 1)[1].split(
        "\n}\n\ninstall_redis()", 1
    )[0]

    assert function.count("sudo_cmd") == 2
    assert function.count("sudo -u postgres psql -v ON_ERROR_STOP=1") == 2
    assert "-U \"\\${DB_USER:-postgres}\"" not in function
    assert "PGPASSWORD=" not in function


def test_schema_migration_temp_files_are_removed_on_failure() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    function = script.split("apply_schema_migrations() {", 1)[1].split(
        "\n}\n\ninstall_redis()", 1
    )[0]

    assert function.count("trap 'rm -f") == 2


def test_deployment_reconciles_containers_from_legacy_compose_project() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "remove_foreign_compose_containers()" in script
    assert 'project}" != "${COMPOSE_PROJECT}' in script
    assert 'remove_foreign_compose_containers "${services[@]}"' in script


def test_production_image_build_uses_local_base_cache() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "DOCKER_BUILDKIT=0 docker build --pull=false" in script
