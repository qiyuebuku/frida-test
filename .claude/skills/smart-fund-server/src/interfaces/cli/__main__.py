"""允许 `python -m src.interfaces.cli <cmd>` 运行 click group"""
from src.interfaces.cli.main import cli

if __name__ == "__main__":
    cli()
