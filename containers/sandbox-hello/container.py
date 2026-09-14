"""Generated stub -- the container is defined by container.toml.

Edit container.toml, not this file.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _loader import Container  # noqa: E402

c = Container.from_toml(__file__)
image, app = c.image, c.app


@app.function(**c.function_kwargs)
def run(command: str = "") -> str:
    return c.execute(command)


@app.local_entrypoint()
def main(command: str = ""):
    print(c.launch(run, command), end="")
