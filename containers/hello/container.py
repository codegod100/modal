"""Generated stub -- the container is defined by container.toml.

Edit container.toml, not this file.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _loader import Container  # noqa: E402

c = Container.from_toml(__file__)
image, app = c.image, c.app


# No @app.function here: a Sandbox runs its command as its own process and
# nothing of this module is imported into it. Registering a Function would be
# dead weight, and its kwargs are where vm_runtime would be wrongly applied.
@app.local_entrypoint()
def main(command: str = ""):
    c.run_sandbox(command)
