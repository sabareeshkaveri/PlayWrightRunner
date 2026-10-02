from __future__ import annotations

import argparse
from pathlib import Path

from .constants import DEFAULT_HOST, DEFAULT_PORT, PRODUCT_NAME, PROJECT_CONFIG_FILENAME
from .view import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Run {PRODUCT_NAME}, the local Playwright test runner.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    if not (project_root / PROJECT_CONFIG_FILENAME).is_file():
        nested_projects = sorted(
            child.resolve()
            for child in project_root.iterdir()
            if child.is_dir() and (child / PROJECT_CONFIG_FILENAME).is_file()
        ) if project_root.is_dir() else []
        message = f"No {PROJECT_CONFIG_FILENAME} found under {project_root}"
        if len(nested_projects) == 1:
            message += (
                f"\nUse --project-root {nested_projects[0]} "
                "to select the Playwright project."
            )
        elif nested_projects:
            options = "\n".join(f"  {path}" for path in nested_projects)
            message += f"\nPlaywright projects were found under:\n{options}"
        parser.error(message)

    server = create_server(project_root, args.host, args.port)
    print(f"{PRODUCT_NAME}: http://{args.host}:{args.port}")
    print(f"Project root: {project_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nStopping {PRODUCT_NAME}.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
