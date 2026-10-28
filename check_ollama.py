"""Check which model is available right now (Colab GPU or hosted API).

Run it any time to see what the agent would use::

    python check_ollama.py
"""

import sys

import config


def main(verbose: bool = True) -> int:
    """Report the available model.

    Args:
        verbose: If False, print nothing (the return code still tells the result).

    Returns:
        0 if a model is available, otherwise 1.
    """
    say = print if verbose else (lambda *a, **k: None)
    config.refresh()
    found = config.backend()
    if found is None:
        say(config.NO_MODEL_MESSAGE)
        return 1
    where = "the Colab GPU" if found.kind == "colab" else f"the {config.api_provider()} API"
    say(f"OK - {found.model} ready via {where}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
